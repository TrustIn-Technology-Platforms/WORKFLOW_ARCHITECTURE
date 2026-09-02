"""Loxo's Source screen: titles, skills, years and past companies, written and saved.

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

Two more sections, read out of Loxo's own bundle (2026-09-02) because the
session had expired before they could be opened live:

- **Years of Experience** is not a number box. It is a read-only input that
  opens a checklist of five bands - `<1`, `1-2`, `3-5`, `6-10`, `10+` (the
  bundle's `personExperienceFilter`, saved as `years_of_experience_ranges`) -
  and every ticked band becomes a chip. `experience_bands` maps a JD's "5+
  years" onto the bands to tick.
- **Company** holds two chip boxes, *Current Company* and *Past Company*, each
  with an "Include Subsidiaries" switch, joined by the same AND/OR control the
  Title section has (`companyNameFilter`). Both autocomplete against Loxo's
  company records and show name + domain per suggestion. The target-company
  list goes into **Past Company**: it is where a candidate has been that says
  they have done this before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

# Loxo's five experience bands, verbatim from its bundle: label, start, end.
# The boundaries sit at half-years because Loxo rounds a profile's total.
EXPERIENCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<1", 0.0, 1.0),
    ("1-2", 1.0, 2.5),
    ("3-5", 2.5, 5.5),
    ("6-10", 5.5, 10.5),
    ("10+", 10.5, 50.0),
)

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
  // The typeable inputs of a section, in document order. The "Include
  // Subsidiaries" switches are checkboxes and are not among them.
  const textInputs = (label) => (sectionNodes(label) || [])
      .flatMap(n => [...n.querySelectorAll('input')])
      .filter(i => i.getBoundingClientRect().width
                && !['checkbox','radio','hidden'].includes((i.getAttribute('type') || '').toLowerCase()));
  // A sub-heading inside a section ("Past Company"), as its leaf element.
  const sublabelEl = (label, sub) => {
    for (const n of (sectionNodes(label) || [])) {
      const hit = [...n.querySelectorAll('*')].find(el => el.children.length === 0
          && (el.innerText || '').trim() === sub && el.getBoundingClientRect().width);
      if (hit) return hit;
    }
    return null;
  };
  const inputAfter = (label, sub) => {
    const anchor = sublabelEl(label, sub);
    if (!anchor) return null;
    return textInputs(label).find(i =>
        anchor.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING) || null;
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

# The same three, for a named box inside a section (Company -> Past Company).
_FOCUS_INPUT_AFTER = ("([label, sub]) => {" + _HELPERS +
                      "const inp = inputAfter(label, sub); if (!inp) return 'no-input';"
                      "inp.focus(); return 'ok:' + (inp.value || ''); }")
_INPUT_VALUE_AFTER = ("([label, sub]) => {" + _HELPERS +
                      "const inp = inputAfter(label, sub); return inp ? inp.value : ''; }")
_SUBSECTION_TEXT = ("([label, sub]) => {" + _HELPERS +
                    "const nodes = sectionNodes(label); if (!nodes) return '';"
                    "const text = nodes.map(n => n.innerText || '').join(String.fromCharCode(10));"
                    "const at = text.indexOf(sub); return at < 0 ? '' : text.slice(at + sub.length); }")

# Mark a section's first typeable input so Playwright can give it a real click
# - the Years of Experience box is read-only and opens its list on focus/click,
# and a programmatic focus() does not reliably reach the app's handler.
_MARK_INPUT = ("(label) => {" + _HELPERS +
               "document.querySelectorAll('[data-lsw]').forEach(el => el.removeAttribute('data-lsw'));"
               "const inp = textInputs(label)[0] || sectionInput(label); if (!inp) return false;"
               "inp.setAttribute('data-lsw', '1'); return true; }")

_SUGGESTIONS = ("() => [...document.querySelectorAll('[role=option], [role=listbox] *, li')]"
                ".filter(el => el.getBoundingClientRect().width && el.children.length <= 1)"
                ".map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 10)")

# Mark the first visible dropdown entry whose first line is `text` and that is
# NOT inside the section itself - a chip already in the box carries the same
# text, and Loxo can list several records under one company name, so the
# best-ranked one (first) is the one to take.
_MARK_OPTION = ("([label, text]) => {" + _HELPERS +
                "document.querySelectorAll('[data-lsw-opt]').forEach(el => el.removeAttribute('data-lsw-opt'));"
                "const nodes = sectionNodes(label) || [];"
                "const inside = (el) => nodes.some(n => n.contains(el));"
                "const wanted = text.toLowerCase();"
                "const hit = [...document.querySelectorAll('[role=option], [role=listbox] *, li')]"
                "  .find(el => el.getBoundingClientRect().width && !inside(el)"
                "    && firstLine(el).toLowerCase() === wanted);"
                "if (!hit) return false; hit.setAttribute('data-lsw-opt', '1'); return true; }")


@dataclass(slots=True)
class SourceReport:
    """What the Source screen was told, for the row's detail line."""

    added_titles: list[str] = field(default_factory=list)
    refused_titles: list[str] = field(default_factory=list)
    added_skills: list[str] = field(default_factory=list)
    refused_skills: list[str] = field(default_factory=list)
    added_experience: list[str] = field(default_factory=list)
    missed_experience: list[str] = field(default_factory=list)
    added_companies: list[str] = field(default_factory=list)
    refused_companies: list[str] = field(default_factory=list)
    search_name: str = ""
    saved: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.added_titles)} title(s)",
            f"{len(self.added_skills)} skill(s)",
        ]
        if self.added_experience:
            parts.append(f"experience {'/'.join(self.added_experience)}")
        if self.added_companies:
            parts.append(f"{len(self.added_companies)} past company(ies)")
        refused = (
            len(self.refused_titles) + len(self.refused_skills)
            + len(self.refused_companies) + len(self.missed_experience)
        )
        if refused:
            parts.append(f"{refused} refused by Loxo's taxonomy")
        parts.append(
            f"saved as {self.search_name!r}" if self.saved else "NOT saved"
        )
        return ", ".join(parts)


