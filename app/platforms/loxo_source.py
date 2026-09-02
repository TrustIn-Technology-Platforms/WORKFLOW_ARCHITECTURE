"""Loxo's Source screen: similar titles and skills, written and saved.

The screen lives at `/agencies/<agency>/jobs/<job>/source` (a person reaches it
via the job page -> Add People -> Loxo Search). Its filter panel is a flat
sibling list - a header button per section, that section's content as the
following siblings, until the next header button. Chips do NOT survive a page
reload on their own: persistence is the **Save search** control, which stores a
named, team-shared saved search that anyone loads back from `Saved searches`
(filter box -> name). All of this was mapped and proven live on job 3658508
on 2026-09-01/02, chip by chip, including the reload-and-restore round trip.

Three traps this module encodes, each found by watching a run go wrong:

- Only real section labels bound a section. The "Include similar Job Titles"
  toggle inside Title's own content is also a left-panel button, and treating
  it as a boundary cut the Title window to nothing.
- Scoping by ancestry instead of siblings once put twelve skills into the
  *Title* box, where Loxo's taxonomy dressed them up as job titles ("SOC 2" ->
  "SOC 2 Analyst"). Every add now verifies the chip landed in its own section.
- The chip inputs re-render on every commit, so element handles are never
  reused - focus is re-acquired through the section on each value.

Values the taxonomy refuses (no suggestion, Enter leaves the text in the box)
are cleared and reported, never left half-typed. "Infrastructure as Code" is a
known refusal; that is Loxo's vocabulary, not an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

# The panel's section labels, verbatim. Anything else that looks like a button
# in the left column is section *content* (toggles, chips), not a boundary.
_HELPERS = """
  const SECTION_LABELS = ['Title','Location','Timezone','Industry','Years of Experience',
      'Skills','Diversity','Security Clearance','Company','Tenure','Company Ranking',
      'Company Size','School','Degree','Graduation Year','School Ranking'];
  const firstLine = (el) => (el.innerText || '').trim().split(String.fromCharCode(10))[0].trim();
  const isHeader = (el) => el.tagName === 'BUTTON'
      && el.getBoundingClientRect().x < 360
      && el.getBoundingClientRect().width > 0
      && SECTION_LABELS.includes(firstLine(el));
  const headerOf = (label) => [...document.querySelectorAll('button')].find(b =>
      isHeader(b) && firstLine(b) === label);
  const sectionNodes = (label) => {
    let head = headerOf(label);
    if (!head) return null;
    while (head.parentElement && !head.nextElementSibling
           && head.parentElement.tagName !== 'BODY')
      head = head.parentElement;
    const nodes = [];
    let sib = head.nextElementSibling;
    while (sib) {
      if (sib.tagName === 'BUTTON' && isHeader(sib)) break;
      if ([...sib.querySelectorAll('button')].some(isHeader)) break;
      nodes.push(sib);
      sib = sib.nextElementSibling;
    }
    return nodes;
  };
  const sectionInput = (label) => {
    const nodes = sectionNodes(label) || [];
    for (const n of nodes) {
      const inp = [...n.querySelectorAll('input')].find(i => i.getBoundingClientRect().width);
      if (inp) return inp;
    }
    return null;
  };
"""

_SECTION_TEXT = ("(label) => {" + _HELPERS +
                 "const nodes = sectionNodes(label); if (!nodes) return '';"
                 "return nodes.map(n => n.innerText || '').join(String.fromCharCode(10)); }")
_CLICK_HEADER = ("(label) => {" + _HELPERS +
                 "const b = headerOf(label); if (!b) return false; b.click(); return true; }")
_FOCUS_INPUT = ("(label) => {" + _HELPERS +
                "const inp = sectionInput(label); if (!inp) return 'no-input';"
                "inp.focus(); return 'ok:' + (inp.value || ''); }")
_INPUT_VALUE = ("(label) => {" + _HELPERS +
                "const inp = sectionInput(label); return inp ? inp.value : ''; }")

_SUGGESTIONS = ("() => [...document.querySelectorAll('[role=option], [role=listbox] *, li')]"
                ".filter(el => el.getBoundingClientRect().width && el.children.length <= 1)"
                ".map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 10)")


@dataclass(slots=True)
class SourceReport:
    """What the Source screen was told, for the row's detail line."""

    added_titles: list[str] = field(default_factory=list)
    refused_titles: list[str] = field(default_factory=list)
    added_skills: list[str] = field(default_factory=list)
    refused_skills: list[str] = field(default_factory=list)
    search_name: str = ""
    saved: bool = False

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.added_titles)} title(s)",
            f"{len(self.added_skills)} skill(s)",
        ]
        refused = len(self.refused_titles) + len(self.refused_skills)
        if refused:
            parts.append(f"{refused} refused by Loxo's taxonomy")
        parts.append(
            f"saved as {self.search_name!r}" if self.saved else "NOT saved"
        )
        return ", ".join(parts)


