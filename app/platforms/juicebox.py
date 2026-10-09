"""Juicebox (PeopleGPT) — a hand-written driver, not a YAML recipe.

Juicebox is the platform the recipe format cannot describe, so it takes the
escape hatch documented in docs/07-platform-recipes.md: a Python driver that
plugs into `RecipeAdapter` and reuses all of its session, login and
failure-artifact handling, overriding only the part that drives the page.

Why it needs a driver, re-established live on 2026-10-09 against the editor
Juicebox shipped on 2026-10-08 (docs/platforms/juicebox.md, "The sequence
editor (2026-10-08)"; the TinyMCE editor this driver was first written for is
gone):

- **New sequence → Build from scratch** creates and autosaves the sequence at
  once (`POST /api/sequence`), titled after the most recent project and dated.
  Every later edit is autosaved as a whole-sequence `PATCH /api/sequence`.
- The subject and every step's body are **Tiptap editors**, all mounted at
  once, with the Editor instance on the element (`element.editor`). Content
  goes in through `setContent` with `emitUpdate` on - without it the editor
  shows the text and the app never saves it, which is how the first probe
  lost its subject. `{{First Name}}` written as text becomes Juicebox's own
  token pill.
- Only step 1 has a subject; steps added with **Add step** are threaded
  follow-ups, scheduled two business days apart by default.
- The editor is the writer and the API is the reader: the app keeps setting
  its own defaults (sender, signature, schedule), and once the editor closes
  the sequence is read back through `GET /api/sequence` and compared with the
  document. A step that did not save fails the run with the sequence recorded,
  so a Delete can still take it down.
- Clicks that change the route hang Playwright (the app holds the document
  open), so navigation clicks pass `no_wait_after` and gotos wait for `commit`.

Saving a sequence contacts nobody; sending starts only when a recruiter adds
contacts and presses go. So the driver's output is a ready-to-review draft —
the same boundary noon draws.
"""

from __future__ import annotations

import html as html_lib
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import (
    Advert,
    NotionRow,
    ParsedDocument,
    PlatformError,
)
from app.platforms.engine import RunReport, _role_name
from app.utils.templating import juicebox_spacing, juicebox_tokens

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

from app.platforms.adapter import RecipeAdapter

log = get_logger(__name__)

# The editor's handles, read off the live page on 2026-10-09
# (artifacts/juicebox-editor-probe/). Step 1's subject; every step's body, in
# step order, all mounted at once.
_SUBJECT = "[data-step-subject-input]"
_BODY = '[aria-label="Message body"]'
_ADD_STEP = "button[aria-label='Add step']"
_VALIDATION = "[data-testid='sequence-validation-errors']"

# Write one Tiptap editor. `{emitUpdate: true}` is what makes the app save:
# Tiptap 3 reads it as the option, Tiptap 2 as a truthy `emitUpdate`. A bare
# setContent changes what is on screen and nothing the app persists.
_SET_EDITOR = """([selector, index, content]) => {
  const els = [...document.querySelectorAll(selector)];
  const el = els[index];
  if (!el) return {ok: false, why: 'not on the page', count: els.length};
  const ed = el.editor;
  if (!ed || !ed.commands) return {ok: false, why: 'no editor instance on it', count: els.length};
  ed.commands.setContent(content, {emitUpdate: true});
  return {ok: true, count: els.length, chars: ed.getText().length};
}"""

_EDITOR_AT = """([selector, index]) => {
  const el = document.querySelectorAll(selector)[index];
  return !!(el && el.editor && el.editor.commands);
}"""

_COUNT = "(selector) => document.querySelectorAll(selector).length"

# The title is a plain input holding the auto-title "<project> - <dd/mm/yyyy>";
# it is marked so a real click and real keystrokes can replace it.
_MARK_TITLE = """(title) => {
  const inputs = [...document.querySelectorAll('input')];
  const el = inputs.find(i => title && i.value === title)
          || inputs.find(i => / - \\d{2}\\/\\d{2}\\/\\d{4}$/.test(i.value || ''));
  if (!el) return null;
  el.setAttribute('data-tb-title', '1');
  return el.value;
}"""