def experience_bands(min_years: int | None, max_years: int | None) -> list[str]:
    """Loxo's bands to tick for a JD's years requirement.

    A band is ticked when its midpoint falls inside the requirement, so "5+
    years" ticks `6-10` and `10+` and leaves `3-5` (midpoint 4) alone: half of
    that band is people with three years. When no midpoint falls inside - a
    narrow "5 years exactly" - the band that holds the minimum is ticked, so
    the requirement is never silently dropped.
    """
    if min_years is None and max_years is None:
        return []
    low = float(min_years or 0)
    high = float(max_years) if max_years is not None else EXPERIENCE_BANDS[-1][2]
    if high < low:
        high = low
    chosen = [name for name, start, end in EXPERIENCE_BANDS if low <= (start + end) / 2 <= high]
    if not chosen:
        chosen = [name for name, start, end in EXPERIENCE_BANDS if start <= low < end]
    return chosen


def company_key(name: str) -> str:
    """A company name reduced to what identifies it.

    Loxo's suggestions carry the plain name ("Stripe"); the drafted list may
    say "Stripe, Inc.". Legal suffixes and punctuation are not identity.
    """
    text = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    text = re.sub(
        r"\b(?:inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|ag|sa|bv|pty|holdings)\b",
        " ",
        text,
    )
    return " ".join(text.split())


def match_company(value: str, options: list[str]) -> str | None:
    """The suggestion that names the same company as `value`, or None.

    Exact after normalisation only. "Axle" must never pick "Axle Logistics":
    a past-company filter on the wrong company finds the wrong people, and
    nobody would notice from the saved search's name.
    """
    wanted = company_key(value)
    if not wanted:
        return None
    for option in options:
        if company_key(option.split("\n")[0]) == wanted:
            return option.split("\n")[0].strip()
    return None


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


async def _click_option(page: "Page", label: str, text: str) -> bool:
    if not await page.evaluate(_MARK_OPTION, [label, text]):
        return False
    await page.locator("[data-lsw-opt='1']").first.click(timeout=8_000)
    return True


