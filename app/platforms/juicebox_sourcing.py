"""Juicebox sourcing: create or open a project, run a JD search, set the filters.

The recruiter flow this automates, as Sohaib described it: create a new
project and name it, press **Job description**, paste the JD (Juicebox's own AI
builds a search from it and names it), press Search and wait - the search opens
in the same tab, in a new tab, or not at all until its title is clicked in the
rail - then fill in the filters its AI leaves thin: job titles, location,
skills, years of experience. Save Changes, Run search. Every step was proven
live on throwaway projects on 2026-09-02, including a save -> reload -> read-back
round trip; match counts on the first test search went from 45k ("globally") to
~1k once the filters were real.

Why the first production run (Axle, 2026-09-02) stopped at a renamed, empty
project: `create_project` logged the new name as `extra={"name": ...}`, and
`name` is a field Python's logging reserves, so the log call itself raised, the
adapter swallowed it as a warning, and the JD paste never ran. The sequence
driver made the same mistake on 2026-08-28. tests/test_logging_conf.py now
sweeps every `extra=` in the codebase for reserved keys.

Hard-won facts this module encodes:

- The app paints blank for 20-30s and shows "Getting things ready" during big
  view transitions; every step waits for a text marker, never a fixed delay.
- "Create new project" creates instantly - no dialog - as a project literally
  named "New Project". It is renamed by double-clicking the title.
- "Job description" opens a modal headed "Search by job description" (Paste JD
  / Upload JD); its own "Search" button builds the search and, when it works as
  observed, lands the same tab on /search?search_id=<id>. A popup and the rail
  are handled too, because that is how a person reaches it when it does not.
- The filter editor is MUI. Section labels appear TWICE - in the editor's left
  nav (inside .MuiListItemButton-root) and as the real section heading; only
  the latter anchors anything. Chip entry is type -> ArrowDown -> Enter; the
  chips are plain <p> typography, NOT .MuiChip-root, so verification reads the
  section's text, bounded by the next section heading in document order.
  The experience fields are ordinary text inputs ("Example: 5 years").
- Filters persist only through "Save Changes"; the saved search then holds
  them across reloads. Never screenshot this app with full_page=True - it
  blanks the virtualized view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

BASE = "https://app.juicebox.ai"
# The Projects list's own address, the fallback when the rail link is dead.
PROJECTS_URL = BASE + "/projects"

# The editor's section headings this module writes, verbatim.
JOB_TITLES = "Job Titles"
LOCATIONS = "Location(s)"
SKILLS = "Skills or Keywords"
MIN_YEARS = "Min Experience (Years)"
MAX_YEARS = "Max Experience (Years)"
COMPANIES = "Companies"
STAGES = "Company Funding Stages"

# Juicebox's funding-stage keys, in order, as its hidden select value spells
# them ("seed,series_a,series_b,series_c" was read off a live search).
_SERIES_ORDER = ["seed"] + [f"series_{letter}" for letter in "abcdefghij"]

_PROJECT_PATH = re.compile(r"(/project/[A-Za-z0-9_-]+)")
_SEARCH_PATH = re.compile(r"/search/[A-Za-z0-9_-]+")

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

# Each section is read up to the next known heading. The two experience inputs
# sit side by side under General, so the Max heading bounds the Min window.
_STOPS = ("['Power Filters', 'Past Job Titles', 'Past Locations', 'Companies', 'Timezone',"
          " 'Max Experience (Years)', 'Required Contact Info', 'Excluded Companies',"
          " 'Estimated Revenue']")

# A section's chips are `<p>` typography in most sections, bare `<text>` nodes
# in Companies, and `span.MuiChip-label` in Company Funding Stages - all three
# are read, so a chip that landed is never reported as refused for want of
# looking (the first Companies run reported all 18 of its chips refused).
_BLOCK_TEXT = ("(label) => {" + _HELPERS +
               "const w = followUntil(label, " + _STOPS + ");"
               "if (!w) return '';"
               "return w.filter(el => el.tagName === 'P'"
               " || (el.querySelectorAll('*').length === 0 && (el.tagName === 'TEXT'"
               " || (el.tagName === 'SPAN' && (el.className || '').toString().includes('MuiChip-label')))))"
               ".map(el => (el.innerText || '').trim()).filter(Boolean).join(String.fromCharCode(10)); }")

_FIND_INPUT = ("const inp = w.find(el => el.tagName === 'INPUT'"
               " && (el.className || '').includes('MuiInputBase-input')"
               " && el.getBoundingClientRect().width);")

_MARK = ("(label) => {" + _HELPERS +
         "document.querySelectorAll('[data-jbw]').forEach(el => el.removeAttribute('data-jbw'));"
         "const w = followUntil(label, " + _STOPS + ");"
         "if (!w) return 'no-window';" + _FIND_INPUT +
         "if (!inp) return 'no-input';"
         "inp.setAttribute('data-jbw', '1'); inp.scrollIntoView({block: 'center'});"
         "return 'ok'; }")

_READ_INPUT = ("(label) => {" + _HELPERS +
               "const w = followUntil(label, " + _STOPS + ");"
               "if (!w) return '';" + _FIND_INPUT +
               "return inp ? String(inp.value || '') : ''; }")

_POPPER = ("() => [...document.querySelectorAll('.MuiAutocomplete-popper li, [role=option]')]"
           ".filter(el => el.getBoundingClientRect().width)"
           ".map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 10)")

# Company Funding Stages is a MUI multi-select, not an autocomplete: a hidden
# native input holds the chosen keys comma-joined, and its sibling combobox
# opens a listbox of options carrying `data-value`. The section holds two such
# selects - "Current + Past" first - so the stages one is the last.
_MARK_STAGE_SELECT = ("() => {" + _HELPERS +
    "document.querySelectorAll('[data-jbw]').forEach(el => el.removeAttribute('data-jbw'));"
    "const w = followUntil('Company Funding Stages', ['Estimated Revenue']);"
    "if (!w) return {state: 'no-window'};"
    "const natives = w.filter(el => el.tagName === 'INPUT'"
    " && (el.className || '').includes('MuiSelect-nativeInput'));"
    "const native = natives[natives.length - 1];"
    "if (!native) return {state: 'no-select'};"
    "const root = native.parentElement;"
    "const display = root && (root.querySelector('[role=combobox]')"
    " || root.querySelector('.MuiSelect-select'));"
    "if (!display) return {state: 'no-display'};"
    "display.setAttribute('data-jbw', '1'); display.scrollIntoView({block: 'center'});"
    "return {state: 'ok', value: native.value || ''}; }")

_STAGE_OPTIONS = ("() => [...document.querySelectorAll('ul[role=listbox] li[role=option]')]"
                  ".filter(el => el.getBoundingClientRect().width)"
                  ".map(el => ({key: el.getAttribute('data-value') || '',"
                  " label: (el.innerText || '').trim(),"
                  " selected: el.getAttribute('aria-selected') === 'true'}))")

_SAVE = """() => {
  const btn = [...document.querySelectorAll('button')]
    .find(b => (b.innerText || '').trim() === 'Save Changes'
               && b.getBoundingClientRect().width);
  if (!btn) return false;
  btn.click();
  return true;
}"""

# The rail under "Current project" reads: Create Agent, Searches (N), New search,
# <each search title>, Create intake, Shortlist. The first title after "New
# search" is the newest search - the one the JD paste just built.
_CLICK_RAIL_SEARCH = """() => {
  const leaves = [...document.querySelectorAll('p,div,span,a,li')]
    .filter(el => el.getBoundingClientRect().width && el.querySelectorAll('*').length === 0);
  const start = leaves.findIndex(el => (el.innerText || '').trim().startsWith('Searches ('));
  if (start < 0) return null;
  for (let i = start + 1; i < leaves.length; i++) {
    const text = (leaves[i].innerText || '').trim();
    if (!text || text === 'New search') continue;
    if (['Create intake', 'Shortlist', 'Create Agent'].includes(text)) return null;
    leaves[i].click();
    return text;
  }
  return null;
}"""

_BODY_TEXT = "() => document.body ? document.body.innerText : ''"


@dataclass(slots=True)
class SourcingReport:
    """What the sourcing setup produced, for the row's detail line."""

    project_url: str = ""
    project_created: bool = False
    search_url: str = ""
    added: dict[str, list[str]] = field(default_factory=dict)
    refused: dict[str, list[str]] = field(default_factory=dict)
    # The funding-stage keys the select held after the save, e.g.
    # ["seed", "series_a"]; empty when the stage was unknown and left alone.
    stage_keys: list[str] = field(default_factory=list)
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


