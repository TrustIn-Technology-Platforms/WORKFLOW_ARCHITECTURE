"""Juicebox (PeopleGPT) — a hand-written driver, not a YAML recipe.

Juicebox is the platform the recipe format cannot describe, so it takes the
escape hatch documented in docs/07-platform-recipes.md: a Python driver that
plugs into `RecipeAdapter` and reuses all of its session, login and
failure-artifact handling, overriding only the part that drives the page.

Why it needs a driver, established live on 2026-08-27 (see
docs/platforms/juicebox.md and memory `juicebox-sequence-editor`):

- The step body is a **TinyMCE editor inside an `about:srcdoc` iframe**, and
  only the *active* step's editor is mounted. Content goes in through TinyMCE's
  own JS API (`setContent` + `save`), keyed by step index, not a CSS selector.
- A sequence is grown one step at a time with an **Add step** button; only the
  first step carries a Subject field — the rest are same-thread follow-ups that
  inherit it. The engine's per-email loop cannot express "add a step between
  emails, and only the first has a subject".
- Clicks that change the route hang Playwright (the app holds the document
  open), so navigation clicks pass `no_wait_after` and gotos wait for `commit`.
- The REST API authenticates with an in-app bearer token, not the cookie, so
  DOM automation is the only route (unlike noon, whose API was the whole job).

Saving a sequence contacts nobody; sending starts only when a recruiter adds
contacts and presses go. So the driver's output is a ready-to-review draft —
the same boundary noon draws.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import (
    Advert,
    NotionRow,
    ParsedDocument,
    PlatformError,
)
from app.platforms.engine import RunReport
from app.utils.templating import juicebox_tokens

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

from app.platforms.adapter import RecipeAdapter

log = get_logger(__name__)

# TinyMCE's editor list is exposed differently across builds; `tinymce.editors`
# is undefined in Juicebox's, so fall back to `tinymce.get()`.
_EDS = (
    "function eds(){var t=window.tinymce;if(!t)return [];"
    "try{if(t.editors&&t.editors.length!==undefined)return [].slice.call(t.editors);}catch(e){}"
    "try{if(typeof t.get==='function'){var g=t.get();return Array.isArray(g)?g:(g?[g]:[]);}}catch(e){}"
    "return [];}"
)
# React tracks controlled inputs through the native value setter; assigning
# `.value` directly leaves its state stale, so drive the setter and fire the
# same events a keystroke would.
_SETN = (
    "function setNative(el,val){var p=el.tagName==='TEXTAREA'"
    "?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
    "var d=Object.getOwnPropertyDescriptor(p,'value').set;d.call(el,val);"
    "el.dispatchEvent(new Event('input',{bubbles:true}));"
    "el.dispatchEvent(new Event('change',{bubbles:true}));}"
)

# Filling a TinyMCE step is not just setContent: only the active step's editor
# is mounted, and when a later step is added this one unmounts. The app's React
# wrapper copies the editor's text into its own model on the editor's change
# events, so a bare setContent (which updates TinyMCE but fires nothing the
# wrapper listens to) is discarded on unmount — the classic symptom being the
# first step saving empty. So: mark dirty, fire the whole event set the wrapper
# binds, push an input from the iframe body, sync the backing textarea, and
# blur to force the commit while the editor is still mounted.
_FILL_STEP = "(a)=>{" + _EDS + _SETN + """
  if(a.name){var n=document.querySelector('input[placeholder="Untitled sequence"]');
    if(n){n.focus();setNative(n,a.name);}}
  var subjEls=[].slice.call(document.querySelectorAll('input[placeholder="Add a subject"]'));
  var E=eds();
  var r={editors:E.length,subjectFields:subjEls.length,setSubject:false,setBody:false};
  if(a.subject&&subjEls[a.idx]){subjEls[a.idx].focus();setNative(subjEls[a.idx],a.subject);r.setSubject=true;}
  if(E[a.idx]){var ed=E[a.idx];
    ed.setContent(a.html);
    try{ed.setDirty(true);}catch(e){}
    ['SetContent','input','change','keyup','NodeChange'].forEach(function(ev){try{ed.fire(ev);}catch(e){}});
    var b=ed.getBody&&ed.getBody();
    if(b){['input','keyup','change'].forEach(function(ev){b.dispatchEvent(new Event(ev,{bubbles:true}));});}
    ed.save();
    var ta=ed.getElement&&ed.getElement();
    if(ta){ta.dispatchEvent(new Event('input',{bubbles:true}));ta.dispatchEvent(new Event('change',{bubbles:true}));}
    try{ed.fire('blur');}catch(e){}
    r.setBody=true;r.chars=ed.getContent().length;}
  return r;
}"""

# Read one step's body length by index — used to verify a fill stuck.
_READ_STEP = "(idx)=>{" + _EDS + """
  var E=eds();
  return E[idx]?E[idx].getContent().replace(/<[^>]+>/g,'').trim().length:-1;
}"""

# TinyMCE 8's autoresize plugin throws inside setContent if the editor is
# mounted but not fully initialised (a race right after the step appears). Only
# fill once the editor reports initialised and has a body.
_EDITOR_READY = "(idx)=>{" + _EDS + """
  var e=eds()[idx];
  return !!(e && e.initialized && e.getBody && e.getBody());
}"""

_COUNTS = "()=>{" + _EDS + """
  return {editors:eds().length,
          subjectFields:document.querySelectorAll('input[placeholder="Add a subject"]').length};
}"""


class JuiceboxAdapter(RecipeAdapter):
    """Create a Juicebox email sequence from a document's email steps."""

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
            except Exception:
                continue
            if "Sequences" in text:
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
        await self._open_new_sequence(page)

        # Step 1 exists already after "Start from scratch"; steps 2..N are added.
        for index, email in enumerate(emails):
            if index > 0:
                await self._add_email_step(page, index)
            subject = juicebox_tokens(email.subject) if index == 0 else ""
            body = juicebox_tokens(email.body_html or email.body_text)
            result = await self._fill_step(page, index, name if index == 0 else None,
                                           subject, body)
            if not result.get("setBody"):
                raise PlatformError(
                    f"could not write email {email.order} into step {index + 1}: "
                    f"the editor was not found ({result})"
                )
            report.emails_written += 1
            report.executed += 1
            log.info(
                "juicebox step filled",
                extra={
                    "order": email.order,
                    "index": index,
                    "subject": subject[:80],
                    "chars": result.get("chars"),
                    "editors": result.get("editors"),
                },
            )
            # Move focus off the editor so the app commits this step's body to
            # its model before the next 'Add step' unmounts the editor.
            await self._commit_focus(page)
            await page.wait_for_timeout(800)

        await self._verify_bodies(page, len(emails), report)
        report.captures["post_url"] = _sequence_url(page.url)

        if self.dry_run:
            report.skipped += 1
            report.warnings.append(
                "dry run: stopped before Save. Juicebox may autosave the draft, "
                "so a sequence can still appear in the list."
            )
            log.info("juicebox dry run - not saving", extra={"sequence": name})
            return report

        await self._save(page)
        report.submitted = True
        log.info(
            "juicebox sequence saved",
            extra={"sequence": name, "steps": report.emails_written, "url": report.post_url},
        )
        return report

    # -- page interactions -------------------------------------------------

    async def _open_new_sequence(self, page: "Page") -> None:
        """Open a blank sequence editor, retrying the flow if it stalls.

        'Start from scratch' intermittently hangs on "Getting things ready…"
        and never mounts the editor. A reload and a fresh attempt clears it —
        which is exactly how the flow was driven by hand.
        """
        sequences_url = self.recipe.defaults.get("sequences_url")
        attempts = 4
        last = ""
        for attempt in range(attempts):
            try:
                await self._go_to_sequence_list(page, sequences_url, first=attempt == 0)
                await page.wait_for_timeout(8_000)
                await self._click_button_or_text(page, "New sequence")
                await page.wait_for_timeout(2_500)
                await self._click_button_or_text(page, "Start from scratch")
            except Exception as exc:
                last = _short(exc)
                log.warning(
                    "juicebox open-sequence attempt failed; retrying",
                    extra={"attempt": attempt + 1, "of": attempts, "error": last},
                )
                continue

            # The editor mounts late (~15-20s); wait for a TinyMCE instance.
            for _ in range(12):
                await page.wait_for_timeout(3_000)
                if (await page.evaluate(_COUNTS))["editors"] >= 1:
                    return
            last = "editor stalled on 'Getting things ready…'"
            log.warning(
                "juicebox editor did not render; retrying",
                extra={"attempt": attempt + 1, "of": attempts},
            )
        raise PlatformError(
            f"could not open a blank sequence editor after {attempts} attempts "
            f"({last})"
        )

    async def _fill_step(
        self, page: "Page", index: int, name: str | None, subject: str, html: str
    ) -> dict:
        """Fill one step, waiting for the editor to be ready and retrying the
        transient TinyMCE autoresize crash that setContent hits right after a
        step mounts."""
        # Wait until the target editor is initialised.
        for _ in range(15):
            try:
                if await page.evaluate(_EDITOR_READY, index):
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1_000)

        last: dict = {}
        for _ in range(4):
            try:
                last = await page.evaluate(
                    _FILL_STEP,
                    {"idx": index, "name": name, "subject": subject, "html": html},
                )
                if last.get("setBody"):
                    return last
            except Exception as exc:
                log.debug("fill_step retry", extra={"index": index, "error": _short(exc)})
            await page.wait_for_timeout(2_000)
        return last

    async def _commit_focus(self, page: "Page") -> None:
        """Blur the editor onto the title input so the app commits the step."""
        try:
            await page.evaluate(
                "()=>{var a=document.activeElement;if(a&&a.blur)a.blur();"
                "var n=document.querySelector('input[placeholder=\"Untitled sequence\"]');"
                "if(n){n.focus();n.blur();}}"
            )
        except Exception:
            pass

    async def _verify_bodies(
        self, page: "Page", count: int, report: RunReport
    ) -> None:
        """Re-activate each step and confirm its body is non-empty.

        Clicking a step in the left rail makes it the active step, which also
        commits its live content to the app's model — so this both verifies and
        reinforces the fill. A step that still reads empty is reported, not
        silently saved.
        """
        empty: list[int] = []
        for n in range(1, count + 1):
            try:
                await page.get_by_text(f"Step {n}:", exact=False).first.click(
                    timeout=5_000, no_wait_after=True
                )
                await page.wait_for_timeout(1_200)
            except Exception:
                continue
            length = await page.evaluate(_READ_STEP, n - 1)
            log.info("juicebox body check", extra={"step": n, "chars": length})
            if length <= 0:
                empty.append(n)
        if empty:
            report.warnings.append(
                "steps saved with an empty body: " + ", ".join(map(str, empty))
            )

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
        await self._click_text(page, "Sequences")

    async def _add_email_step(self, page: "Page", expected_index: int) -> None:
        before = (await page.evaluate(_COUNTS))["editors"]
        try:
            await page.get_by_role("button", name="Add step", exact=True).first.click(
                timeout=8_000
            )
        except Exception as exc:
            raise PlatformError(f"could not click 'Add step': {_short(exc)}") from exc
        await page.wait_for_timeout(3_000)

        counts = await page.evaluate(_COUNTS)
        if counts["editors"] <= before:
            # Some builds open a step-type menu; choose Email.
            for choose in (
                lambda: page.get_by_role("menuitem", name="Email").first.click(timeout=3_000),
                lambda: page.get_by_text("Email", exact=True).last.click(
                    timeout=3_000, no_wait_after=True
                ),
            ):
                try:
                    await choose()
                    break
                except Exception:
                    continue
            await page.wait_for_timeout(3_000)
            counts = await page.evaluate(_COUNTS)

        if counts["editors"] < expected_index + 1:
            raise PlatformError(
                f"adding step {expected_index + 1} failed: editor count stayed at "
                f"{counts['editors']}"
            )

    async def _save(self, page: "Page") -> None:
        try:
            await page.get_by_role("button", name="Save", exact=True).first.click(
                timeout=10_000
            )
        except Exception as exc:
            raise PlatformError(f"could not click 'Save': {_short(exc)}") from exc
        await page.wait_for_timeout(5_000)

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


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


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