async def _fill_experience(page: "Page", bands: list[str]) -> tuple[list[str], list[str]]:
    """Tick Loxo's experience bands. The box is read-only: a click opens the
    checklist, each entry toggles, the list stays open between clicks, and
    Escape closes it. The chips are the proof, read after the list is shut so
    the open list's own labels cannot pass for them."""
    label = "Years of Experience"
    if not await _expand(page, label):
        raise PlatformError(
            "the Source screen's Years of Experience section would not open, "
            "so the experience bands were not written."
        )
    current = (await page.evaluate(_SECTION_TEXT, label)).lower()
    wanted = [band for band in bands if band.lower() not in current]
    if not wanted:
        return [band for band in bands], []

    if not await page.evaluate(_MARK_INPUT, label):
        raise PlatformError("the Years of Experience box was not found in its section.")
    await page.locator("[data-lsw='1']").first.click(timeout=8_000)
    await page.wait_for_timeout(1_200)

    clicked: list[str] = []
    for band in wanted:
        if await _click_option(page, label, band):
            clicked.append(band)
            await page.wait_for_timeout(700)
        else:
            log.info("experience band not offered", extra={"band": band})
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)

    after = (await page.evaluate(_SECTION_TEXT, label)).lower()
    added = [band for band in bands if band.lower() in after]
    missed = [band for band in bands if band not in added]
    for band in wanted:
        log.info(
            "source chip",
            extra={"section": label, "value": band, "committed": band in added},
        )
    return added, missed


async def _add_company(page: "Page", value: str) -> bool:
    label, sub = "Company", "Past Company"
    state = await page.evaluate(_FOCUS_INPUT_AFTER, [label, sub])
    if not state.startswith("ok:"):
        return False
    if state != "ok:":
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(300)
    await page.keyboard.type(value, delay=30)
    await page.wait_for_timeout(2_200)

    options = await page.evaluate(_SUGGESTIONS)
    target = match_company(value, options)
    picked = target is not None and await _click_option(page, label, target)
    if not picked:
        # Nothing Loxo offered names this company. Clear rather than commit a
        # guess - free text is not a company record.
        await page.keyboard.press("Escape")
        await page.evaluate(_FOCUS_INPUT_AFTER, [label, sub])
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(400)
        log.info(
            "company not offered",
            extra={"value": value, "offered": options[:6]},
        )
        return False
    await page.wait_for_timeout(1_300)

    leftover = (await page.evaluate(_INPUT_VALUE_AFTER, [label, sub])).strip()
    if leftover:
        await page.evaluate(_FOCUS_INPUT_AFTER, [label, sub])
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        return False
    committed = await page.evaluate(_SUBSECTION_TEXT, [label, sub])
    return company_key(target) in company_key(committed)


async def _fill_companies(page: "Page", companies: list[str]) -> tuple[list[str], list[str]]:
    """Past Company chips, one autocomplete round trip each."""
    label, sub = "Company", "Past Company"
    if not await _expand(page, label):
        raise PlatformError(
            "the Source screen's Company section would not open, so the past "
            "companies were not written."
        )
    if not (await page.evaluate(_FOCUS_INPUT_AFTER, [label, sub])).startswith("ok:"):
        raise PlatformError(
            "the Company section opened but has no 'Past Company' box - Loxo's "
            "panel may have changed. Open the page by hand to see what it shows."
        )
    added: list[str] = []
    refused: list[str] = []
    for value in companies:
        current = await page.evaluate(_SUBSECTION_TEXT, [label, sub])
        if company_key(value) and company_key(value) in company_key(current):
            continue
        ok = await _add_company(page, value)
        (added if ok else refused).append(value)
        log.info(
            "source chip", extra={"section": sub, "value": value, "committed": ok}
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
    years: tuple[int | None, int | None] = (None, None),
    companies: list[str] | None = None,
    base_url: str = "https://app.loxo.co",
    agency_id: str = "28356",
) -> SourceReport:
    """Write the search filters onto a job's Source screen and save them.

    Titles commit on exact taxonomy matches only - loose matching in the Title
    box is how "SOC 2" once became a job title. Skills match loosely, because
    Loxo files AWS under "Amazon Web Services (AWS)". Experience bands and past
    companies are written after them and, unlike them, a failure there is a
    warning on the report rather than a lost search: the titles and skills are
    what make the search worth saving.
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

    bands = experience_bands(*years)
    if bands:
        try:
            report.added_experience, report.missed_experience = await _fill_experience(page, bands)
        except PlatformError as exc:
            report.missed_experience = bands
            report.warnings.append(str(exc))

    if companies:
        try:
            report.added_companies, report.refused_companies = await _fill_companies(
                page, companies
            )
        except PlatformError as exc:
            report.refused_companies = list(companies)
            report.warnings.append(str(exc))

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