class JuiceboxAdapter(RecipeAdapter):
    """Create a Juicebox email sequence from a document's email steps."""

    supports_delete = True

    async def _delete(
        self, page: "Page", records: list[dict[str, str]], *, row_title: str
    ) -> tuple[bool, list[str]]:
        """Delete each post's sequence, and its sourcing project when the run
        created one (juicebox_delete; the project is checked before it goes)."""
        from app.platforms.juicebox_delete import delete_records, sequence_id

        done, notes = True, []
        for record in records:
            sequence = record.get("sequence") or sequence_id(record.get("post_url", ""))
            project = record.get("project") or None
            if not sequence and not project:
                done = False
                notes.append(
                    "a post left no sequence id to delete by - delete it in Juicebox "
                    f"by hand ({record.get('post_url') or 'no link'})"
                )
                continue
            # A ledger written by hand - or by an older build - can hold a
            # stamp `fromisoformat` will not take. Unguarded it raised out of
            # this loop, so the records after it were never attempted and the
            # row read "the browser step failed", which names the wrong thing.
            # No stamp means the project's age cannot be checked, and
            # `delete_records` then refuses the project rather than guessing.
            created = _posted_at(record.get("project_created_at"))
            report = await delete_records(
                page,
                sequence=sequence,
                project=project,
                dry_run=self.dry_run,
                project_names=tuple(n for n in (record.get("project_name"), row_title) if n),
                posted_at=created,
            )
            notes.append(report.summary)
            # Every warning, live runs included. "already gone" is the one
            # correction a recruiter must see: the summary says "deleted", and
            # dropping the note left the row claiming this run deleted
            # something a person had already removed by hand.
            notes.extend(report.warnings)
            if not self.dry_run and not report.complete:
                done = False
        return done, notes

    async def _assert_logged_in(self, page: "Page") -> None:
        """Juicebox never fires `domcontentloaded`, so the base check hangs.

        Wait on `commit` and poll for the logged-in shell (the app paints blank
        for ~20-30s), rather than a fixed selector timeout against a page that
        has not rendered yet.
        """
        from app.models import AuthenticationRequired

        url = self.recipe.login.url or "https://app.juicebox.ai/"
        await page.goto(url, wait_until="commit", timeout=60_000)
        for _ in range(14):
            await page.wait_for_timeout(3_000)
            try:
                text = await page.evaluate("document.body ? document.body.innerText : ''")
                # The redesign of 2026-10-08 draws the sidebar icon-only: the
                # Sequences item is still there, as a link with an aria-label,
                # but the word is no longer on the page. Reading only the text
                # called a signed-in dashboard expired (artifact 20261008-192400).
                nav = await page.evaluate(
                    "!!document.querySelector(\"a[aria-label='Sequences']\")"
                )
            except Exception:
                continue
            if "Sequences" in text or nav:
                return
            if "Log in" in text and "Sequences" not in text:
                # A remembered-account / password screen. The session is gone.
                break
        raise AuthenticationRequired(
            f"{self.recipe.label} is not logged in (or the session has expired). "
            f"Run: python -m app.cli login {self.recipe.key}"
        )

    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        report = RunReport()
        emails = [e for e in sorted(document.emails, key=lambda e: e.order) if e.is_email]
        if not emails:
            raise PlatformError(
                "Juicebox posts an email sequence, but the document produced no "
                "email steps."
            )

        name = _sequence_name(document, row, emails)
        subject = juicebox_tokens(emails[0].subject).strip()
        bodies = [
            juicebox_spacing(juicebox_tokens(email.body_html or email.body_text))
            for email in emails
        ]

        tap = _ApiTap(page)
        tap.attach()
        try:
            await self._open_new_sequence_dialog(page)
            if self.dry_run:
                # "Build from scratch" saves the sequence the moment it is
                # clicked, so a dry run stops one click short of it.
                await self._click_button_or_text(page, "Close")
                report.skipped += 1
                report.warnings.append(
                    "dry run: stopped at Juicebox's New sequence dialog - 'Build "
                    "from scratch' creates and saves the sequence on the click."
                )
                return report

            sequence = await self._build_from_scratch(page, tap)
            report.records["sequence"] = sequence
            report.captures["post_url"] = _sequence_url(page.url) or page.url
            try:
                await self._write_sequence(page, tap, report, name, subject, bodies)
                juicebox_says = await self._close_editor(page)
                saved = await self._read_back(page, tap, sequence)
            except PlatformError as exc:
                raise _with_records(exc, report)
            except Exception as exc:  # noqa: BLE001 - the sequence exists; say so
                raise _with_records(
                    PlatformError(
                        f"Juicebox: the sequence was created but writing it failed - "
                        f"{exc.__class__.__name__}: {_short(exc)}"
                    ),
                    report,
                ) from exc

            problems = _saved_problems(saved, name, subject, bodies)
            if problems:
                raise _with_records(
                    PlatformError(
                        "Juicebox created the sequence but it did not save as written: "
                        + "; ".join(problems)
                        + (f". Juicebox says: {juicebox_says}" if juicebox_says else "")
                        + f". Open it and finish it by hand, or set the row to Delete: "
                        f"{report.post_url}"
                    ),
                    report,
                )
        finally:
            tap.detach()

        report.submitted = True
        log.info(
            "juicebox sequence saved",
            extra={"sequence": name, "steps": report.emails_written, "url": report.post_url},
        )

        if self.settings.criteria_enabled:
            # Sourcing first: it builds the search, and the criteria step then
            # has a search to rank on even when the row names none. Until
            # 2026-09-03 the order was the other way round, and every row
            # without a `Juicebox Search` column reported the criteria skipped.
            search_url = await self._set_up_sourcing(page, document, row, report)
            await self._set_criteria(page, document, row, report, built_search=search_url)
        return report

    async def _set_up_sourcing(
        self,
        page: "Page",
        document: ParsedDocument,
        row: NotionRow | None,
        report: RunReport,
    ) -> str | None:
        """A sourcing project for the role: JD search plus real filters.

        Returns the URL of the search it built, for the criteria step; None
        when it skipped, failed, or ran dry.

        This is the half Sohaib found missing entirely on 2026-09-01 ("not even
        20 percent configured"): a project, the JD pasted so Juicebox's AI
        builds the search, then the filters its AI leaves thin - titles,
        location, skills, years. Same containment as everywhere else: a
        sourcing failure is a warning on a run whose sequence already saved.
        It now also leaves a screenshot behind, because the first production
        failure (2026-09-02) left one line of text and an empty project.
        """
        from app.pipeline import _row_text
        from app.platforms.browser import save_failure
        from app.platforms.juicebox_sourcing import (
            set_up_sourcing,
            stage_for_filter,
            stage_plan,
            years_span,
        )
        from app.platforms.sourcing_profile import ensure_sourcing
        from app.platforms.targeting_ai import sourcing_location

        # An existing project when the row names one - a recruiter made it, or
        # an earlier run created it and stopped short of the search. Creating
        # a second one beside it is the duplicate nobody asked for, and a value
        # that is not a URL is a question for the recruiter, not a guess.
        project_url: str | None = None
        if row is not None:
            column = self.settings.prop_juicebox_project
            value = (_row_text(row, column) or "").strip()
            if value and not value.startswith("http"):
                report.warnings.append(
                    f"sourcing skipped: {column!r} should hold the project's "
                    f"full URL, not {value!r}"
                )
                return
            project_url = value or None

        advert = document.advert or Advert(title="", body_text="", body_html="")
        name = _role_name(
            document.source_name, row, advert,
            sorted(document.emails, key=lambda e: e.order),
        )
        # The shared profile (D-024): the same titles, skills, years, location
        # and companies every other sourcing platform reads, drafted once and
        # saved. Before it, each platform drafted its own and the searches
        # disagreed about one job. The row's location is the region fallback
        # for the company list, for a JD that names no place.
        profile = await ensure_sourcing(
            document,
            self.settings,
            role_title=name,
            location=(
                (_row_text(row, self.settings.prop_location) if row is not None else None)
                or advert.location
                or ""
            ),
        )
        if profile is None or profile.is_empty:
            report.warnings.append(
                "sourcing skipped: no titles or skills could be drafted "
                "(is ANTHROPIC_API_KEY set here?)"
            )
            return
        report.warnings.append(f"sourcing profile: {profile.summary}")
        # Both tiers in one list, essentials first: Juicebox has one Skills box
        # and the cap cuts from the nice-to-have end.
        skills = profile.skills[: self.settings.sourcing_max_skills]
        # Where the CANDIDATE must be, as the Client JD states it. The row's
        # `Location` and the advert's are the job's location as the posting
        # gives it, which on Axle was where the company sits and not where the
        # hire had to be (Sohaib's review, 2026-09-07) - they only fill the gap.
        location = sourcing_location(
            profile.candidate_location,
            _row_text(row, self.settings.prop_location) if row is not None else None,
            advert.location,
        ) or None

        # Same-stage companies, and the stages themselves - every stage from
        # Seed up to the client's own (Sohaib's rule, 2026-09-02; D-020 for the
        # companies). The stage is read off the document when it states one and
        # inferred by Claude when it does not. An inferred stage draws the
        # Companies list and is said so on the row; it does not set Company
        # Funding Stages (D-022), which stays as Juicebox's own AI left it.
        company = (document.source_name or "").split(" - ")[0].strip()
        stated = profile.stage if profile.stage_stated else None
        stage = profile.stage or None
        selected = stage_for_filter(stage, stated=profile.stage_stated)
        if stage and not stated:
            report.warnings.append(
                f"the document does not state {company or 'the client'}'s funding "
                f"stage; Claude inferred {stage} and drew the Companies filter "
                "from it - check the search's Companies list. Company Funding "
                "Stages was left as Juicebox set it, not narrowed to a guess; "
                f"write the stage into the Client JD (\"Stage: {stage}\") to "
                "have that filter set too"
            )
        elif not stage:
            report.warnings.append(
                "no funding stage could be read or inferred, so Company Funding "
                "Stages was left as Juicebox set it"
            )
        if not profile.companies:
            report.warnings.append(
                "no target companies could be drafted, so the Companies filter "
                "was left empty"
            )

        if self.dry_run:
            where = f"search in {project_url}" if project_url else "create a sourcing project"
            report.warnings.append(
                f"dry run: would {where} with "
                f"{len(profile.similar_titles)} title(s), "
                f"{len(skills)} skill(s)"
                + (f", location {location}" if location else "")
                + (f", {span}" if (span := years_span(profile.min_years, profile.max_years)) else "")
                + f", {len(profile.companies)} company(ies) at {stage or 'an unknown stage'}"
                + (f", stages {'/'.join(stage_plan(selected))}" if stage_plan(selected)
                   else ", stages left as Juicebox set them")
            )
            return

        try:
            result = await set_up_sourcing(
                page,
                project_name=name,
                project_url=project_url,
                # The Client JD verbatim, else the profile's composed spec -
                # never the raw advert while a spec exists. Juicebox's own AI
                # builds the search from this paste, and on 2026-09-28 a search
                # was found built from the marketing copy.
                jd=document.search_jd,
                titles=profile.similar_titles,
                skills=skills,
                location=location,
                min_years=profile.min_years,
                max_years=profile.max_years,
                companies=profile.companies,
                stage=selected,
            )
        except Exception as exc:  # noqa: BLE001 - a sourcing failure is a warning,
            # never a lost run whose sequence already saved.
            log.warning("juicebox sourcing not set up", extra={"error": str(exc)[:200]})
            saved = await save_failure(
                page.context, page, "juicebox-sourcing-failed", self.settings
            )
            report.warnings.append(
                f"sourcing not set up: {exc}"
                + (f" (screenshot: {saved[0]})" if saved else "")
            )
            return
        report.warnings.extend(
            f"sourcing: {section} refused {', '.join(values)}"
            for section, values in result.refused.items() if values
        )
        report.warnings.append(
            f"sourcing project: {result.project_url}"
            + (" (created)" if result.project_created else " (existing)")
        )
        # Only a project this run made is ever deleted with the row; an
        # existing one is a recruiter's. The name and time are what the delete
        # checks the project against before trusting the id (juicebox_delete).
        if result.project_created:
            from datetime import datetime, timezone

            from app.platforms.juicebox_delete import project_id
            from app.platforms.juicebox_sourcing import project_title

            if project_id(result.project_url):
                report.records["project"] = project_id(result.project_url) or ""
                report.records["project_name"] = project_title(name)
                report.records["project_created_at"] = datetime.now(timezone.utc).isoformat()
        report.warnings.append(
            f"sourcing search: {result.search_url} ({result.summary})"
        )
        return result.search_url or None

    async def _set_criteria(
        self,
        page: "Page",
        document: ParsedDocument,
        row: NotionRow | None,
        report: RunReport,
        built_search: str | None = None,
    ) -> None:
        """Rank the criteria of the search this role belongs to.

        The row's `Juicebox Search` column names it when a recruiter made the
        search by hand. Otherwise the search this very run just built is the
        one - it is the role's own, so ranking its criteria is safe. Only when
        neither exists is the stage skipped and said so: writing criteria onto
        a guessed search would quietly re-score another client's candidates.
        """
        from app.platforms.browser import save_failure
        from app.platforms.juicebox_criteria import set_criteria

        search = None
        if row is not None:
            from app.pipeline import _row_text

            search = (_row_text(row, self.settings.prop_juicebox_search) or "").strip()
        if search and not search.startswith("http"):
            report.warnings.append(
                f"search criteria skipped: {self.settings.prop_juicebox_search!r} "
                "should hold the search's full URL"
            )
            return
        search = search or built_search
        if not search:
            report.warnings.append(
                "search criteria skipped: no search to rank - the row names none "
                f"in {self.settings.prop_juicebox_search!r} and none was built"
            )
            return

        # The document's Client JD, else the profile's composed spec, else the
        # advert; the search's own job description is the last resort, inside
        # `set_criteria`. Same rule on all three platforms, so the three
        # criteria sets agree.
        try:
            result = await set_criteria(
                page,
                search,
                document.search_jd,
                role_name=document.source_name,
                settings=self.settings,
                dry_run=self.dry_run,
            )
        except PlatformError as exc:
            log.warning("juicebox criteria not set", extra={"error": str(exc)[:200]})
            shot: list[str] = []
            try:
                shot = await save_failure(
                    page.context, page, "juicebox-criteria-failed", self.settings
                )
            except Exception:  # noqa: BLE001 - the screenshot is evidence, not
                # the job: a browser already in trouble must not turn a contained
                # warning into a failed row.
                log.debug("juicebox criteria screenshot failed", exc_info=True)
            # First in the list: the adapter joins these into one `detail`, and
            # on 2026-09-22 this arrived as a clause inside "Posted OK. Notes:"
            # for a search that ranked nobody. The run itself stays a success -
            # the sequence saved, and failing the row would invite a re-post.
            report.warnings.insert(
                0,
                "SEARCH CRITERIA NOT WRITTEN - this search still ranks nobody by "
                f"the role's requirements. {exc} Search: {search}"
                + (f" (screenshot: {shot[0]})" if shot else ""),
            )
            return

        report.warnings.extend(result.warnings)
        report.warnings.append(f"search criteria: {result.summary}")

    # -- page interactions -------------------------------------------------

    async def _open_new_sequence_dialog(self, page: "Page") -> None:
        """Sequences, New sequence, and the dialog with 'Build from scratch'.

        Retried freely: nothing up to here creates anything. The list paints
        late, and the first 'New sequence' click can land before it exists.
        """
        sequences_url = self.recipe.defaults.get("sequences_url")
        attempts = 3
        last = ""
        for attempt in range(attempts):
            try:
                await self._go_to_sequence_list(page, sequences_url, first=attempt == 0)
                await self._wait_for_text(page, "New sequence", seconds=45)
                await self._click_button_or_text(page, "New sequence")
                await self._wait_for_text(page, "Build from scratch", seconds=20)
                return
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last = _short(exc)
                log.warning(
                    "juicebox new-sequence dialog did not open; retrying",
                    extra={"attempt": attempt + 1, "of": attempts, "error": last},
                )
        raise PlatformError(
            f"could not open Juicebox's New sequence dialog after {attempts} attempts ({last})"
        )

    async def _build_from_scratch(self, page: "Page", tap: "_ApiTap") -> str:
        """Click 'Build from scratch' once and return the sequence it created.

        Never retried: the click creates and saves the sequence, so a second
        click is a second sequence. The id comes off the app's own
        `POST /api/sequence` response, with the editor URL as the fallback.
        """
        from app.platforms.juicebox_delete import sequence_id

        created: dict = {}
        try:
            async with page.expect_response(_is_create, timeout=60_000) as response_info:
                await self._click_button_or_text(page, "Build from scratch")
            response = await response_info.value
            created = ((await response.json()) or {}).get("result") or {}
        except Exception as exc:  # noqa: BLE001 - the URL may still name it
            log.warning("juicebox create response not read", extra={"error": _short(exc)})
        sequence = str(created.get("id") or "") or None
        tap.created_title = str(created.get("title") or "")

        # The editor mounts some seconds after the create returns.
        for _ in range(60):
            if not sequence:
                sequence = sequence_id(page.url)
            if sequence and await page.evaluate(_EDITOR_AT, [_SUBJECT, 0]):
                break
            await page.wait_for_timeout(1_000)
        if not sequence:
            raise PlatformError(
                "'Build from scratch' did not create a sequence Juicebox would name, so "
                "nothing was written. Check Juicebox's Sequences list for a stray draft "
                "dated today and delete it."
            )
        if not await page.evaluate(_EDITOR_AT, [_SUBJECT, 0]):
            raise _with_records(
                PlatformError(
                    "Juicebox created the sequence but its editor never opened, so nothing "
                    "was written into it."
                ),
                {"sequence": sequence, "post_url": _sequence_url(page.url) or page.url},
            )
        log.info("juicebox sequence created", extra={"sequence": sequence,
                                                     "auto_title": tap.created_title})
        return sequence

    async def _write_sequence(
        self,
        page: "Page",
        tap: "_ApiTap",
        report: RunReport,
        name: str,
        subject: str,
        bodies: list[str],
    ) -> None:
        """Title, subject, then each step: step 1 exists, steps 2..N are added."""
        await self._rename(page, name, tap.created_title)
        if subject:
            await self._set_editor(page, _SUBJECT, 0, subject, "the subject")
        for index, body in enumerate(bodies):
            if index > 0:
                await self._add_step(page, index + 1)
            result = await self._set_editor(page, _BODY, index, body, f"step {index + 1}'s body")
            report.emails_written += 1
            report.executed += 1
            log.info(
                "juicebox step written",
                extra={"step": index + 1, "chars": result.get("chars"), "bodies": result.get("count")},
            )
        await self._settle(page, tap)

    async def _rename(self, page: "Page", name: str, auto_title: str) -> None:
        """Replace the auto-title with real keystrokes, as a person would."""
        found = await page.evaluate(_MARK_TITLE, auto_title)
        if found is None:
            raise PlatformError("the sequence's title box was not found in Juicebox's editor")
        box = page.locator("input[data-tb-title='1']").first
        await box.click(timeout=8_000)
        await page.keyboard.press("Control+A")
        await page.keyboard.type(name, delay=10)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(800)

    async def _set_editor(
        self, page: "Page", selector: str, index: int, content: str, what: str
    ) -> dict:
        """Write one Tiptap editor, waiting for it to mount first."""
        for _ in range(20):
            if await page.evaluate(_EDITOR_AT, [selector, index]):
                break
            await page.wait_for_timeout(750)
        result = await page.evaluate(_SET_EDITOR, [selector, index, content])
        if not result.get("ok"):
            raise PlatformError(f"could not write {what} into Juicebox's editor: {result.get('why')}")
        await page.wait_for_timeout(600)
        return result

    async def _add_step(self, page: "Page", wanted: int) -> None:
        """Add step, until `wanted` bodies are on the page. A step-type menu,
        when one opens instead, is answered with Email."""
        try:
            await page.locator(_ADD_STEP).first.click(timeout=8_000, no_wait_after=True)
        except Exception as exc:
            raise PlatformError(f"could not click 'Add step': {_short(exc)}") from exc
        if await self._wait_for_count(page, _BODY, wanted, seconds=12):
            return
        for choose in (
            page.get_by_role("menuitem", name="Email", exact=True),
            page.get_by_role("option", name="Email", exact=True),
        ):
            try:
                await choose.first.click(timeout=3_000)
                break
            except Exception:
                continue
        if not await self._wait_for_count(page, _BODY, wanted, seconds=12):
            have = await page.evaluate(_COUNT, _BODY)
            raise PlatformError(f"adding step {wanted} failed: the editor shows {have} step(s)")

    async def _settle(self, page: "Page", tap: "_ApiTap") -> None:
        """Wait for the autosave that follows the last edit to come back.

        Juicebox saves the whole sequence a moment after each change. Closing
        before the last save lands is how a step goes missing, so this waits
        out the save delay, then for every save sent to be answered and the
        saving to stop. The read-back after Done is the real check; this only
        keeps it from finding a save still on the wire.
        """
        started = tap.clock()
        for _ in range(40):
            await page.wait_for_timeout(500)
            if tap.clock() - started >= 2.5 and not tap.in_flight() and tap.quiet_for(1.5):
                return
        log.warning(
            "juicebox autosave not confirmed before closing",
            extra={"sent": tap.sent, "answered": tap.answered},
        )

    async def _close_editor(self, page: "Page") -> str:
        """Done. Returns Juicebox's own complaint when it closed with errors."""
        complaint = ""
        try:
            panel = page.locator(_VALIDATION)
            if await panel.count():
                complaint = " ".join((await panel.first.inner_text()).split())[:300]
        except Exception:
            pass
        await self._click_button_or_text(page, "Done")
        await page.wait_for_timeout(2_000)
        # "Close with errors?" - the sequence is saved either way; closing
        # lets the read-back say exactly what is wrong.
        if await page.get_by_text("Close anyway", exact=True).count():
            await self._click_button_or_text(page, "Close anyway")
            await page.wait_for_timeout(1_500)
            complaint = complaint or "it closed with errors"
        return complaint

    async def _read_back(self, page: "Page", tap: "_ApiTap", sequence: str) -> dict:
        """The sequence as Juicebox stored it, through its own API."""
        from app.platforms.juicebox_delete import JuiceboxSession

        if not tap.token:
            raise PlatformError("Juicebox's session token was not seen, so the sequence could not be read back")
        data = await JuiceboxSession(page=page, token=tap.token).call(
            "GET", f"/api/sequence?sequenceId={sequence}"
        )
        result = (data or {}).get("result") if isinstance(data, dict) else None
        items = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("id") == sequence:
                return item
        raise PlatformError(f"Juicebox did not return sequence {sequence} when asked for it back")

    async def _wait_for_text(self, page: "Page", text: str, *, seconds: int) -> None:
        for _ in range(seconds):
            try:
                if await page.get_by_text(text, exact=True).count():
                    return
            except Exception:
                pass
            await page.wait_for_timeout(1_000)
        raise PlatformError(f"{text!r} never appeared")

    async def _wait_for_count(self, page: "Page", selector: str, wanted: int, *, seconds: int) -> bool:
        for _ in range(seconds * 2):
            if await page.evaluate(_COUNT, selector) >= wanted:
                return True
            await page.wait_for_timeout(500)
        return False

    async def _go_to_sequence_list(
        self, page: "Page", sequences_url: str | None, first: bool
    ) -> None:
        """Land on the sequence list, closing any open editor modal first.

        On a retry the previous 'Start from scratch' left a modal open, and a
        second `goto` to the same SPA URL never commits — so dismiss the modal
        and use the in-app 'Sequences' nav instead of re-navigating.
        """
        if not first:
            for label in ("Cancel", "Close"):
                try:
                    await page.get_by_role("button", name=label, exact=True).first.click(
                        timeout=2_500, no_wait_after=True
                    )
                except Exception:
                    pass
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(1_500)

        if first and sequences_url:
            try:
                await page.goto(
                    str(sequences_url), wait_until="commit", timeout=45_000
                )
                return
            except Exception as exc:
                log.warning(
                    "juicebox sequences goto slow; using nav instead",
                    extra={"error": _short(exc)},
                )
        # The icon-only sidebar (2026-10-08) has no "Sequences" text to click;
        # its link still carries the aria-label. The text click stays as the
        # fallback for the older layout.
        try:
            await page.locator("a[aria-label='Sequences']").first.click(
                timeout=8_000, no_wait_after=True
            )
            return
        except Exception:
            pass
        await self._click_text(page, "Sequences")

    async def _click_text(self, page: "Page", text: str) -> None:
        try:
            await page.get_by_text(text, exact=True).first.click(
                timeout=12_000, no_wait_after=True
            )
        except Exception as exc:
            raise PlatformError(f"could not click {text!r}: {_short(exc)}") from exc

    async def _click_button_or_text(self, page: "Page", label: str) -> None:
        """Click a control by button role, then text, forcing past overlays.

        The list and modal paint erratically; a control can be present but not
        yet 'stable' for a plain click, and a promo/consent banner sometimes
        sits over it. Forcing the click clears both.
        """
        for locator in (
            page.get_by_role("button", name=label, exact=True).first,
            page.get_by_text(label, exact=True).first,
        ):
            try:
                await locator.wait_for(state="visible", timeout=8_000)
                await locator.click(timeout=8_000, no_wait_after=True, force=True)
                return
            except Exception:
                continue
        raise PlatformError(f"could not click {label!r}")


