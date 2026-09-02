"""Juicebox sourcing: create a project, run a JD search, set the filters.

The recruiter flow this automates, as Sohaib described it: create a new
project, name it, paste the JD (Juicebox's own AI builds a search from it and
names it), then fill in the filters its AI leaves thin - job titles, location,
skills. Every step below was proven live on the "ZZ TEST DELETE ME" project on
2026-09-02, including a save -> reload -> read-back round trip; match counts on
the test search went from 45k ("globally") to ~1k once the filters were real.

Hard-won facts this module encodes:

- The app paints blank for 20-30s and shows "Getting things ready" during big
  view transitions; every step waits for a text marker, never a fixed delay.
- "Create new project" creates instantly - no dialog - as a project literally
  named "New Project". It is renamed by double-clicking the title.
- "Job description" opens a modal (Paste JD / Upload JD); its own "Search"
  button builds the search and lands on /search?search_id=<id>.
- The filter editor is MUI. Section labels appear TWICE - in the editor's left
  nav (inside .MuiListItemButton-root) and as the real section heading; only
  the latter anchors anything. Chip entry is type -> ArrowDown -> Enter; the
  chips are plain <p> typography, NOT .MuiChip-root, so verification reads the
  section's text, bounded by the next section heading in document order.
- Filters persist only through "Save Changes"; the saved search then holds
  them across reloads. never screenshot this app with full_page=True - it
  blanks the virtualized view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

BASE = "https://app.juicebox.ai"

_CLICK_FILTERS = """() => {
  const btn = [...document.querySelectorAll('button, [role=button]')]
    .find(b => b.getBoundingClientRect().width
               && (b.innerText || '').trim().split(String.fromCharCode(10))[0].trim() === 'Filters');
  if (!btn) return false;
  btn.click();
  return true;
}"""

_HELPERS = """
  const sectionLeaf = (label) => [...document.querySelectorAll('p,div,span')]
    .find(el => (el.innerText || '').trim() === label
                && el.getBoundingClientRect().width > 0
                && el.querySelectorAll('*').length === 0
                && !el.closest('.MuiListItemButton-root'));
  const followUntil = (label, stops) => {
    const leaf = sectionLeaf(label);
    if (!leaf) return null;
    const all = [...document.querySelectorAll('*')];
    const start = all.indexOf(leaf);
    const out = [];
    for (let i = start + 1; i < all.length; i++) {
      const el = all[i];
      const text = (el.innerText || '').trim();
      if (stops.includes(text) && el.querySelectorAll('*').length <= 2
          && !el.closest('.MuiListItemButton-root')) break;
      out.push(el);
    }
    return out;
  };
"""

# Each section is read up to the next known section heading.
_STOPS = "['Power Filters', 'Past Job Titles', 'Past Locations', 'Companies', 'Timezone']"

_BLOCK_TEXT = ("(label) => {" + _HELPERS +
               "const w = followUntil(label, " + _STOPS + ");"
               "if (!w) return '';"
               "return w.filter(el => el.tagName === 'P')"
               ".map(el => (el.innerText || '').trim()).join(String.fromCharCode(10)); }")

_MARK = ("(label) => {" + _HELPERS +
         "document.querySelectorAll('[data-jbw]').forEach(el => el.removeAttribute('data-jbw'));"
         "const w = followUntil(label, " + _STOPS + ");"
         "if (!w) return 'no-window';"
         "const inp = w.find(el => el.tagName === 'INPUT'"
         " && (el.className || '').includes('MuiInputBase-input')"
         " && el.getBoundingClientRect().width);"
         "if (!inp) return 'no-input';"
         "inp.setAttribute('data-jbw', '1'); inp.scrollIntoView({block: 'center'});"
         "return 'ok'; }")

_POPPER = ("() => [...document.querySelectorAll('.MuiAutocomplete-popper li, [role=option]')]"
           ".filter(el => el.getBoundingClientRect().width)"
           ".map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 10)")

_SAVE = """() => {
  const btn = [...document.querySelectorAll('button')]
    .find(b => (b.innerText || '').trim() === 'Save Changes'
               && b.getBoundingClientRect().width);
  if (!btn) return false;
  btn.click();
  return true;
}"""


@dataclass(slots=True)
class SourcingReport:
    """What the sourcing setup produced, for the row's detail line."""

    project_url: str = ""
    search_url: str = ""
    added: dict[str, list[str]] = field(default_factory=dict)
    refused: dict[str, list[str]] = field(default_factory=dict)
    saved: bool = False

    @property
    def summary(self) -> str:
        counts = ", ".join(f"{len(v)} {k}" for k, v in self.added.items() if v)
        refused = sum(len(v) for v in self.refused.values())
        parts = [counts or "nothing added"]
        if refused:
            parts.append(f"{refused} refused")
        parts.append("saved" if self.saved else "NOT saved")
        return ", ".join(parts)