# ----------------------------------------------------------------------
# pure helpers
# ----------------------------------------------------------------------


def project_home(url: str) -> str:
    """The project page that offers the JD search, from any URL inside it.

    A recruiter pastes whatever is in the address bar - the sequences list, a
    search, the home - and all of them carry the same /project/<id> segment.
    """
    match = _PROJECT_PATH.search(url or "")
    if not match:
        return (url or "").split("?")[0]
    return f"{BASE}{match.group(1)}/home"


def is_search_url(url: str) -> bool:
    return "search_id=" in (url or "") or bool(_SEARCH_PATH.search(url or ""))


_LOCATION_NOISE = {"hybrid", "remote", "onsite", "on-site", "on site", "in office", "in-office"}


def split_locations(value: str | None) -> list[str]:
    """One chip per place.

    Juicebox's location box takes one place at a time, and the row's column is
    written for people: "NY, ATL", "New York / Atlanta", "Manchester (hybrid)".
    Working arrangements are not places, so they are dropped.
    """
    if not value:
        return []
    text = re.sub(r"\([^)]*\)", " ", value)
    parts = re.split(r"\s*(?:/|,|;|\||\bor\b|\band\b|&)\s*", text)
    seen: set[str] = set()
    places: list[str] = []
    for part in parts:
        place = " ".join(part.split()).strip(" -")
        if not place or place.lower() in _LOCATION_NOISE or place.lower() in seen:
            continue
        seen.add(place.lower())
        places.append(place)
    return places