class _ApiTap:
    """The app's own traffic, read while the driver works.

    Two things come off it: the Firebase ID token every `/api/` call carries
    (the read-back needs it; it is minted in the page and never in a cookie),
    and the autosaves - each edit is saved as a whole-sequence
    `PATCH /api/sequence`, and the driver closes the editor only after the
    save that follows its last edit has come back.
    """

    def __init__(self, page: "Page") -> None:
        import time

        self.clock = time.monotonic
        self.page = page
        self.token: str | None = None
        self.created_title = ""
        self.sent = 0  # autosave requests sent
        self.answered = 0  # ... and answered, or failed
        self._last_activity = 0.0

    def attach(self) -> None:
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_failed)

    def detach(self) -> None:
        for event, handler in (
            ("request", self._on_request),
            ("response", self._on_response),
            ("requestfailed", self._on_failed),
        ):
            try:
                self.page.remove_listener(event, handler)
            except Exception:
                pass

    def _on_request(self, request: Any) -> None:
        if "/api/" not in request.url:
            return
        try:
            token = request.headers.get("fbauthorization")
        except Exception:
            token = None
        if token:
            self.token = token  # the latest: ID tokens are re-minted hourly
        if _is_autosave(request):
            self.sent += 1
            self._last_activity = self.clock()

    def _on_response(self, response: Any) -> None:
        if _is_autosave(response.request):
            self.answered += 1
            self._last_activity = self.clock()

    def _on_failed(self, request: Any) -> None:
        if _is_autosave(request):
            self.answered += 1
            self._last_activity = self.clock()

    def in_flight(self) -> int:
        return max(0, self.sent - self.answered)

    def quiet_for(self, seconds: float) -> bool:
        """No autosave sent or answered for `seconds` (true before the first)."""
        return self.clock() - self._last_activity >= seconds


