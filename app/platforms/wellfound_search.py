"""Wellfound's sourcing: one saved search per role, under Find Talent.

A saved search is a name plus filters - role, location, skills, years - over
Wellfound's candidate pool (`/recruit/source/<id>`). Wellfound makes one by
itself when a job is *published*; the automation only ever saves drafts, so
this makes the search the draft would otherwise wait for, from the same
shared sourcing profile every other platform reads (D-024).

**Nothing here contacts anyone.** A saved search lists candidates; one is
pitched only when a recruiter presses *Request to chat*.

How each part is written, and why (mapped 2026-11-12, briefs in
docs/platforms/wellfound.md):

- **Create** - the New saved search form: a name, a function, *Create search*.
- **Role, location, skills** - the filter column's own pickers. Each is bound
  to Wellfound's vocabulary and stores an internal criterion id, so the
  picker is the only way to turn a word into one.
- **Years** - a slider in the page, but the filters are saved by the search
  call itself: the app re-runs `RecruitSearchResult` with the whole filter
  configuration and the search's id, and the server keeps it. The stored
  filters are read back, the years added, and the same call sent.
- **The pitch template** - `SavedSearchPreferredPitchRecipeUpdate`, so the
  role's template is the one offered when a recruiter pitches from here.
- **The job link is not set.** Wellfound's dropdown offers published jobs
  only, and the listing is a draft. Its button is also not safe to touch:
  opening it and pressing Escape unlinked a real search on 2026-11-12.

Read back through `SearchFilters` at the end; what did not stick is named.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.wellfound_api import APP, WellfoundTap, errors_of

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

NEW_SEARCH_URL = f"{APP}/recruit/search/new"
SOURCE_URL = f"{APP}/recruit/source"
_SEARCH_URL = re.compile(r"/recruit/source/(\d+)")
TITLE_MAX = 150
# Wellfound's years slider stops at 10+.
YEARS_CEILING = 10

# "Broadly speaking, what function are you hiring for?" - the form's radios,
# and the words in a role's title that point at each. Engineering is the
# default: it is what TrustIn recruits for, and the question only seeds the
# role list the search opens with.
FUNCTIONS = ("Engineering", "Design", "Sales", "Product", "Operations", "Marketing", "Management", "Other")
_FUNCTION_WORDS: tuple[tuple[str, str], ...] = (
    (r"\b(designer|design lead|ux|ui/ux)\b", "Design"),
    (r"\b(sales|account executive|business development|sdr|bdr)\b", "Sales"),
    (r"\bproduct (manager|owner|lead)\b|\bhead of product\b", "Product"),
    (r"\b(marketing|growth marketer|content|seo)\b", "Marketing"),
    (r"\b(operations|people ops|finance|recruiter|talent|chief of staff)\b", "Operations"),
)

_PICKERS = {
    # section name in the filter column -> its picker's placeholder
    "Role": "Find a role",
    "Location": "Find a location",
    "Skill": "Find a skill",
}
_CRITERION = {"Role": "ROLE", "Location": "ONSITE_LOCATION", "Skill": "CANONICAL_SKILL"}


# Wellfound's skill list spells these out; the short form finds the wrong
# thing or nothing ("AWS" offers only "AWS CloudFormation", 2026-11-12).
SKILL_NAMES = {
    "aws": "Amazon Web Services",
    "gcp": "Google Cloud Platform",
    "k8s": "Kubernetes",
    "js": "JavaScript",
    "ts": "TypeScript",
    "postgres": "PostgreSQL",
    "golang": "Go",
}


@dataclass(slots=True)
class SearchPlan:
    title: str
    function: str = "Engineering"
    role: str = ""
    location: str = ""
    # Candidates, best first: the picker is tried down the list until
    # `max_skills` have been accepted, because a skill Wellfound's list does
    # not carry should cost the next one its place, not the search a filter.
    skills: list[str] = field(default_factory=list)
    max_skills: int = 3
    min_years: int | None = None


def option_order(texts: list[str], wanted: str, *, loose: bool = False) -> list[int]:
    """Which offered options are the wanted value, best first.

    Exact, then "<wanted>, <region>" (how places are listed). A bare prefix
    counts only when `loose` - right for a city ("New York" → "New York
    City"), wrong for a skill ("AWS" → "AWS CloudFormation").
    """
    want = wanted.strip().lower()
    low = [t.strip().lower() for t in texts]
    order = [i for i, t in enumerate(low) if t == want]
    order += [i for i, t in enumerate(low) if t.startswith(want + ",") and i not in order]
    if loose:
        order += [i for i, t in enumerate(low) if t.startswith(want) and i not in order]
    return order


@dataclass(slots=True)
class SearchResult:
    search_id: str = ""
    url: str = ""
    title: str = ""
    action: str = ""  # created | updated | would create | would update
    roles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    min_years: int | None = None
    pitch_linked: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [
            f"role {', '.join(self.roles) or '-'}",
            f"location {', '.join(self.locations) or '-'}",
            f"skills {', '.join(self.skills) or '-'}",
            f"years {str(self.min_years) + '+' if self.min_years is not None else '-'}",
        ]
        return f"saved search {self.action}: {self.title!r} ({'; '.join(parts)})"


def function_for(title: str) -> str:
    """Which of Wellfound's eight functions a role title belongs to."""
    low = (title or "").lower()
    for pattern, function in _FUNCTION_WORDS:
        if re.search(pattern, low):
            return function
    return "Engineering"


def clamp_years(years: int | None) -> int | None:
    if years is None or years <= 0:
        return None
    return min(int(years), YEARS_CEILING)


def stored(criteria: list[dict[str, Any]], kind: str) -> list[str]:
    """The names a search already holds for one criterion type."""
    names = []
    for item in criteria or []:
        criterion = item.get("criterion") or {}
        if criterion.get("criterionType") == kind and criterion.get("name"):
            names.append(str(criterion["name"]))
    return names


def stored_min_years(criteria: list[dict[str, Any]]) -> int | None:
    for item in criteria or []:
        criterion = item.get("criterion") or {}
        if criterion.get("criterionType") == "TOTAL_EXPERIENCE_RANGE" and criterion.get("minimum") is not None:
            return int(criterion["minimum"])
    return None


def has_name(names: list[str], wanted: str) -> bool:
    """Whether a stored name is the wanted one. Wellfound stores a place as
    "Redwood City, CA" for a typed "Redwood City", so a stored name that
    starts with the wanted word and a comma counts."""
    want = wanted.strip().lower()
    for name in names:
        low = name.strip().lower()
        if low == want or low.startswith(want + ","):
            return True
    return False


def filter_configuration(saved: dict[str, Any]) -> dict[str, Any]:
    """A saved search's stored filters, in the shape the search call takes.

    The mirror of the app's own builder (`source-*.js`, read 2026-11-12): the
    reply to `SearchFilters` lists criteria one by one, the search call wants
    them grouped by kind. Kinds this account's searches have never used come
    out empty, exactly as the app sends them.
    """
    config: dict[str, Any] = {
        "averageExperience": None, "companySize": [], "dealSize": None,
        "diversity": [
            {"category": d.get("category"), "id": d.get("id"), "value": d.get("value")}
            for d in (saved.get("diversity") or [])
        ],
        "education": [], "experience": [], "latestExperience": None, "lookingForRemoteWork": None,
        "market": [], "officePreference": saved.get("officePreference"),
        "remoteCandidateOption": saved.get("remoteCandidateOption"),
        "onsiteLocation": [], "remoteLocation": [], "requiresSponsorship": None, "role": [],
        "roleType": [], "salary": None, "salesMarketSegment": [], "skill": [], "timeZone": [],
        "totalExperience": None,
    }
    lists = {
        "ONSITE_LOCATION": "onsiteLocation", "REMOTE_LOCATION": "remoteLocation",
        "ROLE_TYPE": "roleType", "MARKET": "market", "COMPANY_SIZE": "companySize",
        "TIME_ZONE": "timeZone", "SALES_MARKET_SEGMENT": "salesMarketSegment",
    }
    ranges = {
        "TOTAL_EXPERIENCE_RANGE": "totalExperience", "LATEST_EXPERIENCE_RANGE": "latestExperience",
        "AVERAGE_EXPERIENCE_RANGE": "averageExperience", "SALARY_RANGE": "salary", "DEAL_SIZE_RANGE": "dealSize",
    }
    for item in saved.get("searchCriteria") or []:
        criterion = item.get("criterion") or {}
        kind, cid = criterion.get("criterionType"), criterion.get("id")
        if kind == "ROLE":
            entry = {"criterionId": cid, "criterionType": kind}
            if item.get("criterionQualifier"):
                entry["criterionQualifier"] = item["criterionQualifier"]
            else:
                entry["required"] = bool(item.get("required"))
            config["role"].append(entry)
        elif kind == "CANONICAL_SKILL":
            config["skill"].append({"criterionId": cid, "criterionType": kind, "required": bool(item.get("required"))})
        elif kind in lists:
            config[lists[kind]].append({"criterionId": cid, "criterionType": kind})
        elif kind in ranges:
            config[ranges[kind]] = {
                "criterionRangeMax": criterion.get("maximum"), "criterionRangeMin": criterion.get("minimum"),
                "criterionType": kind, "required": bool(item.get("required")),
            }
        elif kind == "REQUIRES_SPONSORSHIP":
            config["requiresSponsorship"] = {
                "criterionType": kind, "required": bool(item.get("required")), "value": criterion.get("value"),
            }
        elif kind:
            # Experience and education ticks carry their kind as the value.
            bucket = "education" if "DEGREE" in str(kind) else "experience"
            config[bucket].append({"criterionType": kind, "required": bool(item.get("required"))})
    return config


def with_min_years(config: dict[str, Any], years: int) -> dict[str, Any]:
    out = copy.deepcopy(config)
    current = out.get("totalExperience") or {}
    out["totalExperience"] = {
        "criterionRangeMax": current.get("criterionRangeMax"),
        "criterionRangeMin": years,
        "criterionType": "TOTAL_EXPERIENCE_RANGE",
        "required": True,
    }
    return out


async def read_filters(tap: WellfoundTap, search_id: str) -> dict[str, Any] | None:
    """The saved search as Wellfound stores it, or None when it has none by
    that id."""
    body = await tap.call("SearchFilters", {"savedSearchId": str(search_id), "startupId": tap.startup_id})
    try:
        saved = body["data"]["startup"]["recruit"]["savedSearch"]
    except (KeyError, TypeError) as exc:
        raise PlatformError(
            "Wellfound's saved-search filters came back in a shape this does not know"
            + (f": {errors_of(body)}" if errors_of(body) else ".")
        ) from exc
    return saved if isinstance(saved, dict) else None


async def find_search(page: "Page", title: str) -> str | None:
    """A saved search with exactly this name, from the Find Talent sidebar.

    The sidebar opens on the starred tab, so *All Searches* is pressed first.
    It lists a page at a time; a search further down than the first page is
    not found and a second one is created beside it - the recorded id (an
    update, a re-post) is the reliable route.
    """
    await page.goto(SOURCE_URL, wait_until="domcontentloaded", timeout=60_000)
    try:
        await page.wait_for_selector("a[href^='/recruit/source/']", timeout=30_000)
    except Exception:
        return None
    try:
        await page.locator("button:has-text('All Searches'), [role=tab]:has-text('All Searches')").locator(
            "visible=true"
        ).first.click(timeout=4_000)
        await page.wait_for_timeout(2_500)
    except Exception:
        pass
    links = await page.eval_on_selector_all(
        "a[href^='/recruit/source/']",
        "els => els.map(a => [a.getAttribute('href'), (a.innerText || '').trim()])",
    )
    want = re.sub(r"\s+", " ", title).strip().lower()
    for href, text in links:
        # "Last seen: today <name>" / "New <name>": the name is the last line.
        name = re.sub(r"\s+", " ", (text or "").split("\n")[-1]).strip().lower()
        match = _SEARCH_URL.search(href or "")
        if match and name == want:
            return match.group(1)
    return None


async def _create(page: "Page", plan: SearchPlan) -> str:
    await page.goto(NEW_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
    name = page.locator("#form-input--title")
    await name.wait_for(state="visible", timeout=45_000)
    await name.fill(plan.title)
    function = plan.function if plan.function in FUNCTIONS else "Engineering"
    # The radios are visually hidden; the label is the click target.
    await page.locator(f"label:has-text('{function}')").locator("visible=true").first.click(timeout=15_000)
    # Choosing a function reveals "What <function> role are you looking to
    # fill?" - a checklist, and Create search refuses without one ticked
    # ("Please select a role", seen 2026-11-12).
    role = page.locator(f"label:text-is('{plan.role}')").locator("visible=true").first if plan.role else None
    try:
        if role is None:
            raise LookupError
        await role.click(timeout=10_000)
    except Exception as exc:
        offered = await page.eval_on_selector_all(
            "label", "els => els.filter(e => e.querySelector('input[type=checkbox]') || "
            "(e.htmlFor && (document.getElementById(e.htmlFor) || {}).type === 'checkbox'))"
            ".map(e => (e.innerText || '').trim()).filter(Boolean)",
        )
        raise PlatformError(
            f"Wellfound needs a role ticked to create a {function} search and offers no "
            f"{plan.role or '(none given)'!r}. Its {function} roles: {', '.join(offered) or 'none shown'}. "
            "Set the role's category to one of them, or create the search by hand under Find Talent."
        ) from exc
    await page.locator("button[type='submit']:has-text('Create search')").first.click(timeout=15_000)
    try:
        await page.wait_for_url(_SEARCH_URL, timeout=60_000)
    except Exception as exc:
        raise PlatformError(
            f"Wellfound did not open a search after Create search (still on {page.url}). "
            "Its New saved search form may be asking for something this does not fill."
        ) from exc
    match = _SEARCH_URL.search(page.url)
    assert match is not None
    return match.group(1)


async def _pick(page: "Page", section: str, value: str) -> bool:
    """Choose one value in a filter section's picker. False when Wellfound's
    list has nothing by that name."""
    box = page.locator(f"input[placeholder='{_PICKERS[section]}']").locator("visible=true").first
    if not await box.count():
        # Collapsed: the section's header is its toggle. No word boundary
        # after the name - the header's text runs it straight into its
        # byline ("SkillProgramming languages, frameworks and tools"), which
        # is why `Skill\b` matched nothing on 2026-11-12.
        toggle = page.locator("[data-test='toggle control']").filter(has_text=re.compile(rf"^\s*{section}")).first
        await toggle.scroll_into_view_if_needed(timeout=10_000)
        await toggle.click(timeout=10_000)
    await box.wait_for(state="visible", timeout=15_000)
    value = SKILL_NAMES.get(value.strip().lower(), value) if section == "Skill" else value
    await box.click()
    await box.fill("")
    await box.type(value, delay=40)
    await page.wait_for_timeout(1_800)
    options = page.locator("[role=option]").locator("visible=true")
    count = await options.count()
    texts = [((await options.nth(i).inner_text()) or "").split("\n")[0].strip() for i in range(count)]
    order = option_order(texts, value, loose=section == "Location")
    if not order:
        try:
            await box.fill("")
        except Exception:
            pass
        return False
    await options.nth(order[0]).click(timeout=10_000)
    # Each choice re-runs the search, which is what saves it.
    await page.wait_for_timeout(2_500)
    return True


async def _set_years(tap: WellfoundTap, search_id: str, years: int) -> None:
    """Add the minimum-years filter by sending the search call the page
    sends, with the stored filters and the years in it."""
    saved = await read_filters(tap, search_id)
    if saved is None:
        raise PlatformError(f"Wellfound has no saved search {search_id} to set the years on.")
    # The page's own last call for this search, when it has made one: the
    # exact envelope (bucket, sort, paging) rather than a reconstruction.
    live = tap.last_variables.get("RecruitSearchResult") or {}
    envelope = copy.deepcopy(live) if str(live.get("savedSearchId")) == str(search_id) else {}
    configuration = envelope.get("configurationInput") or {
        "bucket": "ACTIVE", "excludeCurated": False, "sort": "MATCH", "perPage": 6,
    }
    configuration.update(
        {
            "filterConfiguration": with_min_years(filter_configuration(saved), years),
            "keyword": saved.get("searchKeyword") or "",
            "savedSearchId": str(search_id),
            "page": 1,
        }
    )
    body = await tap.call(
        "RecruitSearchResult",
        {"configurationInput": configuration, "savedSearchId": str(search_id), "startupId": tap.startup_id},
    )
    if errors_of(body):
        raise PlatformError(f"Wellfound refused the years filter: {errors_of(body)}")


async def _link_pitch(tap: WellfoundTap, search_id: str, template_id: str) -> bool:
    body = await tap.call(
        "SavedSearchPreferredPitchRecipeUpdate",
        {"input": {"pitchRecipeId": str(template_id), "savedSearchId": str(search_id)}},
    )
    recipe = ((body.get("data") or {}).get("savedSearchUpdate") or {}).get("pitchRecipe") or {}
    return str(recipe.get("id") or "") == str(template_id)


async def set_up_search(
    page: "Page",
    tap: WellfoundTap,
    plan: SearchPlan,
    *,
    dry_run: bool,
    search_id: str = "",
    template_id: str = "",
) -> SearchResult:
    """Make the role's saved search, or bring the one it has up to the plan.

    Filters are only ever added: a value a recruiter put on the search stays.
    """
    result = SearchResult(title=plan.title)
    existing = search_id or await find_search(page, plan.title) or ""
    if dry_run:
        result.search_id = existing
        result.url = f"{SOURCE_URL}/{existing}" if existing else ""
        result.action = "would update" if existing else "would create"
        result.roles, result.locations = ([plan.role] if plan.role else []), ([plan.location] if plan.location else [])
        result.skills, result.min_years = list(plan.skills[: plan.max_skills]), clamp_years(plan.min_years)
        return result

    if existing:
        await page.goto(f"{SOURCE_URL}/{existing}/active?sort=match", wait_until="domcontentloaded", timeout=60_000)
        result.action = "updated"
    else:
        existing = await _create(page, plan)
        result.action = "created"
    result.search_id, result.url = existing, f"{SOURCE_URL}/{existing}"
    await page.wait_for_selector("[data-test='toggle control']", timeout=60_000)
    await page.wait_for_timeout(3_000)
    await tap.ready()

    saved = await read_filters(tap, existing)
    if saved is None:
        raise PlatformError(f"Wellfound opened search {existing} but returns no filters for it.")
    criteria = saved.get("searchCriteria") or []

    async def pick(section: str, value: str) -> bool:
        try:
            return await _pick(page, section, value)
        except Exception as exc:  # one picker failing must not lose the rest
            log.warning("wellfound search picker failed", extra={"section": section, "error": str(exc)[:200]})
            return False

    for section, value in (("Role", plan.role), ("Location", plan.location)):
        if not value or has_name(stored(criteria, _CRITERION[section]), value):
            continue
        if not await pick(section, value):
            result.warnings.append(f"search: Wellfound's {section.lower()} list has no {value!r} - left out")

    held = len(stored(criteria, "CANONICAL_SKILL"))
    refused: list[str] = []
    for skill in plan.skills:
        if held >= plan.max_skills:
            break
        name = SKILL_NAMES.get(skill.strip().lower(), skill)
        if has_name(stored(criteria, "CANONICAL_SKILL"), name):
            continue
        if await pick("Skill", skill):
            held += 1
        else:
            refused.append(skill)
    if refused and held < plan.max_skills:
        result.warnings.append(
            "search: Wellfound's skill list has no " + ", ".join(refused)
            + f" - {held} skill filter{'s' if held != 1 else ''} set"
        )

    years = clamp_years(plan.min_years)
    if years is not None:
        try:
            await _set_years(tap, existing, years)
        except PlatformError as exc:
            result.warnings.append(f"search: the years filter was not set - {exc}")

    if template_id:
        try:
            result.pitch_linked = await _link_pitch(tap, existing, template_id)
        except PlatformError as exc:
            result.warnings.append(f"search: the pitch template was not attached - {exc}")
        else:
            if not result.pitch_linked:
                result.warnings.append("search: Wellfound did not attach the pitch template to the search")

    # What Wellfound now holds, not what was typed.
    saved = await read_filters(tap, existing) or {}
    criteria = saved.get("searchCriteria") or []
    result.roles = stored(criteria, "ROLE")
    result.locations = stored(criteria, "ONSITE_LOCATION") + stored(criteria, "REMOTE_LOCATION")
    result.skills = stored(criteria, "CANONICAL_SKILL")
    result.min_years = stored_min_years(criteria)
    if years is not None and result.min_years != years:
        result.warnings.append(
            f"search: years of experience reads {result.min_years} after saving, not {years}+ - "
            "set the slider on the search by hand"
        )
    if plan.location and not result.locations:
        result.warnings.append("search: no location stuck, so the search covers everywhere")
    log.info("wellfound saved search set", extra={"search": existing, "action": result.action})
    return result


async def delete_search(
    page: "Page", tap: WellfoundTap, search_id: str, *, expect_title: str = "", dry_run: bool
) -> tuple[bool, str]:
    """Remove the saved search a post made. `(gone, what happened)`.

    Read first on its own page: a search whose name is no longer the one the
    post recorded has been taken over by a recruiter and is left alone.
    """
    await page.goto(f"{SOURCE_URL}/{search_id}/active?sort=match", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5_000)
    await tap.ready()
    if not _SEARCH_URL.search(page.url) or _SEARCH_URL.search(page.url).group(1) != str(search_id):
        return True, f"saved search {search_id} was already gone"
    if await read_filters(tap, search_id) is None:
        return True, f"saved search {search_id} was already gone"
    heading = ""
    try:
        heading = (
            (await page.locator("h1, h2, h3, h4").locator("visible=true").first.inner_text(timeout=8_000)) or ""
        ).strip()
    except Exception:
        pass
    # The heading carries a "Premium Search" badge after the name, and is cut
    # with an ellipsis when the name is long; compare what it shows.
    heading = re.sub(r"\s*Premium Search\s*$", "", heading).strip()
    shown = re.sub(r"\s*(\.\.\.|…)\s*$", "", heading).strip().lower()
    expect = expect_title.strip().lower()
    if expect and (not shown or not (expect.startswith(shown) or shown.startswith(expect))):
        return False, (
            f"saved search {search_id} is headed {heading!r}, not {expect_title!r} - left in place; "
            "delete it under Find Talent if it is this role's"
        )
    if dry_run:
        return True, f"would delete saved search {heading or search_id!r} ({search_id})"
    body = await tap.call("SavedSearchDelete", {"input": {"savedSearchId": str(search_id)}})
    if errors_of(body):
        return False, (
            f"Wellfound refused to delete saved search {search_id} ({errors_of(body)}) - "
            "delete it under Find Talent (the bin beside its name)"
        )
    if await read_filters(tap, search_id) is not None:
        return False, f"saved search {search_id} is still there after the delete - remove it under Find Talent"
    return True, f"saved search {heading or search_id!r} deleted"
