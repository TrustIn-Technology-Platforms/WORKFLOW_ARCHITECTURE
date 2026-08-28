"""Loxo Outreach — a hand-written driver, not a YAML recipe.

Loxo's campaign editor is too stateful for the recipe engine: the app boots
slowly and intermittently shows a "Try again" data-fetch card, the campaign is
created before it can be named (there is no dry run past creation, like noon),
stages are grown one modal at a time, and "reply in email thread" is a per-stage
toggle set only after a stage exists. So it takes the escape hatch documented in
docs/07-platform-recipes.md — a Python driver on top of `RecipeAdapter`, reusing
its session/login/failure-artifact handling.

Every selector here was proven live on 2026-08-27 against agency 28356, signed
in as marcus@ (see docs/platforms/loxo.md and memory `loxo-platform-facts`):

- Route is `/agencies/28356/campaigns`; `/outreach` 404s. Drive through the
  app's profile runner — bare storage_state gets throttled to blank renders.
- Rename lives behind the campaign gear (`settings`) in a portal flyout
  (`[data-testid=flyout_container]`); its own Save/close, never the page's.
- A stage modal has Subject, a Quill `.ql-editor` body, and a numeric delay;
  "Add" commits it. Merge fields come from the Person menu, not typed.
- "Reply in email thread?" (default OFF) is set per stage via the stage gear →
  Edit → toggle → Save; turning it on removes the Subject field. Every email
  after the first is a threaded follow-up, so it must be ON for stages 2..N.

The output is a ready-to-review campaign left OFF with no prospects; sending
starts only when a recruiter adds prospects and switches the campaign on. Same
boundary noon and Juicebox draw.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import Advert, AuthenticationRequired, NotionRow, ParsedDocument, PlatformError
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RunReport, _role_name

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

FLYOUT = "[data-testid=flyout_container]"
# A visible text input that is not a checkbox/hidden/number — Loxo's name and
# subject fields carry no `type` attribute, so `[type=text]` never matches them.
TEXT_INPUT = "input:not([type=checkbox]):not([type=hidden]):not([type=number])"
REPLY_LABEL = "Reply in email thread?"
# The stage-menu "Edit" item is a glyph glued to its label ("editEdit"); match
# the label with the glyph optional, and require visibility — a hidden copy of
# the menu exists for every stage card.
EDIT_ITEM = "text=/^\\s*(edit)?\\s*Edit\\s*$/ >> visible=true"
DEFAULT_FOLLOWUP_DELAY_DAYS = 3


class LoxoAdapter(RecipeAdapter):
    """Create (or update) a Loxo Outreach campaign from a document's emails."""

    # ------------------------------------------------------------------
    # login
    # ------------------------------------------------------------------
    async def _assert_logged_in(self, page: "Page") -> None:
        """Boot the SPA from the homepage and confirm the logged-in shell.

        Cold deep-links land on the "Try again" card; the homepage is reliable.
        The app renders nothing for 10-20s, so poll rather than trust a fixed
        selector timeout.
        """
        await page.goto("https://app.loxo.co/", wait_until="domcontentloaded", timeout=90_000)
        for _ in range(20):
            await page.wait_for_timeout(2_000)
            try:
                text = await page.evaluate("document.body ? document.body.innerText : ''")
            except Exception:
                continue
            if "/login" in page.url or "Continue with" in text:
                break
            if "Outreach" in text:
                return
        raise AuthenticationRequired(
            f"{self.recipe.label} is not logged in (or the session has expired). "
            f"Run: python -m app.cli login {self.recipe.key}"
        )

    # ------------------------------------------------------------------
    # drive
    # ------------------------------------------------------------------
    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        report = RunReport()
        emails = sorted([e for e in document.emails if e.is_email], key=lambda e: e.order)
        if not emails:
            raise PlatformError(
                "Loxo posts an email sequence, but the document produced no "
                "email steps."
            )

        advert = document.advert or Advert(title="", body_text="", body_html="")
        name = _role_name(document.source_name, row, advert, emails)
        if not name:
            raise PlatformError("Could not determine a campaign name for this document.")

        base = self.recipe.defaults.get("base_url", "https://app.loxo.co")
        slug = self.recipe.defaults.get("agency_id", "28356")
        campaigns_url = f"{base}/agencies/{slug}/campaigns"

        existing = await self._find_campaign(page, campaigns_url, name)

        if self.dry_run:
            report.warnings.append(
                f"dry run: would {'update' if existing else 'create'} campaign {name!r}"
            )
            report.captures["post_url"] = existing or campaigns_url
            return report

        if existing:
            await self._open_stages(page, existing)
            campaign_url = _clean_campaign_url(existing)
        else:
            await self._create_campaign(page)
            # Capture the new campaign's id now: renaming can bounce the app back
            # to the list, so we must be able to return to this editor by URL.
            campaign_url = _clean_campaign_url(page.url)
            await self._rename_campaign(page, name)
            await self._open_stages(page, campaign_url.rstrip("/") + "/stages")

        report.captures["post_url"] = campaign_url or _clean_campaign_url(page.url)

        # Guard: never edit a campaign whose title is not the one we intend. The
        # header renders the name as text once the editor is loaded.
        if not await page.get_by_text(name, exact=True).count():
            body_text = await page.evaluate("() => document.body.innerText")
            if name not in body_text:
                raise PlatformError(
                    f"refusing to edit: the open campaign is not {name!r} (safety guard)."
                )

        # An existing campaign that already has stages is left as-is: appending
        # would duplicate the sequence, and replacing populated stages is not
        # built yet. Delete its stages by hand to re-fill, or rename the doc.
        if existing:
            count = await self._stage_count(page)
            if count > 0:
                report.warnings.append(
                    f"campaign already has {count} stage(s); left unchanged "
                    "(re-posting does not replace existing stages yet)."
                )
                log.info("loxo campaign left unchanged", extra={"campaign": name, "stages": count})
                return report

        first_tok, company_tok = await self._learn_tokens(page)
        signature = self.recipe.defaults.get("signature", "")

        for n, step in enumerate(emails, start=1):
            subject = _translate(step.subject, first_tok, company_tok)
            body = _translate(step.body_text, first_tok, company_tok)
            if signature:
                body = f"{body}\n\n{signature}"
            delay = 0 if n == 1 else (step.delay_days or DEFAULT_FOLLOWUP_DELAY_DAYS)
            await self._add_stage(page, n, subject, body, delay)
            report.emails_written += 1
            report.executed += 1

        # Every email after the first is a threaded follow-up whose gap is in
        # days — both are set in the edit modal, which the add modal can't do.
        for n, step in enumerate(emails, start=1):
            if n == 1:
                continue
            delay = step.delay_days or DEFAULT_FOLLOWUP_DELAY_DAYS
            await self._finalise_stage(page, n, delay)

        log.info(
            "loxo campaign ready",
            extra={"campaign": name, "stages": report.emails_written, "url": report.post_url},
        )
        return report

    # ------------------------------------------------------------------
    # search / create / open
    # ------------------------------------------------------------------
    async def _find_campaign(self, page: "Page", campaigns_url: str, name: str) -> str | None:
        """Return the stages URL of an existing campaign with this exact name,
        or None. Names follow one convention, so an exact match is safe."""
        await page.goto(campaigns_url, wait_until="domcontentloaded")
        await self._wait_ready(page, "text=Add Campaign")
        search = page.locator("input[placeholder*='Search' i]").first
        if await search.count():
            await search.fill(name)
            await page.wait_for_timeout(4_000)
        matches = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"/campaigns/\"]'))"
            ".map(a => ({t: (a.innerText||'').trim(), h: a.getAttribute('href')}))"
            ".filter(x => x.t)"
        )
        for m in matches:
            if m["t"] == name and m["h"]:
                href = m["h"]
                url = href if href.startswith("http") else f"https://app.loxo.co{href}"
                return url if url.rstrip("/").endswith("stages") else url.rstrip("/") + "/stages"
        return None

    async def _create_campaign(self, page: "Page") -> None:
        """Add Campaign → 'Create a campaign' chooser → the From Scratch button.

        The chooser shows two cards, 'From Template' (button 'Browse templates')
        and 'From Scratch' (button 'Start new'). 'From Scratch' is only a heading,
        so target the button. `_role_name` is what names it afterwards.
        """
        await page.get_by_text("Add Campaign", exact=True).first.click()
        await page.wait_for_selector("text=Create a campaign", timeout=20_000)
        await page.wait_for_timeout(1_000)
        for label in ("Start new", "Start from scratch", "Create from scratch"):
            btn = page.get_by_role("button", name=label)
            if not await btn.count():
                btn = page.get_by_text(label, exact=True)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click()
                break
        else:
            raise PlatformError(
                "'Create a campaign' chooser had no from-scratch button "
                "(expected 'Start new')."
            )
        # A fresh campaign lands on its own stages page (Untitled, 0 stages).
        await self._wait_ready(page, "text=New Stage")
        await page.wait_for_timeout(1_500)

    async def _open_stages(self, page: "Page", stages_url: str) -> None:
        await page.goto(stages_url, wait_until="domcontentloaded")
        await self._wait_ready(page, "text=Stages")
        await page.wait_for_timeout(2_000)

    async def _wait_ready(self, page: "Page", selector: str) -> None:
        """Wait for a Loxo view, clicking through the intermittent 'Try again'
        data-fetch card rather than reloading (a reload restarts the 10-20s boot)."""
        for _ in range(5):
            try:
                await page.wait_for_selector(selector, timeout=30_000)
                return
            except Exception:
                retry = page.get_by_text("Try again", exact=True)
                if await retry.count():
                    await retry.first.click()
                else:
                    await page.wait_for_timeout(3_000)
        raise PlatformError(f"Loxo view never rendered (waiting for {selector!r}).")

    # ------------------------------------------------------------------
    # rename
    # ------------------------------------------------------------------
    async def _rename_campaign(self, page: "Page", name: str) -> None:
        await page.get_by_text("settings", exact=True).first.click()
        await page.wait_for_selector("text=Campaign name", timeout=20_000)
        await page.wait_for_timeout(1_000)
        box = await self._name_box(page, name)
        if box is None:
            raise PlatformError("could not find the Campaign name input in the settings flyout.")
        await box.click()
        await box.fill(name)
        await page.wait_for_timeout(800)
        await page.locator(FLYOUT).get_by_text("Save", exact=True).first.click()
        await page.wait_for_timeout(3_000)
        if await page.locator(FLYOUT).count():
            close = page.locator(FLYOUT).get_by_text("close", exact=True)
            if await close.count():
                await close.first.click()
                await page.wait_for_timeout(1_500)

    async def _name_box(self, page: "Page", name: str):
        by_label = page.get_by_label("Campaign name")
        if await by_label.count():
            return by_label.first
        inputs = page.locator(TEXT_INPUT)
        for i in range(await inputs.count()):
            box = inputs.nth(i)
            try:
                if await box.is_visible() and (await box.input_value()).strip() in ("Untitled", name):
                    return box
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # merge tokens
    # ------------------------------------------------------------------
    async def _learn_tokens(self, page: "Page") -> tuple[str | None, str | None]:
        """Read the exact merge-token strings Loxo inserts, in a stage panel
        that is then Cancelled so nothing is saved."""
        await self._open_stage_panel(page)
        await page.locator(".ql-editor").first.click()
        got_first = await self._pick_person_field(page, r"^\s*first\s*name\s*$")
        after_first = (await page.locator(".ql-editor").first.inner_text()).strip()
        got_company = await self._pick_person_field(page, r"^\s*(current\s+)?company(\s+name)?\s*$")
        after_both = (await page.locator(".ql-editor").first.inner_text()).strip()
        first_tok = after_first if got_first else None
        company_tok = after_both[len(after_first):].strip() if got_company else None
        await page.get_by_text("Cancel", exact=True).first.click()
        await self._panel_closed(page)
        log.info("loxo tokens", extra={"first_name": first_tok, "company": company_tok})
        return first_tok, company_tok

    async def _pick_person_field(self, page: "Page", pattern: str) -> bool:
        await page.get_by_text("Person", exact=True).first.click()
        await page.wait_for_timeout(1_500)
        option = page.get_by_text(re.compile(pattern, re.I))
        if not await option.count():
            await page.keyboard.press("Escape")
            return False
        await option.first.click()
        await page.wait_for_timeout(1_200)
        return True

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------
    async def _open_stage_panel(self, page: "Page") -> None:
        """Empty state offers 'New Stage'; once a stage exists, the header '+ Stage'."""
        new = page.get_by_text("New Stage", exact=True)
        if await new.count() and await new.first.is_visible():
            await new.first.click()
        else:
            await page.get_by_text("Stage", exact=True).first.click()
        await page.wait_for_selector(".ql-editor", timeout=20_000)
        await page.wait_for_timeout(1_200)

    async def _panel_closed(self, page: "Page") -> None:
        await page.wait_for_selector(".ql-editor", state="detached", timeout=30_000)
        await page.wait_for_timeout(2_500)

    async def _set_delay_unit_days(self, page: "Page") -> None:
        """Switch the stage delay unit from the default Hours to Days. The
        control reads e.g. 'Hours unfold_more'; the menu option renders in a
        portal later in the DOM, so take the last 'Days' match."""
        unit = page.get_by_text(re.compile(r"^(Hours?|Days?|Minutes?|Weeks?)\s*unfold_more$", re.I))
        if not await unit.count():
            unit = page.get_by_text(re.compile(r"^(Hours?|Days?)$", re.I))
        if not await unit.count():
            return
        await unit.first.click()
        await page.wait_for_timeout(900)
        days = page.get_by_text(re.compile(r"^Days?$", re.I))
        if await days.count():
            await days.last.click()
            await page.wait_for_timeout(600)

    async def _stage_count(self, page: "Page") -> int:
        text = await page.evaluate("() => document.body.innerText")
        m = re.search(r"(\d+)\s*Stages?", text)
        return int(m.group(1)) if m else 0

    async def _add_stage(self, page: "Page", n: int, subject: str, body: str, delay_days: int) -> None:
        await self._open_stage_panel(page)
        subject_box = page.locator(f"{TEXT_INPUT}:visible").first
        await subject_box.click()
        await subject_box.fill(subject)

        editor = page.locator(".ql-editor").first
        await editor.click()
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if line:
                await page.keyboard.insert_text(line)
            if i < len(lines) - 1:
                await page.keyboard.press("Enter")

        # The add modal's delay unit is Hours-only; the day value and the
        # reply-in-thread toggle are set afterwards in the edit modal, which is
        # the only place the unit dropdown works (see _finalise_stage).
        delay_box = page.locator("input[type=number]:visible").first
        if await delay_box.count():
            await delay_box.click()
            await delay_box.fill(str(delay_days))
        await page.wait_for_timeout(600)
        await page.get_by_text("Add", exact=True).first.click()
        await self._panel_closed(page)
        log.info("loxo stage added", extra={"stage": n, "subject": subject[:60], "delay_days": delay_days})

    # ------------------------------------------------------------------
    # reply-in-thread
    # ------------------------------------------------------------------
    async def _finalise_stage(self, page: "Page", stage: int, delay_days: int) -> None:
        """For a follow-up stage, in one edit-modal open: turn 'Reply in email
        thread?' ON, and set the delay in days (the add modal is Hours-only).
        Gear nth(0) is the campaign; stage k is gear nth(k)."""
        await self._open_stage_editor(page, stage)
        label = page.get_by_text(REPLY_LABEL, exact=True)
        if not await label.count():
            await self._cancel_editor(page)
            raise PlatformError(f"stage {stage}: no {REPLY_LABEL!r} control in the edit modal.")
        toggle = page.locator(
            f"xpath=//*[normalize-space(text())='{REPLY_LABEL}']"
            "/ancestor::div[.//input[@type='checkbox']][1]//input[@type='checkbox']"
        ).first
        if not await toggle.is_checked():
            await toggle.click()
            await page.wait_for_timeout(800)
        if not await toggle.is_checked():
            await self._cancel_editor(page)
            raise PlatformError(f"stage {stage}: reply-in-thread toggle did not switch on.")

        await self._set_delay_unit_days(page)
        delay_box = page.locator("input[type=number]:visible").first
        if await delay_box.count():
            await delay_box.click()
            await delay_box.fill(str(delay_days))
            await page.wait_for_timeout(500)

        await page.get_by_text("Save", exact=True).first.click()
        await self._panel_closed(page)
        log.info("loxo stage finalised", extra={"stage": stage, "delay_days": delay_days})

    async def _open_stage_editor(self, page: "Page", stage: int) -> None:
        edit = page.locator(EDIT_ITEM)
        for attempt in range(2):
            gear = page.get_by_text("settings", exact=True).nth(stage)
            await gear.scroll_into_view_if_needed()
            await gear.click()
            try:
                await edit.first.wait_for(state="visible", timeout=8_000)
                break
            except Exception:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(800)
        else:
            raise PlatformError(f"stage {stage}: gear menu never showed Edit.")
        await edit.first.click()
        await page.wait_for_selector(".ql-editor", timeout=15_000)
        await page.wait_for_timeout(1_200)

    async def _cancel_editor(self, page: "Page") -> None:
        cancel = page.get_by_text("Cancel", exact=True)
        if await cancel.count():
            await cancel.first.click()
            try:
                await self._panel_closed(page)
            except Exception:
                pass


def _translate(text: str, first_tok: str | None, company_tok: str | None) -> str:
    if first_tok:
        text = re.sub(r"\{\{?\s*first_name\s*\}?\}", first_tok, text)
    if company_tok:
        text = re.sub(r"\{\{?\s*(current_)?company\s*\}?\}", company_tok, text)
    return text


def _clean_campaign_url(url: str) -> str:
    """Prefer .../campaigns/<id> over the /stages editor URL."""
    m = re.search(r"(https://app\.loxo\.co/agencies/\d+/campaigns/\d+)", url or "")
    return m.group(1) if m else (url or "")