async def _settled(page: "Page", marker: str, tries: int = 45) -> bool:
    for _ in range(tries):
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if marker in text:
            return True
        await page.wait_for_timeout(2_000)
    return False


async def _either(page: "Page", markers: tuple[str, ...], tries: int = 60) -> str:
    for _ in range(tries):
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        for marker in markers:
            if marker in text:
                return marker
        await page.wait_for_timeout(2_000)
    return ""


async def _open_editor(page: "Page") -> None:
    """A fresh search shows the review screen ("Edit filters" link, "Run
    search" button); a run one shows results ("Matches", a "Filters" button).
    Both lead to the same editor."""
    state = await _either(page, ("Matches", "Run search"))
    if not state:
        raise PlatformError("the search page never rendered its review or results view.")
    await page.wait_for_timeout(4_000)
    if state == "Matches":
        await page.evaluate(_CLICK_FILTERS)
    else:
        await page.get_by_text("Edit filters", exact=True).first.click(timeout=15_000)
    if not await _settled(page, "Edit Your Search Filters", 45):
        raise PlatformError("the filter editor never opened.")
    await page.wait_for_timeout(4_000)


async def create_project(page: "Page", name: str) -> str:
    """A new project, renamed. Returns its URL.

    "Create new project" creates instantly with no dialog, so the newest
    "New Project" row is opened and renamed by double-clicking the title.
    """
    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90_000)
    if not await _settled(page, "Projects"):
        raise PlatformError("Juicebox never finished loading its shell.")
    await page.get_by_text("Projects", exact=True).first.click(timeout=20_000)
    if not await _settled(page, "Create new project", 30):
        raise PlatformError("Juicebox's Projects page never rendered.")
    await page.wait_for_timeout(2_000)
    await page.get_by_text("Create new project", exact=True).first.click(timeout=15_000)
    await page.wait_for_timeout(6_000)

    row = page.get_by_text("New Project", exact=True).first
    if not await row.count():
        raise PlatformError(
            "Juicebox created no 'New Project' row - its create flow may have "
            "changed. Create the project by hand and pass its URL."
        )
    await row.click(timeout=15_000)
    await page.wait_for_timeout(8_000)
    if "/project/" not in page.url:
        raise PlatformError(f"opening the new project landed on {page.url}")

    title = page.get_by_text("New Project", exact=True).first
    await title.dblclick(timeout=10_000)
    await page.wait_for_timeout(1_500)
    editor = page.locator("input:visible").first
    if "New Project" in (await editor.input_value() if await editor.count() else ""):
        await editor.fill(name)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3_000)
    log.info("juicebox project created", extra={"name": name, "url": page.url})
    return page.url.split("?")[0]


async def jd_search(page: "Page", project_url: str, jd: str) -> str:
    """Paste the JD; Juicebox's AI builds and names a search. Returns its URL."""
    await page.goto(project_url, wait_until="domcontentloaded", timeout=90_000)
    if not await _settled(page, "Job description"):
        raise PlatformError("the project page never offered the Job description search.")
    await page.wait_for_timeout(3_000)
    await page.get_by_text("Job description", exact=True).first.click(timeout=15_000)
    await page.wait_for_timeout(2_500)

    dialog = page.locator("[role=dialog]").last
    area = dialog.locator("textarea").first
    if not await area.count():
        raise PlatformError("the Paste JD dialog offered no text area.")
    await area.click(timeout=10_000)
    await area.fill(jd, timeout=15_000)
    await page.wait_for_timeout(1_200)
    await dialog.get_by_text("Search", exact=True).last.click(timeout=10_000)

    for _ in range(45):
        if "search_id=" in page.url:
            break
        await page.wait_for_timeout(2_000)
    if "search_id=" not in page.url:
        raise PlatformError("the JD search never produced a search page.")
    if not await _settled(page, "Filters", 45):
        raise PlatformError("the search page never finished building.")
    log.info("juicebox jd search built", extra={"url": page.url})
    return page.url