def _path(url: str) -> str:
    return re.sub(r"^https?://[^/]+", "", (url or "").split("?", 1)[0]).rstrip("/")


def _is_create(response: Any) -> bool:
    return response.request.method == "POST" and _path(response.url) == "/api/sequence"


def _is_autosave(request: Any) -> bool:
    return request.method == "PATCH" and _path(request.url) == "/api/sequence"


def _with_records(exc: Exception, records: Any) -> Exception:
    """Attach what was created, so post_document can ledger a failed post.

    `records` is a RunReport (its records plus the post URL) or a plain dict.
    """
    if isinstance(records, RunReport):
        held = dict(records.records)
        if records.post_url:
            held.setdefault("post_url", records.post_url)
    else:
        held = dict(records or {})
    exc.records = held  # type: ignore[attr-defined]
    return exc


def _plain_words(markup: str) -> list[str]:
    """Words of an HTML or text body, tags and entities gone, case folded.

    Tokens survive as words (`{{first`, `name}}`), so a lost token reads as a
    lost word.
    """
    text = re.sub(r"<[^>]+>", " ", markup or "")
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.findall(r"[\w{}'’.,!?@$%&-]+", text.lower())


def _coverage(saved: str, expected: str) -> float:
    """The share of the expected words that the saved text holds."""
    want = Counter(_plain_words(expected))
    if not want:
        return 1.0
    have = Counter(_plain_words(saved))
    return sum(min(count, have[word]) for word, count in want.items()) / sum(want.values())