def years_span(lo: int | None, hi: int | None) -> str:
    """The experience range as a person would say it, or nothing."""
    if lo is None and hi is None:
        return ""
    if hi is None:
        return f"{lo}+ years"
    if lo is None:
        return f"up to {hi} years"
    return f"{lo}-{hi} years"


_LEGAL_SUFFIXES = re.compile(
    r"\b(?:inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|ag|sa|bv|pty|holdings)\b"
)


def _name_key(name: str) -> str:
    """A company name reduced to what identifies it: "Stripe, Inc." is Stripe."""
    text = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(_LEGAL_SUFFIXES.sub(" ", text).split())


def _compact(name: str) -> str:
    return _name_key(name).replace(" ", "")


def _domain_label(option: str) -> str:
    """The first label of the domain an option shows on its second line:
    "Boost\\nboostinsurance.com" -> "boostinsurance". Empty when there is none."""
    lines = [line.strip() for line in (option or "").strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    host = re.sub(r"^https?://", "", lines[1].lower()).split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0] if "." in host else ""


def same_company(value: str, option: str) -> bool:
    """Does an autocomplete option name the drafted company?

    By name first, legal suffixes aside. Then by domain: Juicebox lists some
    companies under a short name with the full name in the domain - "Boost /
    boostinsurance.com" is Boost Insurance, "Method / methodfi.com" is Method
    Financial (both refused on 2026-09-03). The domain's first label must be a
    prefix of the drafted name, start with the option's own name, and carry
    more of the name than the option shows - so "United Nations / un.org" is
    not Unit, "Alloy Automation / alloy.com" is not Alloy, and "Stripe /
    stripe.com" is not Stripe Olt.
    """
    head = (option.strip().splitlines() or [""])[0]
    if _name_key(head) == _name_key(value):
        return True
    wanted, shown, label = _compact(value), _compact(head), _domain_label(option)
    return (
        len(label) >= 4
        and bool(shown)
        and len(label) > len(shown)
        and wanted.startswith(label)
        and label.startswith(shown)
    )


def match_mode(label: str) -> str:
    """How a section's values are matched against Juicebox's suggestions."""
    if label == COMPANIES:
        return "exact"
    if label == LOCATIONS:
        return "token"
    return "loose"


def pick_option(options: list[str], value: str, *, mode: str = "loose") -> int | None:
    """Which autocomplete option is `value`.

    Juicebox's first suggestion is often `Ask AI for "<value>"`, which contains
    the value and is never the answer. An option's first line is its name (the
    second is a domain or a category tag).

    - `exact` (companies): the name must match exactly, legal suffixes and
      punctuation aside, because the nearest name is a different company - the
      first live run turned "Unit" into United Nations, "Sure" into Sureskills
      and "Ascend" into Ascendion.
    - `token` (locations): the name matches, or the value appears in the option
      as a whole word - "NY" is in "New York, NY, United States" and not in
      "Nyack", which is what a start-of-name rule picked on 2026-09-03.
    - `loose` (titles, skills): an exact name, then one starting with the
      value, then one containing it, then the first real suggestion - what a
      person does with an autocomplete.
    """
    wanted = " ".join(value.split()).lower()
    heads = [
        (option.strip().splitlines() or [""])[0].strip().lower() for option in options
    ]
    real = [i for i, head in enumerate(heads) if not head.startswith("ask ai")]
    if mode == "exact":
        return next((i for i in real if same_company(value, options[i])), None)
    for index in real:
        if heads[index] == wanted:
            return index
    if mode == "token":
        word = re.compile(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])")
        return next((i for i in real if word.search(options[i].lower())), None)
    for index in real:
        if heads[index].startswith(wanted):
            return index
    for index in real:
        if wanted in options[index].lower():
            return index
    return real[0] if real else None


