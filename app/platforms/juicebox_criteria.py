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
  Update, so a dry run can open the dialog and read without risk. Each row is
  filled before the next is added, because Juicebox greys `Add Criterion` out
  while the last row is blank — and a click on a disabled button reports success.
- The page carries an Osano cookie dialog that is also `role=dialog` and comes
  first in the DOM, so the criteria dialog is found by its rows and its heading,
  never by role alone.
- Every lookup that can fail says *which* condition failed. On 2026-09-22 a run
  saved a search and left its ranking empty behind one sentence — "Could not add
  another criterion row" — that fitted four unrelated faults equally well.
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

ADD_LABEL = "Add Criterion"
SAVE_LABEL = "Update"

# Juicebox paints late enough that a fixed wait reads a half-mounted dialog as
# an empty one (2026-09-22: "0 criteria before", then the first Add failed).
DIALOG_WAIT_MS = 12_000
DIALOG_POLL_MS = 1_000
# A new row appears as soon as React re-renders; the wait is for the re-render,
# not the network.
ROW_WAIT_MS = 5_000
ROW_POLL_MS = 250

# Shared by every lookup below, so the module does not contradict itself about
# how the dialog is recognised - which it did until 2026-09-22, when the button
# lookup demanded an exact innerText the heading lookup already normalised.
JS_HELPERS = """
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const panes = () => [...document.querySelectorAll('[class*=MuiDialog-paper],[role=dialog]')];
  const titleOf = (el) => {
    const h = el.querySelector('[class*=MuiDialogTitle],h1,h2,h3,h4,h5,h6');
    return norm(h ? h.innerText : norm(el.innerText).slice(0, 60));
  };
  // The rows are the one signal that cannot be renamed. A heading is checked
  // second because a search with no criteria yet has no rows, and Osano's
  // cookie banner - also role=dialog, also earlier in the DOM - has neither.
  const isCriteria = (el) => !!el.querySelector('textarea[id^=criterion_]')
    || /criteri/i.test(titleOf(el))
    || norm(el.innerText).toLowerCase().startsWith('criteri');
  const criteriaDialog = () => panes().find(isCriteria) || null;
"""

DIALOG_INFO = "() => {" + JS_HELPERS + """
  const hit = criteriaDialog();
  const labelled = (el) => norm(el.innerText)
    || norm(el.getAttribute('aria-label') || el.getAttribute('title') || '');
  return {
    found: !!hit,
    title: hit ? titleOf(hit) : '',
    rows: hit ? hit.querySelectorAll('textarea[id^=criterion_]').length : 0,
    // Buttons say the dialog has finished mounting, which matters for a search
    // whose criteria list is legitimately empty.
    ready: hit ? [...(hit.closest('[class*=MuiDialog-root]') || hit)
      .querySelectorAll('button,[role=button]')]
      .some(el => /update|criterion/i.test(labelled(el))) : false,
    titles: panes().map(titleOf).filter(Boolean).slice(0, 6),
  };
}"""

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