def _saved_problems(saved: dict, name: str, subject: str, bodies: list[str]) -> list[str]:
    """Compare the stored sequence with what the document asked for.

    Words, not markup: the editor rewrites HTML (empty paragraphs become
    `<p><br></p>`, tokens become pills and back), so only lost words count.
    """
    problems: list[str] = []
    title = str(saved.get("title") or "").strip()
    if title != name.strip():
        problems.append(f"the title reads {title!r}, not {name.strip()!r}")
    steps = [s for s in saved.get("steps") or [] if isinstance(s, dict)]
    emails = [s for s in steps if (s.get("type") or "email") == "email"]
    if len(emails) != len(bodies):
        problems.append(f"{len(emails)} email step(s) saved, the document has {len(bodies)}")
    if subject:
        stored = str((emails[0].get("subject") if emails else "") or saved.get("subject") or "")
        if not stored.strip():
            problems.append("the subject did not save")
        elif _coverage(stored, subject) < 0.9:
            problems.append(f"the subject saved as {stored!r}")
    for number, (step, body) in enumerate(zip(emails, bodies), start=1):
        share = _coverage(str(step.get("body") or ""), body)
        if share == 0:
            problems.append(f"step {number}'s body saved empty")
        elif share < 0.9:
            problems.append(f"step {number}'s body saved incomplete ({share:.0%} of its words)")
    return problems


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _posted_at(value: str | None):
    """A ledger's `project_created_at` as a datetime, or None when it is not one.

    The project delete uses this to check the project is the one this run made.
    A value it cannot read is treated as no value: `delete_records` then refuses
    to delete the project rather than trusting the id alone, which is the safe
    direction - on 2026-09-23 a captured id turned out to be a live client's.
    """
    from datetime import datetime

    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        log.warning("ledger holds an unreadable created stamp", extra={"value": text[:40]})
        return None