def present(block: str, value: str, *, mode: str = "loose") -> bool:
    """Is `value` already a chip in a section's text?

    Exact (companies): a chip whose name is the value, no more - "Unit" is not
    present because "United Nations" is. Otherwise the value appears anywhere,
    which is how "ATL" is found inside "Atlanta".
    """
    if mode == "exact":
        key = _name_key(value)
        return any(_name_key(line) == key for line in block.splitlines() if line.strip())
    return value.lower() in block.lower()


def stage_key(stage: str | None) -> str | None:
    """A funding stage as Juicebox keys it, or None when it is not a stage.

    Accepts the drafter's labels ("Series B", "Pre-seed", "Growth stage",
    "Public") and Juicebox's own option labels. "Growth"/"Late stage" is read
    as Series D, "Early stage" as Series A; "Bootstrapped" and "Unknown" are
    not funding stages, so nothing is selected for them.
    """
    text = (stage or "").strip().lower().replace("-", " ").replace("_", " ")
    text = " ".join(text.split())
    if not text or text == "unknown":
        return None
    if text.startswith("pre seed"):
        return "pre_seed"
    if text.startswith("seed"):
        return "seed"
    match = re.match(r"series\s*([a-j])\b", text)
    if match:
        return f"series_{match.group(1)}"
    if text in {"public", "ipo", "publicly traded", "publicly listed", "post ipo"}:
        return "ipo"
    if "growth" in text or "late" in text:
        return "series_d"
    if "early" in text:
        return "series_a"
    return None


def stages_up_to(stage: str | None, available: list[str]) -> list[str]:
    """Every stage from Seed up to and including the client's own.

    Sohaib's rule (2026-09-02): a role at a Series C company should take
    people who worked at Seed, Series A, Series B and Series C companies -
    the stages the client has been through, not the ones ahead of it. The
    result is limited to what Juicebox's menu offers, in stage order.
    """
    key = stage_key(stage)
    if key is None:
        return []
    if key == "pre_seed":
        wanted = ["pre_seed"]
    elif key == "ipo":
        wanted = _SERIES_ORDER + ["ipo"]
    else:
        wanted = _SERIES_ORDER[: _SERIES_ORDER.index(key) + 1]
    return [k for k in wanted if k in available]


ALL_STAGE_KEYS = ["pre_seed", *_SERIES_ORDER, "ipo"]


def stage_plan(stage: str | None) -> list[str]:
    """The stages a run will try to select, before seeing Juicebox's menu."""
    return stages_up_to(stage, ALL_STAGE_KEYS)


def new_lines(before: str, after: str) -> list[str]:
    """Section lines that appeared between two reads - the chip that landed.

    Juicebox labels a chip its own way ("NY" typed lands as "New York" under a
    REGION tag), so the text typed is the wrong thing to look for. Multiplicity
    counts: a second "New York" chip is a new line even though the text is not.
    """
    from collections import Counter

    seen = Counter(line.strip() for line in before.splitlines() if line.strip())
    gained: list[str] = []
    for line in after.splitlines():
        text = line.strip()
        if not text:
            continue
        if seen[text] > 0:
            seen[text] -= 1
            continue
        gained.append(text)
    return gained


def chip_label(gained: list[str], value: str) -> str:
    """The chip's label among the lines a section gained: the typed value when
    it is there, else the last line that is not an all-caps category tag
    (CITY, REGION), else the last line."""
    for line in gained:
        if line.lower() == value.lower():
            return line
    named = [line for line in gained if not line.isupper()]
    return (named or gained)[-1]


def _landed(chosen: str, block: str) -> bool:
    """Did the autocomplete option we picked show up as a chip?

    The chip is not always the text typed: "NY" lands as "New York". So the
    option's own label - its first line, up to the first comma - is checked
    against the section as well.
    """
    head = (chosen or "").strip().splitlines()[0] if (chosen or "").strip() else ""
    head = head.split(",")[0].strip().lower()
    return bool(head) and head in block


# ----------------------------------------------------------------------
# waiting
# ----------------------------------------------------------------------


