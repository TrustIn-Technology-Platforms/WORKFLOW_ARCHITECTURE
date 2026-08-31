"""Juicebox search criteria: read them, rebuild them from the advert, save them.

A Juicebox *search* scores every candidate against a short list of criteria —
the five that a Datology search carried read like "The candidate has 4+ years
building and operating scalable, reliable cloud infrastructure." They live in a
`Criteria` dialog as an ordered list, **Most Important at the top**, and that
order is the only weighting there is: Juicebox has no required/preferred split,
so a criterion either earns its place in the ranking or should not be there.

That makes the tightening policy different in shape from noon's and Loxo's, and
identical in intent. There is nothing to promote, because nothing is optional —
instead the ranking is built dealbreakers first, then the baseline, then the
disqualifiers, so the checks that must filter sit where Juicebox weighs them
most heavily.

Disqualifiers become negative criteria. Juicebox's own placeholder text invites
them ("Should not be currently working at a defense contractor"), so a trait to
avoid is written as "The candidate does not ..." rather than dropped.

Mechanics that matter:

- Each criterion is a `textarea#criterion_N`, in a drag-and-drop row. The ids
  are positional, so rewriting the list is a matter of filling N textareas.
- They are React-controlled: assigning `.value` is ignored. The native setter
  plus an `input` event is what React's onChange listens to — the same trick the
  sequence driver needs for the sequence name.
- `Add Criterion` grows the list; `Update` commits it. Nothing is saved until
  Update, so a dry run can open the dialog and read without risk.
- The page carries an Osano cookie dialog that is also `role=dialog` and comes
  first in the DOM, so the criteria dialog is found by its heading, never by
  role alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.criteria_ai import configured, draft_criteria
from app.platforms.loxo_criteria import AVOID, BASELINE, DEALBREAKER

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://app.juicebox.ai"
# Juicebox's own limit is not published; ten is more than any advert justifies
# and keeps the ranking meaningful.
MAX_CRITERIA = 10

# The dialog is identified by its heading: Osano's cookie banner is also
# role=dialog and sits earlier in the DOM.
DIALOG = """() => [...document.querySelectorAll('[class*=MuiDialog-paper],[role=dialog]')]
  .find(el => (el.innerText || '').trim().startsWith('Criteria')) || null"""

READ = """() => {
  const rows = [...document.querySelectorAll('textarea[id^=criterion_]')]
    .filter(el => !el.hasAttribute('aria-hidden'));
  return rows.map(el => ({id: el.id, value: el.value || ''}));
}"""

# React ignores a plain `.value =`; the native setter plus an input event is
# what its onChange actually listens to.
WRITE = """([id, text]) => {
  const el = document.getElementById(id);
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(el, text);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

CLICK_IN_DIALOG = """(label) => {
  const dialog = [...document.querySelectorAll('[class*=MuiDialog-paper],[role=dialog]')]
    .find(el => (el.innerText || '').trim().startsWith('Criteria'));
  if (!dialog) return false;
  const button = [...dialog.querySelectorAll('button,[role=button]')]
    .find(el => (el.innerText || '').trim() === label);
  if (!button) return false;
  button.click();
  return true;
}"""

OPEN_CRITERIA = """() => {
  const button = [...document.querySelectorAll('button')]
    .find(el => (el.innerText || '').replace(/\\s+/g, ' ').trim().startsWith('Criteria'));
  if (!button) return false;
  button.click();
  return true;
}"""

# Juicebox scores each criterion as a statement about one candidate, so ask
# for that shape rather than reformatting recruiter shorthand afterwards.
PHRASING = (
    "Write every criterion as a complete sentence about one person, "
    "starting with 'The candidate'. Phrase each trait to avoid as a "
    "sentence starting 'The candidate does not' or 'The candidate is not', "
    "so it reads as a check that can pass or fail."
)

DISMISS_COOKIES = """() => {
  const close = document.querySelector('.osano-cm-dialog__close, .osano-cm-accept-all');
  if (close) { close.click(); return true; }
  return false;
}"""


@dataclass(slots=True)
class SearchCriteriaReport:
    search_url: str = ""
    backup_path: str = ""
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    saved: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"{len(self.before)} criteria before, {len(self.after)} after; "
            f"{'saved' if self.saved else 'not saved'}"
        )


def rank_criteria(draft, *, limit: int = MAX_CRITERIA) -> list[str]:
    """Flatten drafted buckets into Juicebox's single ranked list.

    Order is the whole weighting: dealbreakers first, then the baseline, then
    the disqualifiers as negative criteria. Duplicates are dropped so a
    requirement stated twice does not occupy two of a scarce number of slots.
    """
    ranked: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        key = "".join(ch for ch in cleaned.lower() if ch.isalnum() or ch.isspace())
        key = " ".join(key.split())
        if key in seen:
            return
        seen.add(key)
        ranked.append(cleaned)

    for item in draft.dealbreakers:
        add(item.text)
    for item in draft.baseline:
        add(item.text)
    for text in draft.traits_to_avoid:
        add(text)

    return ranked[:limit]


async def _wait_for_shell(page: "Page", *, attempts: int = 14) -> bool:
    """Juicebox paints blank for 20-30s and never fires domcontentloaded."""
    for _ in range(attempts):
        await page.wait_for_timeout(4_000)
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        if len(text.strip()) > 400:
            return True
    return False