# Returns WHY it failed, never a bare false. The recruiter reads that reason on
# the row, and "Could not add another criterion row" stood for four unrelated
# faults - a closed dialog, a renamed button, a greyed-out one, and buttons
# portalled out of the paper - which is why 2026-09-22 could not be diagnosed.
CLICK_IN_DIALOG = "(label) => {" + JS_HELPERS + """
  const want = label.toLowerCase();
  // A leading '+' or icon glyph is decoration; MUI puts a material-icons
  // ligature inside the button, so its innerText reads 'addAdd Criterion'.
  const strip = (s) => s.toLowerCase().replace(/^[^a-z0-9]+/, '').trim();
  const labelled = (el) => norm(el.innerText)
    || norm(el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const score = (el) => {
    const t = strip(norm(el.innerText));
    const a = strip(norm(el.getAttribute('aria-label') || el.getAttribute('title') || ''));
    if (t === want || a === want) return 3;
    if (t.endsWith(want) || a.endsWith(want)) return 2;
    if (t.startsWith(want) || a.startsWith(want)) return 1;
    return 0;
  };
  const off = (el) => el.disabled === true
    || el.getAttribute('aria-disabled') === 'true'
    || /Mui-disabled/.test(el.className || '');
  const pick = (scope) => [...scope.querySelectorAll('button,[role=button]')]
    .map(el => ({el: el, s: score(el), dis: off(el)}))
    .filter(c => c.s > 0)
    .sort((a, b) => b.s - a.s);

  const dialog = criteriaDialog();
  if (!dialog) {
    return {ok: false, reason: 'no-dialog', label: label,
            titles: panes().map(titleOf).filter(Boolean).slice(0, 6)};
  }
  // MUI renders DialogActions inside the paper, but a portal can put them
  // beside it in the dialog root: both are still this dialog's own buttons.
  const root = dialog.closest('[class*=MuiDialog-root]') || dialog;
  // De-duplicated: one 'Delete criterion' per row would fill the sentence the
  // recruiter reads with the same word eight times.
  const buttons = [...new Set([...root.querySelectorAll('button,[role=button]')]
    .map(labelled).filter(Boolean))].slice(0, 12);
  const found = pick(root);
  if (!found.length) {
    const outside = pick(document.body).length > 0;
    return {ok: false, reason: outside ? 'outside-dialog' : 'no-button',
            label: label, buttons: buttons, title: titleOf(dialog)};
  }
  const live = found.find(c => !c.dis);
  if (!live) {
    return {ok: false, reason: 'disabled', label: label, buttons: buttons,
            found: labelled(found[0].el), title: titleOf(dialog)};
  }
  live.el.click();
  return {ok: true, reason: 'clicked', label: label, found: labelled(live.el)};
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
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        # "0 criteria before, 8 after; not saved" reads like progress. It is the
        # sentence a recruiter saw on 2026-09-22 for a search that ranks nobody.
        if self.dry_run:
            return (
                f"dry run: {len(self.before)} criteria on the search, "
                f"{len(self.after)} drafted; nothing written"
            )
        if not self.saved:
            return (
                f"NOT SAVED - the search still ranks by its {len(self.before)} "
                f"old criteria, not the {len(self.after)} drafted for this role"
            )
        return f"{len(self.before)} criteria before, {len(self.after)} after; saved"


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


def _quoted(items: object, empty: str = "none") -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]  # type: ignore[union-attr]
    return ", ".join(f"'{value}'" for value in values) or empty


def _click_failure(result: dict) -> str:
    """Turn a refused click into a sentence the recruiter can act on.

    Four faults used to share one message. Each names what was found, because
    the criteria stage saves no screenshot the reader could check instead.
    """
    label = str(result.get("label") or ADD_LABEL)
    reason = str(result.get("reason") or "")
    if reason == "no-dialog":
        return (
            f"The Criteria dialog closed before '{label}' could be clicked, so "
            "the criteria were never written and the search is unchanged. "
            f"Dialogs open on the page: {_quoted(result.get('titles'))}. Set the "
            "criteria on the search by hand."
        )
    if reason == "disabled":
        return (
            f"Juicebox's '{result.get('found') or label}' button is greyed out, so "
            "the criteria list could not be grown - Juicebox does that while a "
            "criterion row is still blank. Open the search, fill or delete any "
            "blank criterion, then set the criteria."
        )
    if reason == "outside-dialog":
        return (
            f"A button labelled '{label}' exists on the page but not inside the "
            "Criteria dialog, so clicking it could have hit the wrong control. "
            f"The dialog itself offers: {_quoted(result.get('buttons'))}. Set the "
            "criteria by hand and pass those button names to the developer."
        )
    return (
        f"The Criteria dialog has no '{label}' button. It offers: "
        f"{_quoted(result.get('buttons'))}. Juicebox has renamed or moved it - "
        "set the criteria by hand and pass those names to the developer."
    )


async def _open_dialog(page: "Page") -> list[str]:
    if not await page.evaluate(OPEN_CRITERIA):
        raise PlatformError(
            "The search's Criteria button was not found. Juicebox may have "
            "changed the search page, or the search did not finish loading."
        )
    # Poll rather than wait once: a dialog read before its rows mount looks like
    # a search with no criteria, and the backup then saves an empty list.
    # The rows are what the wait is for, so an empty list costs the whole budget
    # rather than being believed the moment the dialog frame appears.
    info: dict = {}
    waited = 0
    while waited < DIALOG_WAIT_MS:
        await page.wait_for_timeout(DIALOG_POLL_MS)
        waited += DIALOG_POLL_MS
        info = await page.evaluate(DIALOG_INFO)
        if info.get("found") and info.get("rows"):
            break
    if not info.get("found"):
        raise PlatformError(
            "The Criteria dialog did not open: no dialog on the page holds the "
            f"criteria list. Dialogs found: {_quoted(info.get('titles'))}. Open "
            "the search and check Juicebox has not changed the dialog."
        )
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


async def _add_row(page: "Page", have: int, wanted: int) -> list[dict]:
    """Click Add Criterion and wait for the row it promises to exist."""
    result = await page.evaluate(CLICK_IN_DIALOG, ADD_LABEL)
    if not result.get("ok"):
        raise PlatformError(_click_failure(result))
    waited = 0
    while waited < ROW_WAIT_MS:
        await page.wait_for_timeout(ROW_POLL_MS)
        waited += ROW_POLL_MS
        rows = await page.evaluate(READ)
        if len(rows) > have:
            return rows
    # The button was there, enabled, and clicked - and nothing appeared. That is
    # a cap, and it is the only reading left once the click itself is accounted
    # for; before 2026-09-22 a missing button reported this same sentence.
    raise PlatformError(
        f"Juicebox stopped at {have} criterion rows and {wanted} were drafted: "
        f"clicking '{ADD_LABEL}' added nothing, so the search caps the list at "
        f"{have}. Nothing was saved - open the search and either add the "
        "remaining rows by hand or keep the top ones."
    )


async def _write_list(page: "Page", ranked: list[str]) -> list[str]:
    """Fill the dialog's rows with `ranked`, growing the list as it goes.

    A row is filled before the next is added, not after all of them: Juicebox
    greys Add Criterion out while the last row is blank, and a click that adds
    nothing then points at the row it stopped on rather than at a total that
    came up short much later.
    """
    info = await page.evaluate(DIALOG_INFO)
    if not info.get("found"):
        # The drafting call between reading and writing takes tens of seconds
        # with the page idle, which is long enough for the dialog to go.
        raise PlatformError(
            "The Criteria dialog is no longer open, so nothing was written and "
            "the search still has the criteria it started with. Set them on the "
            "search by hand."
        )
    rows = await page.evaluate(READ)
    for index, text in enumerate(ranked):
        if index >= len(rows):
            rows = await _add_row(page, len(rows), len(ranked))
        row = rows[index]
        if not await page.evaluate(WRITE, [row["id"], text]):
            raise PlatformError(
                f"Criterion {index + 1} could not be typed into the dialog: its "
                f"row ({row['id']}) disappeared mid-write. Nothing was saved - "
                "re-open the search and set the criteria there."
            )
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
    saved = await page.evaluate(CLICK_IN_DIALOG, SAVE_LABEL)
    if not saved.get("ok"):
        raise PlatformError(_click_failure(saved))
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
    report = SearchCriteriaReport(search_url=search_url, dry_run=dry_run)

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

    saved = await page.evaluate(CLICK_IN_DIALOG, SAVE_LABEL)
    if not saved.get("ok"):
        raise PlatformError(_click_failure(saved))
    await page.wait_for_timeout(8_000)
    report.saved = True

    log.info(
        "juicebox criteria saved",
        extra={"before": len(report.before), "after": len(ranked)},
    )
    return report