async def _settled(page: "Page", marker: str, tries: int = 45) -> bool:
    for _ in range(tries):
        text = await page.evaluate(_BODY_TEXT)
        if marker in text:
            return True
        await page.wait_for_timeout(2_000)
    return False


async def _either(page: "Page", markers: tuple[str, ...], tries: int = 60) -> str:
    for _ in range(tries):
        text = await page.evaluate(_BODY_TEXT)
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


# ----------------------------------------------------------------------
# the project
# ----------------------------------------------------------------------


async def _go_to_projects(page: "Page") -> None:
    """Land on the Projects list, where "Create new project" lives.

    The rail paints before React has bound its handlers, so on a fast machine
    a single click on "Projects" lands on a dead link and the page stays on
    Home (headed local run, 2026-09-02; the slower Railway container never
    saw it). So: settle, dismiss the cookie banner, then click and check, and
    click again until the list renders, before falling back to its address.
    """
    from app.platforms.juicebox_criteria import DISMISS_COOKIES

    await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=90_000)
    if not await _settled(page, "Projects"):
        raise PlatformError("Juicebox never finished loading its shell.")
    try:
        await page.evaluate(DISMISS_COOKIES)
    except Exception:
        pass
    for _ in range(5):
        await page.wait_for_timeout(2_500)
        for locator in (
            page.get_by_role("link", name="Projects", exact=True).first,
            page.get_by_text("Projects", exact=True).first,
        ):
            try:
                await locator.click(timeout=5_000, no_wait_after=True)
                break
            except Exception:
                continue
        if await _settled(page, "Create new project", 6):
            return
    await page.goto(PROJECTS_URL, wait_until="domcontentloaded", timeout=90_000)
    if await _settled(page, "Create new project", 15):
        return
    raise PlatformError("Juicebox's Projects page never rendered.")


async def create_project(page: "Page", name: str) -> str:
    """A new project, renamed. Returns its URL.

    "Create new project" creates instantly with no dialog, so the newest
    "New Project" row is opened and renamed by double-clicking the title.
    """
    await _go_to_projects(page)
    await page.wait_for_timeout(2_000)
    await page.get_by_text("Create new project", exact=True).first.click(timeout=15_000)
    await page.wait_for_timeout(6_000)

    row = page.get_by_text("New Project", exact=True).first
    if not await row.count():
        raise PlatformError(
            "Juicebox created no 'New Project' row - its create flow may have "
            "changed. Create the project by hand and put its URL in the row's "
            "Juicebox Project column."
        )
    await row.click(timeout=15_000)
    await page.wait_for_timeout(8_000)
    if "/project/" not in page.url:
        raise PlatformError(f"opening the new project landed on {page.url}")

    # The rename: double-click the title, wait for the inline input (the
    # Railway container takes longer than 1.5s to show it - the 2026-09-03
    # run left a project called "New Project"), type, Enter, check, retry.
    for attempt in range(3):
        title = page.get_by_text("New Project", exact=True).first
        try:
            await title.dblclick(timeout=10_000)
        except Exception:
            break
        editor = None
        for _ in range(8):
            await page.wait_for_timeout(1_000)
            candidate = page.locator("input:visible").first
            if await candidate.count() and "New Project" in await candidate.input_value():
                editor = candidate
                break
        if editor is None:
            continue
        await editor.fill(name)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3_000)
        if name in await page.evaluate(_BODY_TEXT):
            break
        log.info("juicebox project rename retry", extra={"project": name, "attempt": attempt + 1})
    if name not in await page.evaluate(_BODY_TEXT):
        # Twice on the server (2026-09-03) and never locally - so leave the
        # evidence a laptop cannot reproduce: what inputs the page had, and a
        # screenshot in the artifact dir (pulled with scripts/pull_artifacts.py).
        inputs = await page.evaluate(
            "() => [...document.querySelectorAll('input')]"
            ".filter(el => el.getBoundingClientRect().width)"
            ".map(el => (el.getAttribute('placeholder') || el.value || el.type || '?').slice(0, 40))"
        )
        log.warning(
            "juicebox project rename did not stick",
            extra={"project": name, "url": page.url, "inputs": inputs[:10]},
        )
        try:
            from datetime import datetime, timezone

            from app.config import get_settings

            shots = Path(get_settings().artifact_dir)
            shots.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            await page.screenshot(path=str(shots / f"{stamp}-juicebox-rename-failed.png"))
        except Exception:  # noqa: BLE001 - evidence is best effort
            pass
    # `project`, not `name`: `name` is a reserved LogRecord field, and a log
    # call raising here is exactly what stopped the first production run.
    log.info("juicebox project created", extra={"project": name, "url": page.url})
    return page.url.split("?")[0]