async def _open_dialog(page: "Page") -> list[str]:
    if not await page.evaluate(OPEN_CRITERIA):
        raise PlatformError(
            "The search's Criteria button was not found. Juicebox may have "
            "changed the search page, or the search did not finish loading."
        )
    await page.wait_for_timeout(5_000)
    if not await page.evaluate(DIALOG):
        raise PlatformError("The Criteria dialog did not open.")
    rows = await page.evaluate(READ)
    return [row["value"] for row in rows]


READ_JD = """() => {
  // The search page renders the job description above the Filters/Criteria bar.
  const text = document.body ? document.body.innerText : '';
  const cut = text.search(/\\nFilters\\n/);
  const head = cut > 0 ? text.slice(0, cut) : text;
  const start = head.search(/What we're looking for|About the|We are looking for/);
  return (start > 0 ? head.slice(start) : head).slice(0, 12000);
}"""


async def read_job_description(page: "Page") -> str:
    """The advert as the search itself carries it.

    A search already holds the job description it was built from, and that is
    the right source for its criteria — pointing a client's search at another
    company's advert would be worse than leaving it alone.
    """
    return str(await page.evaluate(READ_JD) or "").strip()


async def read_criteria(page: "Page", search_url: str) -> list[str]:
    """The search's current criteria, in ranked order. Opens nothing else."""
    await page.goto(search_url, wait_until="commit", timeout=90_000)
    if not await _wait_for_shell(page):
        raise PlatformError(
            "Juicebox never rendered its shell. The session has probably "
            "expired: python -m app.cli login juicebox"
        )
    await page.evaluate(DISMISS_COOKIES)
    return await _open_dialog(page)


async def _write_list(page: "Page", ranked: list[str]) -> list[str]:
    """Fill the dialog's rows with `ranked`, growing the list if it is short."""
    rows = await page.evaluate(READ)
    for _ in range(max(0, len(ranked) - len(rows))):
        if not await page.evaluate(CLICK_IN_DIALOG, "Add Criterion"):
            raise PlatformError("Could not add another criterion row.")
        await page.wait_for_timeout(900)

    rows = await page.evaluate(READ)
    if len(rows) < len(ranked):
        raise PlatformError(
            f"Juicebox offers {len(rows)} criterion rows but {len(ranked)} were "
            "drafted; it may cap the list."
        )
    for row, text in zip(rows, ranked):
        if not await page.evaluate(WRITE, [row["id"], text]):
            raise PlatformError(f"Could not write into {row['id']}.")
        await page.wait_for_timeout(250)
    return rows


async def restore_criteria(page: "Page", search_url: str, backup_path: str) -> list[str]:
    """Put a backed-up criteria list back on a search.

    Rows beyond the backup's length are left alone rather than deleted: the
    backup says what was there, not what must not be.
    """
    saved = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    criteria = [c for c in (saved.get("criteria") or []) if str(c).strip()]
    if not criteria:
        raise PlatformError(f"{backup_path} holds no criteria to restore.")

    await read_criteria(page, search_url)  # opens the dialog
    await _write_list(page, criteria)
    if not await page.evaluate(CLICK_IN_DIALOG, "Update"):
        raise PlatformError("The Criteria dialog's Update button was not found.")
    await page.wait_for_timeout(8_000)
    log.info("juicebox criteria restored", extra={"count": len(criteria)})
    return criteria


async def set_criteria(
    page: "Page",
    search_url: str,
    advert_text: str,
    *,
    role_name: str = "",
    settings: Settings | None = None,
    dry_run: bool = False,
) -> SearchCriteriaReport:
    """Rebuild a search's criteria from the advert and save them."""
    settings = settings or get_settings()
    report = SearchCriteriaReport(search_url=search_url)

    if not configured(settings):
        raise PlatformError(
            "ANTHROPIC_API_KEY is not set, and Juicebox's criteria are drafted "
            "from the advert rather than generated by the platform. Set it, or "
            "write the criteria by hand."
        )
    report.before = await read_criteria(page, search_url)
    log.info("juicebox criteria read", extra={"count": len(report.before)})

    advert_text = (advert_text or "").strip() or await read_job_description(page)
    if not advert_text:
        raise PlatformError(
            "No advert text was given and the search carries no job description, "
            "so there is nothing to build criteria from."
        )

    # The criteria we are about to replace, on disk before anything is written.
    directory = Path(settings.artifact_dir) / "juicebox-criteria"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = directory / f"search-{stamp}.json"
    backup.write_text(
        json.dumps({"search_url": search_url, "criteria": report.before}, indent=2),
        encoding="utf-8",
    )
    report.backup_path = str(backup)

    draft = await draft_criteria(
        advert_text,
        wanted=[DEALBREAKER, BASELINE, AVOID],
        role_name=role_name,
        phrasing=PHRASING,
        settings=settings,
    )
    ranked = rank_criteria(draft)
    if not ranked:
        raise PlatformError("No criteria could be drafted from this advert.")
    report.after = ranked

    if dry_run:
        report.warnings.append(
            f"dry run: would replace {len(report.before)} criteria with "
            f"{len(ranked)}"
        )
        return report

    # A row we do not write keeps whatever a recruiter put there: shrinking the
    # list is not this automation's call.
    rows = await _write_list(page, ranked)

    if len(rows) > len(ranked):
        report.warnings.append(
            f"{len(rows) - len(ranked)} existing criterion row(s) left as they "
            "were - the drafted list was shorter"
        )

    if not await page.evaluate(CLICK_IN_DIALOG, "Update"):
        raise PlatformError("The Criteria dialog's Update button was not found.")
    await page.wait_for_timeout(8_000)
    report.saved = True

    log.info(
        "juicebox criteria saved",
        extra={"before": len(report.before), "after": len(ranked)},
    )
    return report
