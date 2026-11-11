"""Wellfound — the YAML advert recipe, plus the delete and update it cannot express.

`platforms/wellfound.yaml` still owns the posting: the New Job Posting form,
filled and saved as a draft. This driver runs that recipe unchanged, keeps
what the run created for the ledger (the job's URL and the title it was
posted under), adds the delete - Wellfound's own `DestroyJobListing`,
sent from inside the tab ([wellfound_delete](wellfound_delete.py)) - and the
update: the listing the row already made, opened on its edit form and the
document's fields written over it (`Ready to Update`, D-031, 2026-11-05).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.config import Settings
from app.logging_conf import get_logger
from app.models import NotionRow, ParsedDocument, PlatformError
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RecipeEngine, RunReport, _StopRun, build_context
from app.platforms.recipe import Step

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://wellfound.com"
_JOB_URL = re.compile(r"wellfound\.com/recruit/jobs/(\d+)")


def find_listing(row: NotionRow | None, settings: Settings) -> tuple[str, str] | None:
    """The listing an update rewrites: `(job id, how it was found)`.

    The ledger first - the newest record of a post this row made, unless a
    delete has since cleared the platform - then a Wellfound job link in the
    row's `Post URL` column. Nothing else: the Jobs list has never been read
    by the automation, and guessing a listing by title on an account with
    fifty drafts would edit the wrong one.
    """
    if row is None:
        return None
    from app.ledger import Ledger
    from app.pipeline import _row_text

    entry = Ledger(settings.ledger_file).get(row.page_id)
    if entry and "wellfound" not in entry.deleted:
        for record in reversed(entry.records.get("wellfound", [])):
            match = _JOB_URL.search(record.get("job") or record.get("post_url") or "")
            if match:
                return match.group(1), "the listing this row's post made"
    text = _row_text(row, settings.prop_post_url) or ""
    match = _JOB_URL.search(text)
    if match:
        return match.group(1), f"the {settings.prop_post_url} column"
    return None


def recorded_ids(row: NotionRow | None, settings: Settings) -> dict[str, str]:
    """The pitch template and saved search this row's newest post recorded,
    for an update to rewrite rather than duplicate. Empty when the ledger
    has none (a post from before 2026-11-12, or the half was off)."""
    if row is None:
        return {}
    from app.ledger import Ledger

    entry = Ledger(settings.ledger_file).get(row.page_id)
    if not entry or "wellfound" in entry.deleted:
        return {}
    found: dict[str, str] = {}
    for record in reversed(entry.records.get("wellfound", [])):
        for key in ("pitch_template", "saved_search"):
            if record.get(key) and key not in found:
                found[key] = str(record[key])
    return found


def update_steps(steps: list[Step], job_id: str) -> list[Step]:
    """The posting steps, re-aimed at an existing listing's edit form.

    The first `goto_until` opens `/recruit/jobs/<id>/edit` instead of the
    `/new` form; a step marked `on_update: skip` in the recipe is left out;
    the submit step clicks its `update_selector` when it names one. Text
    fields and single-value selects replace what is there, which is what
    makes the rest of the recipe reusable as written.
    """
    rewritten: list[Step] = []
    aimed = False
    for step in steps:
        if str(step.params.get("on_update") or "").lower() == "skip":
            continue
        params = dict(step.params)
        if not aimed and step.action in ("goto", "goto_until"):
            params["url"] = f"{{{{ base_url }}}}/recruit/jobs/{job_id}/edit"
            params["description"] = "the listing's edit form"
            aimed = True
        if step.submit and params.get("update_selector"):
            params["selector"] = params["update_selector"]
        params.pop("update_selector", None)
        rewritten.append(Step(action=step.action, params=params, index=step.index, phase=step.phase))
    return rewritten


class WellfoundAdapter(RecipeAdapter):
    supports_delete = True
    supports_update = True

    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        report = await engine.run(document, row)
        # Every run makes a new listing, so the URL alone says what to delete;
        # the title lets the delete read the page back and refuse an id that
        # no longer points at what was posted.
        if report.post_url:
            report.records.setdefault("job", report.post_url)
            advert = document.advert_for(self.recipe.key)
            if advert is not None and advert.title.strip():
                report.records.setdefault("job_title", advert.title.strip()[:150])
        await self._pitch_and_search(page, document, row, report)
        return report

    async def _pitch_and_search(
        self,
        page: "Page",
        document: ParsedDocument,
        row: NotionRow | None,
        report: RunReport,
        *,
        template_id: str = "",
        search_id: str = "",
    ) -> None:
        """The recruiter half of a Wellfound role: its pitch template and its
        saved search (2026-11-12).

        The advert is what candidates find; these are what a recruiter uses
        to go and find candidates - the note they pitch with
        ([wellfound_pitch](wellfound_pitch.py)) and the filtered search they
        pitch from ([wellfound_search](wellfound_search.py)). Neither
        contacts anyone. `WELLFOUND_PITCH_ENABLED` / `WELLFOUND_SEARCH_ENABLED`
        switch each off.

        Same containment as every other sourcing half: the advert is already
        saved, so a failure here is a warning on the row and a screenshot,
        never a failed post.
        """
        settings = self.settings
        want_pitch = bool(settings.wellfound_pitch_enabled)
        want_search = bool(settings.wellfound_search_enabled and settings.criteria_enabled)
        if not (want_pitch or want_search):
            return
        from app.platforms.browser import save_failure
        from app.platforms.wellfound_api import WellfoundTap

        advert = document.advert_for(self.recipe.key) or document.advert
        title = (advert.title.strip() if advert is not None and advert.title else "")[:150]
        tap = None
        try:
            tap = WellfoundTap(page)
            if want_pitch:
                template_id = await self._pitch(page, tap, document, report, title, template_id)
            if want_search:
                await self._search(page, tap, document, row, report, title, search_id, template_id)
        except Exception as exc:  # contained: the advert is saved either way
            if want_search and report.sourcing is None:
                report.sourcing = "failed"
            first_line = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0][:300]
            report.warnings.append(f"pitch/search stopped: {first_line}")
            log.warning("wellfound pitch/search failed", extra={"error": first_line})
            try:
                await save_failure(page.context, page, "wellfound-sourcing-failed", settings)
            except Exception:
                pass
        finally:
            if tap is not None:
                tap.detach()

    async def _pitch(self, page, tap, document, report, title: str, template_id: str) -> str:
        from app.platforms.wellfound_pitch import pitch_from, save_pitch

        pitch = pitch_from(document, title=title)
        if pitch is None:
            report.warnings.append(
                "pitch: the document has no InMail, LinkedIn or email message to make a "
                "Wellfound pitch template from - none was created"
            )
            return template_id
        result = await save_pitch(page, tap, pitch, dry_run=self.dry_run, template_id=template_id)
        report.warnings.extend(result.warnings)
        report.warnings.append(f"{result.summary}, from the document's {pitch.source}")
        if result.template_id and not self.dry_run:
            report.records["pitch_template"] = result.template_id
            report.records["pitch_title"] = result.title
        return result.template_id or template_id

    async def _search(
        self, page, tap, document, row, report, title: str, search_id: str, template_id: str
    ) -> None:
        from app.pipeline import _row_text
        from app.platforms.juicebox_sourcing import split_locations
        from app.platforms.sourcing_profile import ensure_sourcing
        from app.platforms.targeting_ai import sourcing_location
        from app.platforms.wellfound_search import SearchPlan, function_for, set_up_search

        advert = document.advert_for(self.recipe.key) or document.advert
        row_location = _row_text(row, self.settings.prop_location) if row is not None else None
        advert_location = advert.location if advert is not None else None
        profile = await ensure_sourcing(
            document, self.settings, role_title=title, location=row_location or advert_location or ""
        )
        if profile is None or profile.is_empty:
            report.sourcing = "skipped"
            report.warnings.append(
                "search skipped: no skills could be drafted for it (is ANTHROPIC_API_KEY set here?)"
            )
            return
        if not title:
            report.sourcing = "skipped"
            report.warnings.append("search skipped: the advert has no title to name it by")
            return
        places = split_locations(sourcing_location(profile.candidate_location, row_location, advert_location))
        # The recipe's own shorthand table (NY -> New York City): Wellfound's
        # location list is the same one the job form uses.
        shorthand = next(
            (s.params.get("map") or {} for s in self.recipe.steps
             if s.action == "combobox" and "location" in str(s.params.get("description") or "")),
            {},
        )
        location = places[0] if places else ""
        function = function_for(title)
        category = (advert.category or "").strip() if advert is not None else ""
        plan = SearchPlan(
            title=title,
            function=function,
            # Wellfound's role list is its own; the recipe's default is the
            # one engineering role known to be on it.
            role=category or (str(self.recipe.defaults.get("default_role") or "") if function == "Engineering" else ""),
            location=str(shorthand.get(location, location)),
            # Every skill on a Wellfound search is required, so each one
            # narrows it: the essentials only, and few of them kept.
            skills=list(profile.must_have_skills),
            max_skills=self.settings.wellfound_search_max_skills,
            min_years=profile.min_years,
        )
        result = await set_up_search(
            page, tap, plan, dry_run=self.dry_run, search_id=search_id, template_id=template_id
        )
        report.warnings.extend(result.warnings)
        report.warnings.append(result.summary)
        report.sourcing = "set" if not self.dry_run else report.sourcing
        if result.url and not self.dry_run:
            report.records["saved_search"] = result.search_id
            report.records["search_title"] = result.title
            if not report.sourcing_url:
                report.sourcing_url = result.url

    async def _update(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        """Write the document's advert over the listing this row already made.

        Title, description, position type, primary role, years, salary and
        equity - the fields the document carries. Skills and location stay as
        posted (their widgets add rather than replace), and so do the visa and
        remote radios (the document never set them). A listing a recruiter has
        published since is left alone: an unattended run must not change what
        candidates can see, the same line the delete holds. A row with no
        listing to rewrite - none recorded, or the recorded one deleted on
        Wellfound - gets a fresh draft, as a post would make it.
        """
        from app.platforms.wellfound_delete import read_job

        found = find_listing(row, self.settings)
        job = await read_job(page, found[0]) if found else None
        if job is None or not job["exists"]:
            # Nothing to rewrite, so the update posts: a fresh draft, exactly
            # as Ready to Post would make it, recorded in the ledger as new.
            # Said in the report (and on the row) because a recruiter reading
            # "updated" would look for their old draft and find it unchanged
            # - or, when it was deleted on Wellfound, not at all.
            why = (
                "no listing is recorded for this row"
                if found is None
                else f"the listing this row made ({found[0]}) is gone from Wellfound"
            )
            log.info("wellfound update - creating a listing instead", extra={"why": why})
            report = await self._drive(page, document, row)
            report.warnings.insert(0, f"listing: {why} - a new draft was created, not updated")
            return report
        job_id, how = found
        if job["published"]:
            raise PlatformError(
                f"Wellfound: the listing {job.get('title')!r} ({job_id}) has been "
                "published since it was posted, and an update would change the "
                "live advert. Unpublish it on Wellfound (its page offers Unpublish) "
                "and set the status again, or edit it there by hand."
            )

        report = RunReport()
        report.warnings.append(f"listing: {how} ({job_id})")
        log.info("wellfound update", extra={"job": job_id, "how": how, "title": job.get("title")})

        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        engine.report = report
        context = build_context(document, row, self.recipe.defaults, self.recipe.key)
        try:
            await engine.run_steps(update_steps(self.recipe.steps, job_id), context)
        except _StopRun:
            # The dry run stopped before Save; everything after it reads back
            # a save that never happened.
            pass
        report.submitted = not self.dry_run
        # The link for the row's Notes line goes in `sourcing_url`, not
        # `post_url`: a post URL on the result is recorded in the ledger as
        # something new to delete, and this listing is already there.
        report.sourcing_url = f"{APP}/recruit/jobs/{job_id}"
        report.captures.pop("post_url", None)
        # The pitch template and saved search follow the document too: the
        # ones the post recorded are rewritten, else found by the role's name.
        recorded = recorded_ids(row, self.settings)
        await self._pitch_and_search(
            page, document, row, report,
            template_id=recorded.get("pitch_template", ""),
            search_id=recorded.get("saved_search", ""),
        )
        return report

    async def _delete(
        self, page: "Page", records: list[dict[str, str]], *, row_title: str
    ) -> tuple[bool, list[str]]:
        """Delete each post's draft listing (wellfound_delete), after reading
        its page back; a listing published since is left alone."""
        from app.platforms.wellfound_delete import delete_job, job_id_from

        done, notes = True, []
        for record in records:
            # What the recruiter half made for this post, when it ran.
            if record.get("pitch_template") or record.get("saved_search"):
                gone, extra = await self._delete_pitch_and_search(page, record)
                notes.extend(extra)
                done = done and gone
                if not (record.get("job") or record.get("post_url")):
                    continue
            if not job_id_from(record.get("job") or record.get("post_url")):
                done = False
                notes.append(
                    "a post left no job id to delete by - find the draft in Wellfound's "
                    f"Jobs list and delete it by hand ({record.get('post_url') or 'no link'})"
                )
                continue
            report = await delete_job(page, record, dry_run=self.dry_run)
            notes.append(report.summary)
            notes.extend(report.warnings)
            if report.accepted_shape and report.accepted_shape != "jobListingId":
                notes.append(f"Wellfound took the id as {report.accepted_shape!r}")
            if not self.dry_run and not report.complete:
                done = False
            elif self.dry_run and report.refused:
                done = False
        return done, notes

    async def _delete_pitch_and_search(self, page: "Page", record: dict[str, str]) -> tuple[bool, list[str]]:
        """Remove a post's pitch template and saved search. Each is read back
        by the name the post recorded first; one a recruiter has renamed is
        left alone and named."""
        from app.platforms.wellfound_api import WellfoundTap
        from app.platforms.wellfound_pitch import delete_pitch
        from app.platforms.wellfound_search import delete_search

        done, notes = True, []
        tap = WellfoundTap(page)
        try:
            for key, title_key, remove in (
                ("saved_search", "search_title", delete_search),
                ("pitch_template", "pitch_title", delete_pitch),
            ):
                if not record.get(key):
                    continue
                try:
                    gone, note = await remove(
                        page, tap, record[key], expect_title=record.get(title_key, ""), dry_run=self.dry_run
                    )
                except PlatformError as exc:
                    gone, note = False, f"{key.replace('_', ' ')} {record[key]}: {exc}"
                notes.append(note)
                done = done and gone
        finally:
            tap.detach()
        return done, notes