async def _expand(page: "Page", label: str) -> bool:
    for _ in range(3):
        state = await page.evaluate(_FOCUS_INPUT, label)
        if state.startswith("ok:"):
            return True
        await page.evaluate(_CLICK_HEADER, label)
        await page.wait_for_timeout(2_200)
    return (await page.evaluate(_FOCUS_INPUT, label)).startswith("ok:")


async def _add_chip(page: "Page", label: str, value: str, loose: bool) -> bool:
    state = await page.evaluate(_FOCUS_INPUT, label)
    if not state.startswith("ok:"):
        return False
    if state != "ok:":
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(300)
    await page.keyboard.type(value, delay=30)
    await page.wait_for_timeout(1_900)

    options = await page.evaluate(_SUGGESTIONS)
    target = next((o for o in options if o.lower() == value.lower()), None)
    if target is None and loose:
        target = next((o for o in options if o.lower().startswith(value.lower())), None)
    if target is None and loose:
        target = next(
            (o for o in options
             if value.lower() in o.lower() or o.lower() in value.lower()),
            None,
        )
    if target is not None:
        await page.get_by_text(target, exact=True).last.click(timeout=8_000)
    else:
        await page.keyboard.press("Enter")
    await page.wait_for_timeout(1_300)

    leftover = (await page.evaluate(_INPUT_VALUE, label)).strip()
    if leftover:
        await page.evaluate(_FOCUS_INPUT, label)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        return False
    committed = await page.evaluate(_SECTION_TEXT, label)
    return (target or value).lower() in committed.lower()


async def _fill_section(
    page: "Page", label: str, values: list[str], *, loose: bool
) -> tuple[list[str], list[str]]:
    if not await _expand(page, label):
        raise PlatformError(
            f"the Source screen's {label} section would not open, so its "
            "filters were not written. Loxo's panel may have changed; open the "
            "page by hand to see what it shows."
        )
    added: list[str] = []
    refused: list[str] = []
    for value in values:
        current = await page.evaluate(_SECTION_TEXT, label)
        if value.lower() in current.lower():
            continue
        ok = await _add_chip(page, label, value, loose)
        (added if ok else refused).append(value)
        log.info(
            "source chip", extra={"section": label, "value": value, "committed": ok}
        )
    return added, refused


async def _save_search(page: "Page", name: str) -> bool:
    """The bookmark-Save control, then its dialog. Same name = overwrite,
    which Loxo's own dialog states - that is what makes re-runs idempotent."""
    save = page.get_by_text("Save", exact=True).first
    if not await save.count():
        return False
    await save.click(timeout=10_000)
    await page.wait_for_timeout(2_500)
    name_box = page.locator(
        "[role=dialog] input:visible, [class*=Modal] input:visible"
    ).first
    if not await name_box.count():
        return False
    await name_box.fill(name, timeout=8_000)
    confirm = page.locator("[role=dialog], [class*=Modal]").get_by_text(
        "Save", exact=True
    ).last
    if not await confirm.count():
        return False
    await confirm.click(timeout=8_000)
    await page.wait_for_timeout(3_500)
    return True


async def configure_source(
    page: "Page",
    job_id: str,
    *,
    titles: list[str],
    skills: list[str],
    search_name: str,
    base_url: str = "https://app.loxo.co",
    agency_id: str = "28356",
) -> SourceReport:
    """Write the search filters onto a job's Source screen and save them.

    Titles commit on exact taxonomy matches only - loose matching in the Title
    box is how "SOC 2" once became a job title. Skills match loosely, because
    Loxo files AWS under "Amazon Web Services (AWS)".
    """
    report = SourceReport(search_name=search_name)
    await page.goto(
        f"{base_url}/agencies/{agency_id}/jobs/{job_id}/source",
        wait_until="domcontentloaded",
        timeout=90_000,
    )
    await page.wait_for_timeout(16_000)

    report.added_titles, report.refused_titles = await _fill_section(
        page, "Title", titles, loose=False
    )
    report.added_skills, report.refused_skills = await _fill_section(
        page, "Skills", skills, loose=True
    )
    report.saved = await _save_search(page, search_name)
    if not report.saved:
        raise PlatformError(
            "the Source filters were written but the search could not be "
            "saved, and unsaved filters vanish when the page closes. Open the "
            "job's Source screen and save the search by hand."
        )
    log.info(
        "source configured",
        extra={"job": job_id, "summary": report.summary},
    )
    return report