async def open_project(page: "Page", project_url: str) -> str:
    """Land on a project's home, where the JD search lives. Returns the URL used."""
    home = project_home(project_url)
    for candidate in dict.fromkeys((home, project_url.split("?")[0])):
        await page.goto(candidate, wait_until="domcontentloaded", timeout=90_000)
        if await _settled(page, "Job description", 30):
            return candidate
    raise PlatformError(
        f"{project_url} never offered the Job description search. Is it a "
        "Juicebox project URL (app.juicebox.ai/project/<id>/...)?"
    )


# ----------------------------------------------------------------------
# the search
# ----------------------------------------------------------------------


async def jd_search(page: "Page", project_url: str, jd: str) -> str:
    """Paste the JD; Juicebox's AI builds and names a search. Returns its URL."""
    await open_project(page, project_url)
    await page.wait_for_timeout(3_000)
    await page.get_by_text("Job description", exact=True).first.click(timeout=15_000)

    dialog = page.locator("[role=dialog]").filter(has_text="Search by job description").last
    area = dialog.locator("textarea").first
    try:
        await area.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        raise PlatformError(
            "the 'Search by job description' dialog did not open, or offered no "
            "text area to paste into."
        ) from exc
    await area.click(timeout=10_000)
    await area.fill(jd, timeout=15_000)
    await page.wait_for_timeout(1_200)

    # The search may open in this tab, in a new one, or only in the rail - so
    # watch for new pages from before the click until the search is found.
    popups: list["Page"] = []

    # A plain function: Playwright wraps event handlers and cannot wrap a
    # bound builtin like `popups.append` (AttributeError on `_pw_impl_instance_`,
    # the 2026-09-02 headed run).
    def on_page(opened: "Page") -> None:
        popups.append(opened)

    context = page.context
    context.on("page", on_page)
    try:
        search_button = dialog.get_by_role("button", name="Search", exact=True).last
        if not await search_button.count():
            search_button = dialog.get_by_text("Search", exact=True).last
        await search_button.click(timeout=10_000)
        url = await _await_search(page, popups)
    finally:
        context.remove_listener("page", on_page)

    if not await _settled(page, "Filters", 45):
        raise PlatformError("the search page never finished building.")
    log.info("juicebox jd search built", extra={"url": url})
    return url


async def _await_search(page: "Page", popups: list["Page"], *, tries: int = 45) -> str:
    """The URL of the search the JD paste built, however Juicebox showed it.

    Same tab first (what the proving runs saw), then a popup, and after ~20s of
    neither, the newest title under "Searches (N)" in the rail - the click a
    person makes when nothing opens on its own.
    """
    rail_clicked = False
    for attempt in range(tries):
        if is_search_url(page.url):
            return page.url
        for popup in list(popups):
            url = popup.url
            if not is_search_url(url):
                continue
            log.info("juicebox search opened in a new tab", extra={"url": url})
            popups.remove(popup)
            try:
                await popup.close()
            except Exception:
                pass
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            return url
        if attempt >= 10 and not rail_clicked:
            title = await page.evaluate(_CLICK_RAIL_SEARCH)
            if title:
                rail_clicked = True
                log.info("juicebox search opened from the rail", extra={"title": title})
        await page.wait_for_timeout(2_000)
    raise PlatformError(
        "the JD search never produced a search page - Juicebox may still be "
        "building it. Open the project and check its Searches."
    )


# ----------------------------------------------------------------------
# the filters
# ----------------------------------------------------------------------


async def _clear_box(page: "Page", label: str) -> None:
    if await page.evaluate(_MARK, label) == "ok":
        await page.locator("[data-jbw='1']").focus()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")


async def _section_text(page: "Page", label: str) -> str:
    """A section's chips, read after scrolling it into view.

    The editor virtualises what is off-screen, so a section read cold can come
    back short and a chip that is there gets added twice (a skill, 2026-09-03).
    `_MARK` scrolls the section's input into view as a side effect.
    """
    await page.evaluate(_MARK, label)
    await page.wait_for_timeout(500)
    return await page.evaluate(_BLOCK_TEXT, label)