async def _add_chip(page: "Page", label: str, value: str) -> bool:
    state = await page.evaluate(_MARK, label)
    if state != "ok":
        log.info("juicebox filter input missing",
                 extra={"section": label, "state": state})
        return False
    box = page.locator("[data-jbw='1']")
    await box.focus(timeout=10_000)
    await page.keyboard.type(value, delay=40)
    await page.wait_for_timeout(2_200)
    options = await page.evaluate(_POPPER)
    if options:
        index = next((i for i, o in enumerate(options)
                      if o.lower() == value.lower() or value.lower() in o.lower()), 0)
        for _ in range(index + 1):
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(150)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1_600)
    block = await page.evaluate(_BLOCK_TEXT, label)
    committed = value.lower() in block.lower()
    if not committed and await page.evaluate(_MARK, label) == "ok":
        await page.locator("[data-jbw='1']").focus()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
    return committed


async def configure_filters(
    page: "Page",
    search_url: str,
    *,
    titles: list[str],
    skills: list[str],
    location: str | None,
) -> SourcingReport:
    """Open the search's filter editor, add what is missing, save, verify."""
    report = SourcingReport(search_url=search_url)
    await page.goto(search_url, wait_until="domcontentloaded", timeout=90_000)
    await _open_editor(page)

    sections = [("Job Titles", titles), ("Skills or Keywords", skills)]
    if location:
        sections.insert(1, ("Location(s)", [location]))
    for label, values in sections:
        added: list[str] = []
        refused: list[str] = []
        for value in values:
            current = await page.evaluate(_BLOCK_TEXT, label)
            if value.lower() in current.lower():
                continue
            ok = await _add_chip(page, label, value)
            (added if ok else refused).append(value)
            log.info("juicebox chip",
                     extra={"section": label, "value": value, "committed": ok})
        report.added[label] = added
        report.refused[label] = refused

    report.saved = bool(await page.evaluate(_SAVE))
    await page.wait_for_timeout(12_000)
    if not report.saved:
        raise PlatformError(
            "the filters were written but Save Changes was not found - unsaved "
            "filters are lost when the editor closes. Open the search and save "
            "by hand."
        )

    # A fresh search still sits on its review screen: run it, so the saved
    # search is live at the yes/no stage rather than parked.
    run = page.get_by_text("Run search", exact=True).first
    if await run.count():
        await run.click(timeout=15_000)
        await _settled(page, "Matches", 60)
        await page.wait_for_timeout(6_000)

    # The proof: reload and read the sections back.
    await page.goto(search_url, wait_until="domcontentloaded", timeout=90_000)
    await _open_editor(page)
    for label, values in sections:
        block = (await page.evaluate(_BLOCK_TEXT, label)).lower()
        lost = [v for v in values if v.lower() not in block
                and v not in report.refused.get(label, [])]
        if lost:
            report.refused.setdefault(label, []).extend(lost)
            log.warning("juicebox filters lost on reload",
                        extra={"section": label, "lost": lost})
    log.info("juicebox sourcing configured", extra={"summary": report.summary})
    return report


async def set_up_sourcing(
    page: "Page",
    *,
    project_name: str,
    jd: str,
    titles: list[str],
    skills: list[str],
    location: str | None,
) -> SourcingReport:
    """The whole flow: project -> JD search -> filters -> saved."""
    project_url = await create_project(page, project_name)
    search_url = await jd_search(page, project_url, jd)
    report = await configure_filters(
        page, search_url, titles=titles, skills=skills, location=location
    )
    report.project_url = project_url
    return report