def _sequence_name(
    document: ParsedDocument, row: NotionRow | None, emails: list[Any]
) -> str:
    """A human name for the sequence, matching every other platform.

    The recruiters' .docx filename is the convention "Company - Role - Location"
    and is the source of truth, so it wins. Then the Notion row title, then a
    real advert title (unless the parser fell back to the email opener, which
    starts with a greeting or a token), and last the shared subject line.
    """
    if (document.source_name or "").strip():
        return document.source_name.strip()

    if row is not None and (row.title or "").strip():
        return row.title.strip()

    advert = document.advert or Advert(title="", body_text="", body_html="")
    title = (advert.title or "").strip()
    looks_like_greeting = title.lower().startswith(("hi ", "hello", "hey", "dear")) or (
        "{" in title
    )
    if title and not looks_like_greeting:
        return title

    subject = juicebox_tokens(emails[0].subject).strip()
    return subject or "New sequence"


def _sequence_url(url: str) -> str:
    """Prefer a clean link to the created sequence over the editor URL."""
    match = re.search(r"createdSequenceId=([A-Za-z0-9]+)", url or "")
    if match:
        base = url.split("/sequences", 1)[0]
        return f"{base}/sequences/{match.group(1)}"
    return url or ""


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0][:120] if text else exc.__class__.__name__