async def _add_chip(page: "Page", label: str, value: str, *, mode: str = "loose") -> str | None:
    """Type one value into a section's box and return the chip label it landed
    as, or None when nothing landed (the half-typed text is then cleared)."""
    state = await page.evaluate(_MARK, label)
    if state != "ok":
        log.info("juicebox filter input missing",
                 extra={"section": label, "state": state})
        return None
    await page.wait_for_timeout(400)
    before = await page.evaluate(_BLOCK_TEXT, label)
    box = page.locator("[data-jbw='1']")
    await box.focus(timeout=10_000)
    await page.keyboard.type(value, delay=40)
    await page.wait_for_timeout(2_200)
    options = await page.evaluate(_POPPER)
    chosen = ""
    if options:
        index = pick_option(options, value, mode=mode)
        if index is None:
            # Suggestions came, none of them this one: Juicebox does not know
            # it, and taking the nearest name would filter on the wrong company.
            await page.keyboard.press("Escape")
            await _clear_box(page, label)
            log.info("juicebox option not offered",
                     extra={"section": label, "value": value, "offered": options[:5]})
            return None
        chosen = options[index]
        if _landed(chosen, before.lower()):
            # The suggestion is a chip the section already has ("NY" typed,
            # "New York" already there): nothing to add, nothing refused.
            await page.keyboard.press("Escape")
            await _clear_box(page, label)
            return chip_label([chosen.strip().splitlines()[0]], value)
        for _ in range(index + 1):
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(150)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1_600)
    after = await page.evaluate(_BLOCK_TEXT, label)
    gained = new_lines(before, after)
    if gained:
        return chip_label(gained, value)
    lowered = after.lower()
    if value.lower() in lowered or _landed(chosen, lowered):
        return value
    await _clear_box(page, label)
    return None


@dataclass(slots=True)
class StageResult:
    labels: list[str] = field(default_factory=list)   # selected, as the menu names them
    missing: list[str] = field(default_factory=list)  # wanted keys that did not land
    offered: list[str] = field(default_factory=list)  # keys the menu had
    value: str = ""                                    # the select's value afterwards


async def _set_stages(page: "Page", stage: str | None) -> StageResult:
    """Select every funding stage from Seed up to the client's own.

    An unknown stage leaves Juicebox's own choice alone and selects nothing:
    a guess here would decide who is searched.
    """
    result = StageResult()
    marked = await page.evaluate(_MARK_STAGE_SELECT)
    if marked.get("state") != "ok":
        log.info("juicebox stage select missing", extra={"state": marked.get("state")})
        return result
    result.value = str(marked.get("value") or "")
    await page.locator("[data-jbw='1']").click(timeout=10_000)
    await page.wait_for_timeout(1_200)
    options = await page.evaluate(_STAGE_OPTIONS)
    if not options:
        await page.keyboard.press("Escape")
        log.info("juicebox stage menu did not open")
        return result

    # Match on the key Juicebox gives each option, else on what its label
    # reads as, so a renamed key still lands on the right stage.
    keyed: dict[str, dict] = {}
    for option in options:
        key = option["key"] if option["key"] in ALL_STAGE_KEYS \
            else (stage_key(option["label"]) or option["key"])
        if key:
            keyed.setdefault(key, option)
    result.offered = list(keyed)
    wanted = stages_up_to(stage, result.offered)
    result.missing = [k for k in stage_plan(stage) if k not in keyed]
    if not wanted:
        await page.keyboard.press("Escape")
        return result

    # Toggle to exactly the wanted set: what the AI pre-selected beyond it is
    # deselected, what it missed is selected.
    for key, option in keyed.items():
        should = key in wanted
        if option["selected"] == should:
            continue
        target = page.locator(f'ul[role=listbox] li[role=option][data-value="{option["key"]}"]')
        if not await target.count():
            target = page.locator("ul[role=listbox] li[role=option]").filter(
                has_text=option["label"]
            )
        try:
            await target.first.click(timeout=5_000)
            await page.wait_for_timeout(400)
        except Exception as exc:
            log.info("juicebox stage toggle failed",
                     extra={"stage": key, "error": str(exc)[:120]})
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)

    after = await page.evaluate(_MARK_STAGE_SELECT)
    result.value = str(after.get("value") or "")
    selected = [k for k in result.value.split(",") if k]
    result.labels = [keyed[k]["label"] for k in wanted if k in selected]
    result.missing += [k for k in wanted if k not in selected]
    log.info("juicebox stages set", extra={"wanted": wanted, "value": result.value})
    return result


