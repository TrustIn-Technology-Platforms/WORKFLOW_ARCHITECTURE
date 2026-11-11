"""Wellfound's outreach: one pitch template per role.

Wellfound has no sequence. A recruiter pitches a candidate from Find Talent
with a single note, and the note comes from a *pitch template* kept under
Settings → Templates - the recruiters make one per role, titled as the job
is and holding the whole message in the first box (read off the account's
sixteen pitch templates, 2026-11-12). This writes that template from the
document, so the role's pitch is waiting when a recruiter opens a candidate.

**Nothing here contacts anyone.** A template is text on the account; a
candidate hears from it only when a recruiter presses *Request to chat*.

The message is the document's **InMail** step: a complete cold message with
no thread behind it, which is what a Wellfound pitch is. A document without
one falls back to the LinkedIn message, then the first email. The greeting
line and the sign-off are dropped - Wellfound adds the candidate's name
above the note and the template's own footer (`Best, <recruiter>`) below it.

The form is driven through the page (stable `form-input--*` ids, mapped
2026-11-12); the list is read back through Wellfound's own `PitchRecipes`
call to prove what was saved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import ParsedDocument, PlatformError
from app.platforms.wellfound_api import APP, WellfoundTap, errors_of

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

TEMPLATES_URL = f"{APP}/recruit/account/templates"
# Which step of the document is the pitch, best first.
PITCH_CHANNELS = ("inmail", "linkedin", "email")
# Wellfound's title box takes more, but the list shows one line.
TITLE_MAX = 150

_GREETING = re.compile(r"^\s*(hi|hello|hey|dear)\b[^\n]{0,60}[,!:.]?\s*$", re.I)
_SIGN_OFF = re.compile(
    r"^\s*(best|best wishes|best regards|kind regards|warm regards|regards|thanks|"
    r"many thanks|thank you|cheers|yours sincerely|yours|sincerely|talk soon)\s*[,!.]?\s*$",
    re.I,
)
# A merge token in any of the document's spellings: {first_name}, {{ company }}.
_TOKEN = re.compile(r"\{\{?\s*[\w .]+\s*\}?\}")

# The boxes a pitch leaves empty. A recruiter's first-ever template opens
# with Wellfound's boilerplate in the first two, so they are cleared, not
# trusted to be blank.
_EMPTY_BOXES = ("cta", "companyDescription", "teamDescription", "companyAchievements")


@dataclass(slots=True)
class Pitch:
    title: str
    body: str
    source: str  # which step of the document it came from
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PitchResult:
    template_id: str = ""
    title: str = ""
    action: str = ""  # created | updated | unchanged | would create | would update
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        where = f" ({self.template_id})" if self.template_id else ""
        return f"pitch template {self.action}: {self.title!r}{where}"


def clean_body(text: str) -> str:
    """The message without its greeting and sign-off, paragraphs kept."""
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _GREETING.match(lines[0]):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # A sign-off is the closing word and, at most, the name under it.
    for back in (1, 2):
        if len(lines) >= back and _SIGN_OFF.match(lines[-back]):
            del lines[-back:]
            break
    body = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", body)


def pitch_from(document: ParsedDocument, *, title: str = "") -> Pitch | None:
    """The role's pitch, or None when the document has no message to make
    one from (an advert-only document)."""
    step = None
    for channel in PITCH_CHANNELS:
        candidates = sorted((e for e in document.emails if e.channel == channel), key=lambda e: e.order)
        candidates = [e for e in candidates if (e.body_text or "").strip()]
        if candidates:
            step = candidates[0]
            break
    if step is None:
        return None
    body = clean_body(step.body_text)
    if not body:
        return None
    advert = document.advert_for("wellfound") or document.advert
    name = (
        title.strip()
        or (advert.title.strip() if advert is not None and advert.title else "")
        or (step.subject or "").strip()
        or (document.source_name or "").strip()
    )
    if not name:
        return None
    pitch = Pitch(title=name[:TITLE_MAX], body=body, source=step.label or step.channel)
    if step.channel != "inmail":
        pitch.warnings.append(
            f"pitch: the document has no InMail section, so its {step.label or step.channel} "
            "was used as the Wellfound pitch"
        )
    tokens = sorted(set(_TOKEN.findall(body)))
    if tokens:
        # Wellfound's pitch box has no merge fields; a token would reach the
        # candidate as typed.
        pitch.warnings.append(
            "pitch: the message still carries " + ", ".join(tokens) + " - Wellfound will "
            "not fill these in; edit the template (or the document) before pitching"
        )
    return pitch


def _same(a: str, b: str) -> bool:
    squash = lambda s: re.sub(r"\s+", " ", (s or "")).strip()  # noqa: E731
    return squash(a) == squash(b)


def match_template(templates: list[dict[str, Any]], *, title: str, template_id: str = "") -> dict[str, Any] | None:
    """The pitch template this role already has: the recorded id, else the
    newest pitch-kind template with exactly this title."""
    pitches = [t for t in templates if (t.get("kind") or "PITCH") == "PITCH"]
    if template_id:
        for template in pitches:
            if str(template.get("id")) == str(template_id):
                return template
    same = [t for t in pitches if _same(str(t.get("title") or "").lower(), title.lower())]
    if not same:
        return None
    return max(same, key=lambda t: int(t.get("updatedAt") or t.get("createdAt") or 0))


async def list_templates(tap: WellfoundTap) -> list[dict[str, Any]]:
    body = await tap.call("PitchRecipes", {"startupId": tap.startup_id})
    try:
        recipes = body["data"]["startup"]["recruit"]["pitchRecipes"]
    except (KeyError, TypeError) as exc:
        raise PlatformError(
            "Wellfound's templates list came back in a shape this does not know"
            + (f": {errors_of(body)}" if errors_of(body) else ".")
        ) from exc
    return [r for r in (recipes or []) if isinstance(r, dict)]


async def _open_templates(page: "Page") -> None:
    await page.goto(TEMPLATES_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector("button:has-text('Create new template')", timeout=60_000)


async def _fill(page: "Page", selector: str, value: str) -> None:
    box = page.locator(selector).locator("visible=true").first
    await box.wait_for(state="visible", timeout=20_000)
    await box.fill(value)


async def save_pitch(
    page: "Page",
    tap: WellfoundTap,
    pitch: Pitch,
    *,
    dry_run: bool,
    template_id: str = "",
) -> PitchResult:
    """Create the role's pitch template, or rewrite the one it has.

    Found by the recorded id, else by an exact title match among the pitch
    templates, so a second post of one role rewrites its template instead of
    leaving two. Read back through the list afterwards: a save Wellfound did
    not keep is an error, not a success.
    """
    await _open_templates(page)
    await tap.ready()
    existing = match_template(await list_templates(tap), title=pitch.title, template_id=template_id)
    result = PitchResult(title=pitch.title, warnings=list(pitch.warnings))

    if existing is not None:
        result.template_id = str(existing.get("id") or "")
        if _same(existing.get("recruiterDescription") or "", pitch.body) and _same(
            existing.get("title") or "", pitch.title
        ):
            result.action = "unchanged"
            return result
    if dry_run:
        result.action = "would update" if existing is not None else "would create"
        return result

    if existing is not None:
        await page.locator(f"[data-test='PitchRecipes-Edit--{result.template_id}']").first.click(timeout=20_000)
    else:
        await page.locator("button:has-text('Create new template')").first.click(timeout=20_000)
        await page.locator("[data-test='CreateDropdown-Item--Pitch template']").first.click(timeout=20_000)
    await _fill(page, "#form-input--title", pitch.title)
    await _fill(page, "#form-input--recruiterDescription", pitch.body)
    if existing is None:
        for name in _EMPTY_BOXES:
            await _fill(page, f"#form-input--{name}", "")
    save = page.locator("button[type='submit']:has-text('Save pitch template')").locator("visible=true").first
    await save.wait_for(state="visible", timeout=20_000)
    if await save.is_disabled():
        # The button enables on a changed form; a blur lets the form notice.
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(800)
    await save.click(timeout=20_000)
    await page.wait_for_timeout(3_000)

    saved = match_template(await list_templates(tap), title=pitch.title, template_id=result.template_id)
    if saved is None or not _same(saved.get("recruiterDescription") or "", pitch.body):
        raise PlatformError(
            f"Wellfound did not keep the pitch template {pitch.title!r}: it is "
            + ("not in the templates list" if saved is None else "there, but with different text")
            + " after Save. Open Settings → Templates on Wellfound to see what it holds."
        )
    result.template_id = str(saved.get("id") or "")
    result.action = "updated" if existing is not None else "created"
    log.info("wellfound pitch template saved", extra={"template": result.template_id, "action": result.action})
    return result


async def delete_pitch(
    page: "Page",
    tap: WellfoundTap,
    template_id: str,
    *,
    expect_title: str = "",
    dry_run: bool,
) -> tuple[bool, str]:
    """Remove the template a post made. `(gone, what happened)`.

    The id must still carry the title the post recorded: a template a
    recruiter has since renamed is theirs now and is left alone.
    """
    await _open_templates(page)
    await tap.ready()
    template = next((t for t in await list_templates(tap) if str(t.get("id")) == str(template_id)), None)
    if template is None:
        return True, f"pitch template {template_id} was already gone"
    title = str(template.get("title") or "")
    if expect_title and not _same(title.lower(), expect_title.lower()):
        return False, (
            f"pitch template {template_id} is now titled {title!r}, not {expect_title!r} - "
            "left in place; delete it under Settings → Templates if it is this role's"
        )
    if dry_run:
        return True, f"would delete pitch template {title!r} ({template_id})"
    # Wellfound asks with the browser's own confirm() - no dialog in the page
    # and no call until it is answered (seen 2026-11-12). Playwright answers
    # an unhandled one with Cancel, so this one click is given an OK.
    answered: list[str] = []

    async def accept(dialog: Any) -> None:
        answered.append(dialog.message)
        await dialog.accept()

    page.once("dialog", accept)
    await page.locator(f"[data-test='PitchRecipes-Delete--{template_id}']").first.click(timeout=20_000)
    await page.wait_for_timeout(3_000)
    if not answered:
        page.remove_listener("dialog", accept)
    if any(str(t.get("id")) == str(template_id) for t in await list_templates(tap)):
        return False, (
            f"pitch template {title!r} ({template_id}) is still listed after Delete - "
            "remove it under Settings → Templates on Wellfound"
        )
    return True, f"pitch template {title!r} deleted"