async def _set_years(page: "Page", label: str, years: int) -> bool:
    """The experience fields are plain inputs, not chips: fill, blur, read back."""
    state = await page.evaluate(_MARK, label)
    if state != "ok":
        log.info("juicebox filter input missing",
                 extra={"section": label, "state": state})
        return False
    box = page.locator("[data-jbw='1']")
    await box.click(timeout=10_000)
    await box.fill(str(years), timeout=10_000)
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(1_200)
    return (await page.evaluate(_READ_INPUT, label)).strip() == str(years)


async def configure_filters(
    page: "Page",
    search_url: str,
    *,
    titles: list[str],
    skills: list[str],
    location: str | None,
    min_years: int | None = None,
    max_years: int | None = None,
    companies: list[str] | None = None,
    stage: str | None = None,
) -> SourcingReport:
    """Open the search's filter editor, add what is missing, save, verify."""
    report = SourcingReport(search_url=search_url)
    await page.goto(search_url, wait_until="domcontentloaded", timeout=90_000)
    await _open_editor(page)

    sections: list[tuple[str, list[str]]] = [(JOB_TITLES, titles)]
    places = split_locations(location)
    if places:
        sections.append((LOCATIONS, places))
    sections.append((SKILLS, skills))
    if companies:
        sections.append((COMPANIES, companies))
    for label, values in sections:
        added: list[str] = []
        refused: list[str] = []
        mode = match_mode(label)
        for value in values:
            current = await _section_text(page, label)
            if present(current, value, mode=mode):
                continue
            # A company is a name, and the nearest name is a different
            # company; titles and skills may take the nearest suggestion.
            landed = await _add_chip(page, label, value, mode=mode)
            # `added` holds the chip as Juicebox labels it - what a recruiter
            # sees in the editor - and `refused` what was typed and did not land.
            (added if landed else refused).append(landed or value)
            log.info("juicebox chip",
                     extra={"section": label, "value": value, "landed": landed})
        report.added[label] = added
        report.refused[label] = refused

    years = [(MIN_YEARS, min_years), (MAX_YEARS, max_years)]
    for label, value in years:
        if value is None:
            continue
        ok = await _set_years(page, label, value)
        (report.added if ok else report.refused)[label] = [str(value)]
        log.info("juicebox years",
                 extra={"section": label, "value": value, "committed": ok})

    stages = StageResult()
    if stage:
        stages = await _set_stages(page, stage)
        if stages.labels:
            report.added[STAGES] = stages.labels
        if stages.missing:
            report.refused[STAGES] = stages.missing

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
    for label, _values in sections:
        block = await _section_text(page, label)
        lost = [chip for chip in report.added.get(label, [])
                if not present(block, chip, mode=match_mode(label))]
        if lost:
            report.added[label] = [c for c in report.added[label] if c not in lost]
            report.refused.setdefault(label, []).extend(lost)
            log.warning("juicebox filters lost on reload",
                        extra={"section": label, "lost": lost})
    for label, value in years:
        if value is None or label not in report.added:
            continue
        kept = (await page.evaluate(_READ_INPUT, label)).strip()
        if kept != str(value):
            report.refused[label] = report.added.pop(label)
            log.warning("juicebox years lost on reload",
                        extra={"section": label, "kept": kept})
    if stage:
        marked = await page.evaluate(_MARK_STAGE_SELECT)
        report.stage_keys = [k for k in str(marked.get("value") or "").split(",") if k]
        lost = [k for k in stages_up_to(stage, stages.offered) if k not in report.stage_keys]
        if lost and STAGES in report.added:
            log.warning("juicebox stages lost on reload",
                        extra={"lost": lost, "kept": report.stage_keys})
            report.refused.setdefault(STAGES, []).extend(lost)
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
    min_years: int | None = None,
    max_years: int | None = None,
    companies: list[str] | None = None,
    stage: str | None = None,
    project_url: str | None = None,
    search_url: str | None = None,
) -> SourcingReport:
    """The whole flow: project -> JD search -> filters -> saved.

    `project_url` reuses a project that already exists - one a recruiter made,
    or one an earlier run created and then stopped short of the search - rather
    than standing a duplicate up beside it. `search_url` goes further and only
    sets the filters on a search that already exists.
    """
    created = False
    if search_url is None:
        created = project_url is None
        if created:
            project_url = await create_project(page, project_name)
        search_url = await jd_search(page, project_url, jd)
    elif project_url is None:
        project_url = project_home(search_url)
    report = await configure_filters(
        page, search_url, titles=titles, skills=skills, location=location,
        min_years=min_years, max_years=max_years, companies=companies, stage=stage,
    )
    report.project_url = project_url or ""
    report.project_created = created
    return report
