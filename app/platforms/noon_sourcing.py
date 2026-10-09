"""noon's sourcing wizard, replayed through the calls its own front end makes.

Stage 1 of a noon role — "Sourcing · Find candidates" — is a seven-step wizard:
paste the job description, pick a candidate pool, confirm the search criteria
noon extracted (titles, years, location, must-haves, nice-to-haves, example
companies, the client, visa), rate the target companies it proposes, star the
non-negotiables, rank them, answer a few clarifying questions. The recruiter's
habit is to make the result as tight as the wizard allows: every nice-to-have
promoted into the must-haves, every generated criterion kept as a
non-negotiable, the strictest answer chosen for each question. That is what
this module does, in that order.

**Why the API and not the page.** The wizard is a single component with timed
stage transitions, chip inputs, a two-thumb slider, an autocomplete that only
opens on real keystrokes, star toggles and a one-question-at-a-time screen —
while the state it produces travels in a handful of JSON calls. Driving the DOM
would mean racing animations to reproduce a payload we can simply send, so this
sends it. Every call here is one the portal makes itself, in the same order,
with the same fields — a replay, not an extension — and a changed payload shape
shows up as a `PlatformError` naming the call that failed. The mapping was
first read out of noon's portal bundle (2026-08-31) and then **watched being
made by the live wizard on 2026-10-09**, which is where the step-3 write
(`prepare_role_preferences`, the full `update_role`) and the target-company
rating step come from; `docs/platforms/noon.md#the-sourcing-wizard` records
each screen against its call.

That makes this an undocumented interface: noon has not published it and could
change it. Ask noon (support@noon.ai) before treating it as stable.

The auth token is Firebase's, re-minted per page load and sent in the JSON body
rather than a header, so it cannot be replayed from a session file. It is
lifted from the first request the portal makes after it boots, and the calls go
out through `page.evaluate(fetch(...))` — same browser, same origin, same
credentials as the tab the recruiter would have used.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

API = "https://noon.fly.dev"
PORTAL = "https://www.noon.ai/portal/sourcing"

# noon's own sentinel for "this question was left unanswered". The settings
# screen writes it when an answer box is cleared, and renders it as empty.
SKIP = "SKIP"

# Candidate pools offered by step 2. "public" is Entire Internet, which is what
# the account sources from; the others exist so a caller can override.
SOURCES = ("public", "ats", "inbound")

# How often `fetch_role` asks again for a role noon has not listed yet.
ROLE_POLL_SECONDS = 10.0

# Step 3 summarises the job description server-side; the portal polls for it
# while the screen is open. Bounded here because a slow summary is not a
# reason to leave the role half-configured.
JD_SUMMARY_POLL_SECONDS = 2.0
JD_SUMMARY_WAIT_SECONDS = 30.0

# The most example companies handed to noon. Each one is a search call, and
# the shared profile's list is already a shortlist.
MAX_EXAMPLE_COMPANIES = 12

# LLM-written answer options, so these are markers rather than an enumeration.
# Loose ones are stripped from the text before the strict ones are counted, so
# that "not required" does not also score as "required".
_LOOSE_MARKERS = (
    "not required", "not needed", "not necessary", "not important",
    "nice to have", "nice-to-have", "preferred but", "preferred, but",
    "optional", "open to", "flexible", "no preference", "no strong preference",
    "does not matter", "doesn't matter", "willing to consider", "would consider",
    "either is fine", "either works", "bonus", "plus, not", "no requirement",
    "is fine", "is sufficient", "acceptable", "strong plus", "a plus",
)
_STRICT_MARKERS = (
    "required", "must have", "must be", "non-negotiable", "nonnegotiable",
    "mandatory", "essential", "strictly", "only candidates", "only from",
    "exclusively", "hard requirement", "hard filter", "dealbreaker", "deal-breaker",
)

# A question that offers to widen the search ("would you consider…") is
# tightened by answering no; one that asks whether something is demanded ("is X
# required?") is tightened by answering yes. Bare yes/no options carry no
# strictness of their own, so the question's own wording decides.
_WIDENING_QUESTION = (
    "would you consider", "would you be open", "are you open", "should we also",
    "should noon also", "can we include", "would you accept", "is it okay",
    "is it ok", "would you be flexible", "any exceptions", "willing to",
    "should we broaden", "should we expand", "would you look at",
)
_DEMANDING_QUESTION = (
    "required", "require", "must", "essential", "necessary", "need to have",
    "dealbreaker", "deal-breaker", "non-negotiable",
)

_YES_WORDS = ("yes", "yeah", "yep", "correct", "true", "agree")
_NO_WORDS = ("no", "nope", "never", "false")

_FETCH_JS = """
async ([url, payload]) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let data = text;
  try { data = JSON.parse(text); } catch (error) { /* plain text is fine */ }
  return {status: response.status, data: data};
}
"""


# ----------------------------------------------------------------------
# what a run produced
# ----------------------------------------------------------------------


@dataclass(slots=True)
class SourcingReport:
    """What the wizard was told, for the log and for the Notion detail line."""

    role_id: str = ""
    must_haves: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    non_negotiables: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    started_sourcing: bool = False
    warnings: list[str] = field(default_factory=list)
    # The search filters, as noon read them back out of the text it was given.
    # Criteria rank the pool; these decide the pool, so an empty location is
    # worth saying out loud even on a run that otherwise succeeded.
    location: str = ""
    titles: list[str] = field(default_factory=list)
    years: tuple[int, int] | None = None
    # Step 3's company half: the example companies that resolved to a noon
    # company record, the ones that did not, and how many of noon's proposed
    # target companies were rated in step 4.
    example_companies: list[str] = field(default_factory=list)
    unresolved_companies: list[str] = field(default_factory=list)
    rated_companies: int = 0
    client_description: str = ""

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.must_haves)} must-have(s)",
            f"{len(self.promoted)} promoted from nice-to-have",
            f"{len(self.non_negotiables)} non-negotiable(s)",
            f"{len(self.answers)} question(s) answered",
        ]
        parts.append(f"location {self.location}" if self.location else "no location")
        if self.titles:
            parts.append(f"{len(self.titles)} title(s)")
        if self.years:
            parts.append(f"{self.years[0]}-{self.years[1]} years")
        if self.example_companies:
            parts.append(f"{len(self.example_companies)} example company(ies)")
        if self.rated_companies:
            parts.append(f"{self.rated_companies} target company(ies) rated")
        parts.append("sourcing started" if self.started_sourcing else "not started")
        return ", ".join(parts)


@dataclass(slots=True)
class WizardBrief:
    """What the caller knows about the role beyond the job description.

    Everything here fills a gap noon's own extractor may leave: the shared
    sourcing profile's titles, years and target companies (D-024), the row's
    location, and a one-line description of the client for noon's calibration.
    noon's reading of the text wins where it has one; these are the fallbacks
    and the additions, never an override.
    """

    example_companies: list[str] = field(default_factory=list)
    client_description: str = ""
    titles: list[str] = field(default_factory=list)
    years: tuple[int, int] | None = None
    location: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# criteria — pure functions, so the policy is testable without a browser
# ----------------------------------------------------------------------


# TrustIn writes a job title as the role, then what sells it: "Backend Platform
# Engineer - NYC / Series A / Kubernetes". The decoration is separated by a
# spaced dash or a slash, and both are safe to cut on because a real job title
# contains neither: "Site Reliability Engineer", "Head of Data". A hyphen
# without spaces is kept, so "Front-End Engineer" survives whole.
_TITLE_DECORATION = re.compile(r"\s+[-–—/|·•]\s*.*$|\s*/\s*.*$")


def role_title(title: str) -> str:
    """Just the role, out of a decorated title.

    This is what goes into the preamble's `Job title:` line, and noon turns it
    into the title list it searches for (`preferences.type`). Handed the whole
    decorated string it reads "NYC" and "Series A" as job titles and looks for
    people who hold them, which is worse than telling it nothing: a wrong
    filter excludes the right people silently.

    Only the leading segment is trusted, so anything that does not parse into
    one comes back empty rather than guessed at.
    """
    cleaned = _TITLE_DECORATION.sub("", (title or "").strip()).strip(" -–—/|,")
    # A single word is a company or a fragment far more often than a job title,
    # and "Kepler" as a search title would be actively wrong.
    return cleaned if len(cleaned.split()) >= 2 else ""


def targeting_preamble(
    *,
    title: str = "",
    location: str = "",
    employment_type: str = "",
    skills: list[str] | None = None,
    similar_titles: list[str] | None = None,
    nice_to_have: list[str] | None = None,
    companies: list[str] | None = None,
) -> str:
    """The search facts, stated plainly, to sit above the job description.

    noon's `generate_params` *extracts* the role's search parameters — the
    location, the titles and the years of experience that decide which profiles
    the agent looks at in the first place — and since 2026-10-09 the replay
    writes what it extracted onto the role itself (`build_preferences`). It
    reads what it can out of the text it is given, and the text it was given
    was the advert, which is marketing copy: TrustIn's adverts do not state the
    location in prose, because the location is a Notion column. So the location
    came back empty on every role and the agent searched globally
    (docs/12-sourcing-criteria.md, gap 1).

    These lines are the fix that needs no new endpoint: the facts the row
    already holds, written the way a recruiter would type them into the wizard,
    so noon's own extractor picks them up. `Location:` and `Job title:` are the
    forms the portal's placeholder text uses.

    Salary is deliberately left out even though the row carries it — noon has
    no compensation preference, so the only thing it could become is a
    criterion, and every criterion here is promoted to a non-negotiable and
    starred. "Will accept £35-45k" is not a thing a profile can satisfy, so it
    would narrow the search to nobody while looking like diligence.

    The list lines beyond the filters — similar titles, the two skill tiers,
    the target companies — come from the shared sourcing profile (D-024) and
    exist so noon's extractor reads the *whole* brief, not the title alone: the
    2026-09-28 review found roles set up with nothing else. Must-haves become
    dealbreakers, nice-to-haves are promoted by `tighten` anyway, and the
    companies give the criteria generator something concrete to rank on — a
    recruiter deletes a criterion in noon in one click, where a search missing
    Python never says so.

    Whether it worked is not assumed — `run_wizard` reads the role back
    afterwards, and refuses to start the search if the location is still empty
    rather than letting it run unrestricted.
    """
    lines: list[str] = []
    role = role_title(title)
    if role:
        lines.append(f"Job title: {role}")
    if similar_titles:
        others = ", ".join(
            t.strip() for t in similar_titles
            if t.strip() and t.strip().lower() != role.lower()
        )
        if others:
            lines.append(f"Also matching job titles: {others}")
    if location.strip():
        lines.append(f"Location: {location.strip()}")
    if employment_type.strip():
        lines.append(f"Employment type: {employment_type.strip()}")
    if skills:
        named = ", ".join(s.strip() for s in skills if s.strip())
        if named:
            lines.append(f"Key skills: {named}")
    if nice_to_have:
        named = ", ".join(s.strip() for s in nice_to_have if s.strip())
        if named:
            lines.append(f"Nice-to-have skills: {named}")
    if companies:
        named = ", ".join(c.strip() for c in companies if c.strip())
        if named:
            lines.append(f"Ideal past companies: {named}")
    return "\n".join(lines)


def as_text(value: Any) -> str:
    """noon answers with a string for some fields and a list for the same
    fields elsewhere (`location` is a string on the way in and a list on the
    role), so both are read the same way."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def as_list(value: Any) -> list[str]:
    """The same value as the list shape `preferences` stores it in.

    `as_text` is for reading; this is for writing back. Every recorded noon
    role holds `preferences.location` as a list (artifacts/live1, and all 125
    roles surveyed), so a location extracted as a bare string is wrapped rather
    than sent as one — and a multi-part location stays split, because joining
    "New York" and "Atlanta, Georgia, United States" into one string would ask
    noon to find a single place by that name.
    """
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def as_lines(value: Any) -> list[str]:
    """noon keeps must-haves and nice-to-haves as one newline-joined string.

    Mirrors the portal's own reader, which accepts a list or a string and drops
    a single trailing empty line (the editor leaves one behind).
    """
    if isinstance(value, list):
        text = "\n".join(str(item) for item in value)
    else:
        text = str(value or "")
    lines = text.replace("\r", "").split("\n")
    if len(lines) == 1 and lines[0] == "":
        return []
    if lines and lines[-1] == "":
        lines.pop()
    return [line for line in lines if line.strip()]


def as_years(value: Any) -> tuple[int, int] | None:
    """`yoe` as noon returns it (`[5, 12]`), or None when it is not a band.

    The slider on step 3 runs 0 to 40; anything outside that, reversed, or not
    a pair of numbers is treated as "noon said nothing" rather than written
    onto the role, where it would silently filter everyone out.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        low, high = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if low < 0 or high > 60 or low > high:
        return None
    return low, high


def tighten(must_haves: Any, nice_to_haves: Any) -> tuple[list[str], list[str]]:
    """Every nice-to-have becomes a must-have. Returns (must-haves, promoted).

    This is the whole point of the exercise: noon splits what it read out of the
    advert into dealbreakers and preferences, and a preference does not filter
    anybody out. Duplicates are dropped case-insensitively, because the same
    requirement phrased twice would be scored twice.
    """
    kept: list[str] = []
    seen: set[str] = set()
    promoted: list[str] = []

    for line in as_lines(must_haves):
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(line.strip())

    for line in as_lines(nice_to_haves):
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(line.strip())
            promoted.append(line.strip())

    return kept, promoted


def parse_criteria(feedback: str) -> list[str]:
    """The generated criteria, out of the `*`-separated blob noon returns.

    `gpt_stream` answers with one bullet per criterion. The portal splits on the
    bullet character and drops whatever precedes the first one, which is why an
    intro sentence in the response is harmless.
    """
    text = (feedback or "").strip()
    if not text or text == "No feedback provided.":
        return []
    marker = "*" if "*" in text[:5] else "-" if "-" in text[:5] else "*"
    parts = text.split(marker)
    return [part.strip() for part in parts[1:] if part.strip()]


def format_feedback(criteria: list[str]) -> str:
    """Back into the blob noon stores on the role, exactly as the portal does."""
    return "\n".join(f"*{criterion}" for criterion in criteria if criterion.strip())


def _inherent_strictness(option: str) -> int:
    text = f" {option.strip().lower()} "
    loose = sum(1 for marker in _LOOSE_MARKERS if marker in text)
    stripped = text
    for marker in _LOOSE_MARKERS:
        stripped = stripped.replace(marker, " ")
    strict = sum(1 for marker in _STRICT_MARKERS if marker in stripped)
    return strict - loose


def _first_word(option: str) -> str:
    cleaned = option.strip().lower().lstrip("([\"'")
    for separator in (",", ".", " ", "—", "-", ":"):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0]
    return cleaned.strip()


def strictest_answer(question: str, options: list[str]) -> tuple[str, str]:
    """The option that narrows the search, and why it was chosen.

    Returns `(SKIP, reason)` when nothing in the options or the question says
    which way is stricter — leaving the question unanswered keeps the criteria
    as they were, where guessing could loosen them.
    """
    choices = [option for option in options if str(option).strip()]
    if not choices:
        return SKIP, "no options offered"

    scored = [(_inherent_strictness(option), option) for option in choices]
    best = max(score for score, _ in scored)
    if best > 0:
        winner = next(option for score, option in scored if score == best)
        return winner, "wording marks it as the stricter option"

    # Bare yes/no. The question decides which of the two narrows the search.
    asked = question.strip().lower()
    widening = any(phrase in asked for phrase in _WIDENING_QUESTION)
    demanding = any(phrase in asked for phrase in _DEMANDING_QUESTION)
    wanted = _NO_WORDS if widening else _YES_WORDS if demanding else ()
    if wanted:
        for option in choices:
            if _first_word(option) in wanted:
                return option, (
                    "question offers to widen the search, so the answer is no"
                    if widening
                    else "question asks whether it is demanded, so the answer is yes"
                )

    return SKIP, "no option is clearly stricter"


# ----------------------------------------------------------------------
# step 3 — the search criteria, as the role stores them
# ----------------------------------------------------------------------


# Defaults the portal adds to a role's `preferences` block the first time step
# 3 is confirmed (recorded 2026-10-09). Written with setdefault so a role that
# already carries a recruiter's choice keeps it.
_STEP3_DEFAULTS: dict[str, Any] = {
    "location_distance": 0,
    "onlySourceFromTheseCompanies": False,
    "ban_past_candidates": True,
    "ban_ats_candidates": False,
    "past_candidates_cooldown_days": 30,
    "ats_candidates_cooldown_days": 30,
}


def build_preferences(
    existing: Any,
    params: dict[str, Any],
    *,
    brief: WizardBrief | None = None,
    example_company_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """The role's `preferences` block with step 3 filled in.

    This is what the wizard's "Confirm the search criteria" screen writes, and
    until 2026-10-09 it was the one screen the replay skipped — so every role
    kept the empty block its creation modal wrote and searched the whole world
    on criteria alone. The keys are the portal's, as recorded from a live
    role: the title list is `type` (not `titles`), the years band is
    `experience`, the company-type chips are `companySpecs`, the example
    companies are `required_companies_to_source_from` (noon company ids).

    noon's own extraction wins where it has one; the brief fills the gaps. The
    existing block is amended rather than rebuilt, so keys this code has never
    heard of (`companyBlacklist`, `recruiters`, `managementExp`) travel
    untouched - a partial block would silently clear them.
    """
    preferences = dict(existing) if isinstance(existing, dict) else {}
    brief = brief or WizardBrief()

    titles = [t for t in as_lines(params.get("titles")) if t] or list(brief.titles)
    if titles:
        preferences["type"] = titles

    years = as_years(params.get("yoe")) or brief.years
    if years:
        preferences["experience"] = [years[0], years[1]]

    location = as_list(params.get("location")) or as_list(brief.location)
    if location:
        preferences["location"] = location

    specs = [s for s in as_lines(params.get("company_specs")) if s]
    if specs:
        preferences["companySpecs"] = specs

    ids = [str(i).strip() for i in example_company_ids if str(i).strip()]
    if ids:
        already = [str(i) for i in (preferences.get("required_companies_to_source_from") or [])]
        preferences["required_companies_to_source_from"] = list(
            dict.fromkeys([*already, *ids])
        )

    for key, value in _STEP3_DEFAULTS.items():
        preferences.setdefault(key, value)
    return preferences


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_EMPLOYER_WORDS = (
    "startup", "start-up", "scale-up", "scaleup", "company", "client", "we are",
    "we're", "is hiring", "are hiring", "our ", "business", "firm", "team of",
    "backed", "funded", "series", "seed", "founded",
)


def client_description(job_description: str, *, stage: str = "") -> str:
    """One line about the employer, for noon's "Describe the client company" box.

    Step 3 will not continue without the client named or described, and noon
    uses the description to calibrate the caliber of candidate it proposes
    ("Fortune 500 retailer headquartered in NYC with 10,000+ employees" is its
    own example). TrustIn does not name the client in a JD, so the description
    is the sentence of the JD that introduces the employer - the first one
    that talks about the company rather than the job - with the profile's
    funding stage in front when the sentence does not already say it.
    """
    text = (job_description or "").replace("\r", "")
    candidates: list[str] = []
    for paragraph in text.split("\n"):
        line = paragraph.strip()
        # Headings and bullet fragments are not sentences about anybody.
        if len(line) < 40 or (len(line.split()) <= 6 and not line.endswith(".")):
            continue
        candidates.extend(s.strip() for s in _SENTENCE_END.split(line) if len(s.strip()) >= 40)

    chosen = next(
        (s for s in candidates if any(w in s.lower() for w in _EMPLOYER_WORDS)),
        candidates[0] if candidates else "",
    )
    chosen = chosen[:240].rstrip(" ,;:")
    stage = (stage or "").strip()
    if stage and stage.lower() not in chosen.lower():
        chosen = f"{stage} stage. {chosen}".strip()
    return chosen


def match_company(name: str, results: Any) -> dict[str, Any] | None:
    """The one search result that *is* the named company, or None.

    `company_search_by_name` answers a prefix search - "Vercel" also returns
    Vercelli and a Vercel LLC - so only a result whose name matches, ignoring
    case and punctuation, is taken, and the first such result wins (noon lists
    the best-known first). A near miss is skipped rather than guessed at: an
    example company tells noon what kind of place to look in, and the wrong
    company of the same name points it at the wrong kind.
    """
    wanted = _company_key(name)
    if not wanted or not isinstance(results, list):
        return None
    for result in results:
        if isinstance(result, dict) and _company_key(str(result.get("name", ""))) == wanted:
            return result
    return None


def _company_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    # "Vercel Inc" and "Vercel" are one company; the suffix is not identity.
    return re.sub(r"\s+(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|ag|sa)$", "", key).strip()


def rate_company_cards(
    cards: Any,
    *,
    wanted: Iterable[str] = (),
    employees: tuple[int | None, int | None] | None = None,
) -> list[dict[str, Any]]:
    """Great / Okay / No for each target company noon proposes (step 4).

    noon shows the cards when the written criteria are ambiguous ("startups"
    and "SaaS" are, it says) and labels each with how it came to propose it:
    `anchor_yes` is a company it expects to fit, `anchor_no` one it expects
    not to, and `boundary` a probe along one dimension (`size_stage`,
    `caliber_high_outside`, `criterion:saas`…). The recruiter answers from
    taste; this answers from what the brief already says:

    - a company the shared profile named as a target is **great**, whatever
      noon thought of it - that is the one place the brief is explicit;
    - noon's own anchors are taken at their word (`anchor_yes` great,
      `anchor_no` no);
    - a boundary probe is judged on size against the band noon itself derived
      for the client (`company_json.employees_min/max_number` on the step-3
      save): inside the band **okay**, outside it **no**. Unknown size, or no
      band, is **okay** - the neutral answer, which teaches noon nothing and
      costs nothing.

    Every card gets a rating because the screen does not continue otherwise,
    and a rating noon can act on carries the card's own `group` and
    `dimension` back, exactly as the portal sends them.
    """
    targets = {_company_key(w) for w in wanted if _company_key(w)}
    low, high = employees if employees else (None, None)
    ratings: list[dict[str, Any]] = []
    if not isinstance(cards, list):
        return ratings
    for card in cards:
        if not isinstance(card, dict) or not card.get("company_id"):
            continue
        group = card.get("group")
        name = str(card.get("name") or "")
        size = card.get("employees")
        if _company_key(name) in targets:
            rating = "great"
        elif group == "anchor_yes":
            rating = "great"
        elif group == "anchor_no":
            rating = "no"
        elif isinstance(size, (int, float)) and (low is not None or high is not None):
            inside = (low is None or size >= low) and (high is None or size <= high)
            rating = "ok" if inside else "no"
        else:
            rating = "ok"
        ratings.append(
            {
                "company_id": str(card["company_id"]),
                "rating": rating,
                "group": group,
                "dimension": card.get("dimension"),
            }
        )
    return ratings


def employee_band(update_role_response: Any) -> tuple[int | None, int | None] | None:
    """The client-size band noon derived on the step-3 save, if it sent one."""
    if not isinstance(update_role_response, dict):
        return None
    company = update_role_response.get("company_json")
    if not isinstance(company, dict):
        return None
    low = company.get("employees_min_number")
    high = company.get("employees_max_number")
    if low is None and high is None:
        return None
    try:
        return (int(low) if low is not None else None, int(high) if high is not None else None)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# the session behind the portal
# ----------------------------------------------------------------------


@dataclass(slots=True)
class NoonSession:
    """One page, its Firebase token, the company the account belongs to and
    the signed-in user's id - the portal sends the last two alongside the
    token on most calls, and `update_role` wants the user."""

    page: "Page"
    token: str
    company: str = ""
    user: str = ""
    # The signed-in address. `refetch_roles` answers nothing without it
    # (2026-10-09): the role list is the user's, not the company's.
    email: str = ""
    timeout_seconds: float = 120.0

    async def post(
        self, path: str, payload: dict[str, Any], *, forbidden_ok: bool = False
    ) -> Any:
        """One call, made from inside the tab so it carries the real origin.

        `forbidden_ok` returns None on a 403 instead of declaring the session
        dead: `poll_role_params` answers Forbidden for a role id noon does not
        know, which is a fact about the role, not about the login.
        """
        url = f"{API}/{path.lstrip('/')}"
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_FETCH_JS, [url, payload]),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(
                f"noon did not answer {path} within {self.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"noon call {path} could not be made: {exc}") from exc

        status = int(result.get("status") or 0)
        data = result.get("data")
        if status == 403 and forbidden_ok:
            return None
        if status == 401 or status == 403:
            raise AuthenticationRequired(
                "noon rejected the session while setting up sourcing. Run: "
                "python -m app.cli login noon"
            )
        if status >= 400:
            detail = json.dumps(data)[:200] if not isinstance(data, str) else data[:200]
            raise PlatformError(f"noon returned {status} from {path}: {detail}")
        return data


async def capture_session(
    page: "Page", *, url: str = PORTAL, timeout_ms: int = 45_000
) -> NoonSession:
    """Read the auth token off the portal's own traffic.

    The token is a Firebase ID token minted from IndexedDB on page load and
    passed in the body of every call, so there is nothing in the cookie jar to
    reuse and no header to copy. Watching what the app sends is both the
    simplest way to get it and proof that the session is genuinely alive.
    """
    found: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if API not in request.url:
            return
        try:
            body = request.post_data
        except Exception:
            return
        if not body:
            return
        try:
            payload = json.loads(body)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        for key in ("token", "company", "user", "email"):
            value = payload.get(key)
            if isinstance(value, str) and value and key not in found:
                found[key] = value

    page.on("request", on_request)
    try:
        await page.goto(url, wait_until="domcontentloaded")
        waited = 0
        # Keep waiting a little past the token for the company id: it rides on
        # most calls but not the first one, and gpt_stream wants it.
        wanted = ("token", "company", "email")
        while waited < timeout_ms and not all(found.get(key) for key in wanted):
            await page.wait_for_timeout(500)
            waited += 500
            if found.get("token") and waited > 12_000:
                break
            # An expired session bounces to /log-in and will never send a token,
            # so there is nothing to wait out.
            if "/log-in" in page.url and waited > 3_000:
                break
    finally:
        page.remove_listener("request", on_request)

    if "/log-in" in page.url or not found.get("token"):
        raise AuthenticationRequired(
            "noon is not logged in (or the session has expired), so the "
            "sourcing criteria cannot be set. Run: python -m app.cli login noon"
        )

    log.info(
        "noon session captured",
        extra={
            "company": found.get("company", ""),
            "user": found.get("user", ""),
            "email": found.get("email", ""),
            "url": page.url,
        },
    )
    return NoonSession(
        page=page,
        token=found["token"],
        company=found.get("company", ""),
        user=found.get("user", ""),
        email=found.get("email", ""),
    )


# ----------------------------------------------------------------------
# the wizard
# ----------------------------------------------------------------------


class SourcingWizard:
    """Steps 1 to 7 of a role's sourcing setup, in the order the portal runs them."""

    def __init__(
        self,
        session: NoonSession,
        role_id: str,
        role_name: str,
        *,
        source: str = "public",
        start_sourcing: bool = True,
    ) -> None:
        self.session = session
        self.role_id = role_id
        self.role_name = role_name
        self.source = source if source in SOURCES else "public"
        self.start_sourcing = start_sourcing
        self.report = SourcingReport(role_id=role_id)

    def _with_token(self, **payload: Any) -> dict[str, Any]:
        return {"token": self.session.token, **payload}

    # -- step 1: the job description ------------------------------------
    async def read_job_description(self, jd: str, *, save: bool = True) -> dict[str, Any]:
        """Hand noon the job description and take back what it extracted.

        `dont_save` keeps the call a pure read, which is what a dry run wants
        (the portal itself sends the call twice: once with the flag to preview,
        once without to save). Without it noon caches the job description
        against the role — and nothing else: the location, titles and years it
        returns are written by step 3, not by this call (a role read back with
        `preferences.location` still `[]` on 2026-09-22 having been handed a
        location it quoted back correctly).
        """
        payload: dict[str, Any] = self._with_token(
            jd=jd, role=self.role_id, role_name=self.role_name
        )
        if not save:
            payload["dont_save"] = True
        params = await self.session.post("generate_params", payload)
        if not isinstance(params, dict):
            raise PlatformError(
                "noon did not return search parameters for this job description."
            )
        log.info(
            "noon read the job description",
            extra={
                "role": self.role_id,
                "titles": len(as_lines(params.get("titles"))),
                "must_haves": len(as_lines(params.get("must_haves"))),
                "nice_to_haves": len(as_lines(params.get("nice_to_haves"))),
                "location": as_text(params.get("location")),
                "yoe": as_text(params.get("yoe")),
                "company_specs": len(as_lines(params.get("company_specs"))),
            },
        )
        return params

    # -- step 2: where to source from -----------------------------------
    async def set_candidate_pool(self) -> None:
        await self.session.post(
            "set_candidate_source",
            self._with_token(role=self.role_id, source=self.source),
        )

    # -- step 3: confirm the search criteria ------------------------------
    async def resolve_companies(self, names: Iterable[str]) -> list[str]:
        """Example companies, by name, to the noon company ids the role stores.

        One `company_search_by_name` per name - the autocomplete behind the
        "Example companies" box. A name that does not match a result exactly is
        reported and left out (`match_company`).
        """
        ids: list[str] = []
        for name in list(dict.fromkeys(n.strip() for n in names if n.strip()))[:MAX_EXAMPLE_COMPANIES]:
            results = await self.session.post(
                "company_search_by_name", self._with_token(query=name)
            )
            hit = match_company(name, results)
            if hit is None:
                self.report.unresolved_companies.append(name)
                continue
            ids.append(str(hit.get("id")))
            self.report.example_companies.append(str(hit.get("name") or name))
        if self.report.unresolved_companies:
            self.report.warnings.append(
                "noon has no company record matching: "
                + ", ".join(self.report.unresolved_companies)
                + " - add them by hand in the role's Control Panel if they matter"
            )
        return ids

    async def prepare_criteria(self, preferences: dict[str, Any], must_haves: list[str]) -> None:
        """What the portal sends on arriving at step 3, then on every edit.

        It stages the draft server-side and starts the job-description summary
        the step-3 save builds on; the summary is polled, bounded, and a slow
        one is logged rather than fatal.
        """
        await self.session.post(
            "prepare_role_preferences",
            self._with_token(
                role=self.role_id,
                preferences=preferences,
                must_haves="\n".join(must_haves),
                nice_to_haves="",
            ),
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        while loop.time() - started < JD_SUMMARY_WAIT_SECONDS:
            status = await self.session.post(
                "role_summarized_jd_finished", self._with_token(role=self.role_id)
            )
            if isinstance(status, dict) and status.get("finished"):
                return
            await asyncio.sleep(JD_SUMMARY_POLL_SECONDS)
        log.warning(
            "noon's job description summary did not finish in time",
            extra={"role": self.role_id, "waited_seconds": JD_SUMMARY_WAIT_SECONDS},
        )

    async def confirm_criteria(
        self,
        preferences: dict[str, Any],
        must_haves: list[str],
        *,
        client_description: str,
        requires_visa_sponsorship: Any,
    ) -> Any:
        """Step 3's Continue: the whole screen, in the two calls it makes.

        `update_role` is the write that was missing until 2026-10-09. The
        portal sends the role's name alongside, the preferences block whole,
        the must-haves (and the emptied nice-to-haves) twice - once as the
        lists and once as `feedback` - the client's description or name, and
        noon's own visa reading passed straight back. Its answer carries the
        client-size band (`company_json`) the rating step is judged against.
        """
        joined = "\n".join(must_haves)
        await self.session.post(
            "setup_clarifying_questions",
            self._with_token(role=self.role_id, must_haves=joined),
        )
        payload: dict[str, Any] = self._with_token(
            role=self.role_id,
            name=self.role_name,
            preferences=preferences,
            must_haves=joined,
            nice_to_haves="",
            feedback=joined,
            client_description=client_description or None,
            client_name=None,
            client_linkedin_alt=None,
            requires_visa_sponsorship=requires_visa_sponsorship,
        )
        if self.session.user:
            payload["user"] = self.session.user
        return await self.session.post("update_role", payload)

    async def generate_criteria(self, must_haves: list[str]) -> list[str]:
        """Turn the must-haves into the criteria list step 5 selects from.

        The answer is one `*` bullet each. The portal sends the must-haves both
        inside the `<must_haves>` message and as fields since 2026-10-09;
        `rerun_prep: false` says step 3's preparation already ran.
        """
        joined = "\n".join(must_haves)
        feedback = await self.session.post(
            "gpt_stream",
            {
                "newdemo": True,
                "msg": f"<must_haves>\n{joined}\n</must_haves>",
                "prompt": None,
                "role": self.role_id,
                "company": self.session.company,
                "source": self.source,
                "v2": True,
                "rerun_prep": False,
                "must_haves": joined,
                "nice_to_haves": "",
            },
        )
        # The portal reads `response.data` straight as text; a JSON wrapper is
        # unwrapped here rather than reported as "no criteria", which would send
        # whoever sees it looking at the advert instead of at the response.
        if isinstance(feedback, dict):
            feedback = feedback.get("text") or feedback.get("response") or ""
        criteria = parse_criteria(feedback if isinstance(feedback, str) else "")
        if not criteria:
            raise PlatformError(
                "noon generated no criteria from these must-haves. The advert may "
                "be too short, or the must-haves too vague to filter on."
            )
        return criteria

    # -- step 4: the target companies noon proposes -----------------------
    async def rate_target_companies(
        self,
        preferences: dict[str, Any],
        *,
        client_description: str,
        wanted: Iterable[str],
        employees: tuple[int | None, int | None] | None,
    ) -> Any:
        """Rate noon's proposed companies when it asks; None when it does not.

        Returns what `save_company_ratings` answered, which the portal then
        carries on the autopilot block as `company_ratings`.
        """
        cards = await self.session.post(
            "company_rating_cards",
            self._with_token(
                role=self.role_id,
                preferences=preferences,
                client_name=None,
                client_description=client_description or None,
                client_linkedin_alt=None,
            ),
        )
        if not isinstance(cards, dict) or not cards.get("show") or not cards.get("cards"):
            return None
        ratings = rate_company_cards(cards["cards"], wanted=wanted, employees=employees)
        if not ratings:
            return None
        saved = await self.session.post(
            "save_company_ratings", self._with_token(role=self.role_id, ratings=ratings)
        )
        self.report.rated_companies = len(ratings)
        log.info(
            "noon target companies rated",
            extra={
                "role": self.role_id,
                "rated": len(ratings),
                "great": sum(1 for r in ratings if r["rating"] == "great"),
                "no": sum(1 for r in ratings if r["rating"] == "no"),
                "reason": str(cards.get("reason") or "")[:120],
            },
        )
        return saved

    # -- step 5: select every criterion ----------------------------------
    async def select_non_negotiables(
        self,
        autopilot: dict[str, Any],
        criteria: list[str],
        *,
        preferences: dict[str, Any],
        must_haves: list[str],
        company_ratings: Any,
    ) -> dict[str, Any]:
        """Star all of them. An unstarred criterion is not applied to anybody.

        noon's own advice on this screen is "best results come from 3 or fewer";
        keeping all of them is the deliberately tighter setting this automation
        exists to apply, and it is the reason a run can come back with very few
        candidates. Loosening is a matter of removing criteria in the Control
        Panel afterwards.

        The block also carries, as the portal's does, the step-3 facts the
        agent reads from here rather than from `preferences`: the company-type
        chips, the pool, the must-haves, the example companies and the ratings.
        `enabled` is the agent's on switch - the portal turns it on the moment
        `Start sourcing` is pressed; held back here when the caller wants the
        role saved but idle.
        """
        autopilot["enabled"] = bool(self.start_sourcing)
        autopilot["feedback"] = format_feedback(criteria)
        autopilot["source"] = self.source
        autopilot.setdefault("sourcing_type", "recruiting")
        autopilot["must_haves"] = "\n".join(must_haves)
        # Emptied rather than dropped: the nice-to-haves are now must-haves, and
        # leaving the originals behind would have noon score them a second time
        # as preferences.
        autopilot["nice_to_haves"] = ""
        if preferences.get("companySpecs") is not None:
            autopilot["companySpecs"] = list(preferences.get("companySpecs") or [])
        autopilot["required_companies_to_source_from"] = list(
            preferences.get("required_companies_to_source_from") or []
        )
        autopilot.setdefault("examples", [""])
        if company_ratings is not None:
            autopilot["company_ratings"] = company_ratings
        autopilot.setdefault("calibration_stage", "calibrating")
        autopilot["pending_non_negotiables"] = [
            {"id": f"criterion-{index}", "text": text}
            for index, text in enumerate(criteria)
        ]
        await self.session.post(
            "role_autopilot",
            self._with_token(id=self.role_id, autopilot=autopilot),
        )
        return autopilot

    # -- steps 6 and 7: rank, then the clarifying questions ----------------
    async def fetch_questions(self, criteria: list[str]) -> dict[str, list[str]]:
        questions = await self.session.post(
            "clarifying_questions",
            self._with_token(role=self.role_id, non_negotiables=list(criteria)),
        )
        if not isinstance(questions, dict):
            return {}
        return {
            str(question): [str(option) for option in options] if isinstance(options, list) else []
            for question, options in questions.items()
        }

    async def rank(self, autopilot: dict[str, Any], criteria: list[str]) -> dict[str, Any]:
        """Order is the ranking: #1 is the criterion noon weighs most heavily.

        The order noon generated them in is kept — it follows the advert, which
        is the only stated view of what matters most.
        """
        autopilot["non_negotiables"] = list(criteria)
        autopilot["use_ordering"] = True
        # No token on this one: the portal's save-autopilot call carries only the
        # role and the block, and it is copied as it is rather than improved.
        await self.session.post(
            "role_autopilot",
            {"id": self.role_id, "autopilot": autopilot, "initialization": True},
        )
        await self.session.post(
            "rank_non_negotiables",
            self._with_token(id=self.role_id, non_negotiables=list(criteria)),
        )
        return autopilot

    async def answer_questions(self, questions: dict[str, list[str]]) -> dict[str, str]:
        """One strictest-available answer per question; unanswered when unclear."""
        answers: dict[str, str] = {}
        for question, choices in questions.items():
            answer, why = strictest_answer(question, choices)
            answers[question] = answer
            await self.session.post(
                "mark_clarifying_question",
                self._with_token(role=self.role_id, question=question, answer=answer),
            )
            log.info(
                "clarifying question answered",
                extra={
                    "role": self.role_id,
                    "question": question[:90],
                    "answer": answer[:60],
                    "why": why,
                },
            )
        return answers

    async def finish(self, autopilot: dict[str, Any], answers: dict[str, str]) -> None:
        """The call that sets noon searching. Everything before it only saves.

        `initialization` reads backwards: the portal sends `true` while it is
        still setting the role up, and `false` on the last save of the wizard —
        which is the one that starts the search. So `initialization: false` is
        "go", and repeating `true` saves the answers and leaves the role idle.
        """
        autopilot["clarifying_answers"] = answers
        autopilot["enabled"] = bool(self.start_sourcing)
        await self.session.post(
            "role_autopilot",
            {
                "id": self.role_id,
                "autopilot": autopilot,
                "initialization": not self.start_sourcing,
            },
        )


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------


class RoleMissing(PlatformError):
    """The role is in neither of noon's role lists - deleted, or never made."""


async def fetch_role(
    session: NoonSession,
    role_id: str,
    *,
    fresh: bool = False,
    wait_seconds: float = 0.0,
    poll_seconds: float | None = None,
) -> dict[str, Any]:
    """The role as noon holds it — its autopilot and preferences are what we amend.

    Two reads, because neither alone says enough (both recorded 2026-10-09):

    - `poll_role_params {token, role}` is the direct read of one role - its
      `autopilot` and `preferences` as they are now, from the moment it is
      created. It is what the portal's own role page polls. It answers 403 for
      an id noon does not know, and it still answers for a *deleted* role.
    - `refetch_roles {token, email, company}` is the user's role list. A new
      role takes minutes to appear in it (every posting run on 2026-10-08
      missed its own role there; hours later all were listed), but it is the
      only read that says a role was deleted: the record stays, as a tombstone
      with `obsolete: true` and nothing else on it. `all_roles` is not asked
      any more - it answered `[]` for the whole account on 2026-10-08 and again
      on 2026-10-09.

    So: the direct read is the record; the list is consulted for a tombstone,
    which raises `RoleMissing` (a delete's read-back). `fresh` is the direct
    read alone - for reading back a value just written, where the list would
    only add a scan of 200 roles. `wait_seconds` keeps asking for a role
    neither read knows yet, polling every `poll_seconds`; the posting run
    passes NOON_ROLE_WAIT_SECONDS, a delete or a read-back passes nothing and
    a miss means gone.
    """
    listing: dict[str, Any] = {"token": session.token}
    if session.company:
        listing["company"] = session.company
    if session.email:
        listing["email"] = session.email

    async def direct() -> dict[str, Any] | None:
        data = await session.post(
            "poll_role_params",
            {"token": session.token, "role": role_id, "check_exhausts": False, "inbound": False},
            forbidden_ok=True,
        )
        if not isinstance(data, dict):
            return None
        autopilot, preferences = data.get("autopilot"), data.get("preferences")
        if not isinstance(autopilot, dict) and not isinstance(preferences, dict):
            return None
        preferences = preferences if isinstance(preferences, dict) else {}
        return {
            "id": role_id,
            "name": str(preferences.get("name") or ""),
            "autopilot": autopilot if isinstance(autopilot, dict) else {},
            "preferences": preferences,
        }

    async def listed() -> dict[str, Any] | None:
        payload = await session.post("refetch_roles", listing)
        roles = payload.get("roles") if isinstance(payload, dict) else payload
        if not isinstance(roles, list):
            return None
        for role in roles:
            if isinstance(role, dict) and str(role.get("id")) == role_id:
                return role
        return None

    def merged(record: dict[str, Any] | None, row: dict[str, Any] | None) -> dict[str, Any]:
        # The list row carries the name, the creator and the flags; the direct
        # read carries the blocks as they are now. The blocks win.
        if row is None:
            return record or {}
        if record is None:
            return row
        return {**row, "autopilot": record["autopilot"], "preferences": record["preferences"]}

    record = await direct()
    if fresh and record is not None:
        return record

    row = await listed()
    if row is not None and row.get("obsolete"):
        raise RoleMissing(
            f"noon role {role_id!r} has been deleted (it is listed as obsolete)."
        )
    if record is not None or row is not None:
        return merged(record, row)
    log.info("role not readable yet", extra={"role": role_id})

    poll = ROLE_POLL_SECONDS if poll_seconds is None else poll_seconds
    loop = asyncio.get_running_loop()
    started = loop.time()
    while loop.time() - started < wait_seconds:
        await asyncio.sleep(poll)
        record = await direct()
        row = await listed()
        if row is not None and row.get("obsolete"):
            raise RoleMissing(
                f"noon role {role_id!r} has been deleted (it is listed as obsolete)."
            )
        if record is not None or row is not None:
            # The lag is not documented anywhere; the log is how it gets measured.
            log.info(
                "role appeared in noon",
                extra={"role": role_id, "waited_seconds": round(loop.time() - started)},
            )
            return merged(record, row)

    waited = f" after waiting {wait_seconds:.0f}s" if wait_seconds else ""
    raise RoleMissing(
        f"noon has no role {role_id!r} on this account{waited}. If it was just "
        "created, noon had not caught up; run `source --role` again in a "
        "moment. Otherwise it has been deleted."
    )


def _warn_no_location(report: SourcingReport, *, targeting: str) -> None:
    report.warnings.append(
        "noon extracted no location from this job description, so the role "
        "will be searched globally. Fill the row's Location column, or "
        "state the location in the client's JD."
        if not targeting.strip()
        else "noon extracted no location even though one was given to it - "
        "check the role's Control Panel and set it by hand."
    )


def _warn_no_titles(report: SourcingReport) -> None:
    report.warnings.append(
        "noon extracted no job titles from this job description, so it is "
        "matching on the criteria alone. Check the role's Control Panel."
    )


def _check_preferences(role: dict[str, Any], report: SourcingReport) -> bool:
    """Did the filters actually land on the role? Returns whether a location did.

    Extraction succeeding and the save succeeding are two different things, and
    only the second one decides who gets searched for — so this reads the role
    rather than trusting the write, and its answer is what decides whether the
    search is allowed to start. The title list is `preferences.type` on a real
    role (recorded 2026-10-09); `titles` is what `generate_params` calls the
    same thing on the way in.
    """
    preferences = role.get("preferences")
    if not isinstance(preferences, dict):
        report.warnings.append(
            "noon's role carries no preferences block, so the location and "
            "titles could not be confirmed."
        )
        return False

    saved = as_text(preferences.get("location"))
    if saved:
        report.location = saved

    titles = [t for t in as_lines(preferences.get("type")) if t]
    if titles:
        report.titles = titles
    years = as_years(preferences.get("experience"))
    if years:
        report.years = years
    # Read off the role rather than off the extraction: a role that already
    # carries titles from an earlier run is not matching on criteria alone,
    # whatever this document's text happened to yield.
    if not report.titles:
        _warn_no_titles(report)

    log.info(
        "noon search filters after the save",
        extra={
            "role": report.role_id,
            "location": report.location,
            "titles": len(report.titles),
            "experience": as_text(preferences.get("experience")),
            "example_companies": len(preferences.get("required_companies_to_source_from") or []),
        },
    )
    return bool(saved)


async def set_up_sourcing(
    page: "Page",
    role_id: str,
    role_name: str,
    job_description: str,
    *,
    source: str = "public",
    start_sourcing: bool = True,
    dry_run: bool = False,
    targeting: str = "",
    fallback_must_haves: list[str] | None = None,
    brief: WizardBrief | None = None,
    role_wait_seconds: float = 0.0,
) -> SourcingReport:
    """Take the token off the live portal, then run the wizard."""
    session = await capture_session(page)
    return await run_wizard(
        session,
        role_id,
        role_name,
        job_description,
        source=source,
        start_sourcing=start_sourcing,
        dry_run=dry_run,
        targeting=targeting,
        fallback_must_haves=fallback_must_haves,
        brief=brief,
        role_wait_seconds=role_wait_seconds,
    )


async def run_wizard(
    session: NoonSession,
    role_id: str,
    role_name: str,
    job_description: str,
    *,
    source: str = "public",
    start_sourcing: bool = True,
    dry_run: bool = False,
    targeting: str = "",
    fallback_must_haves: list[str] | None = None,
    brief: WizardBrief | None = None,
    role_wait_seconds: float = 0.0,
) -> SourcingReport:
    """Run the whole wizard for one role and report what it was told.

    A dry run stops after reading the advert: `generate_params` is sent with
    `dont_save`, so noon parses the text and hands back the criteria it would
    have used without writing anything to the role. That is as far as a
    rehearsal can go — every step after it saves on arrival.

    `targeting` is prepended to the job description — see `targeting_preamble`.
    It is separate from the description rather than merged into it by the caller
    so that what noon extracted can be compared against what it was told.

    `fallback_must_haves` is what the wizard runs on when noon reads no
    requirements out of the text at all — the shared profile's essential
    skills, phrased as requirements by the caller. Before it existed such a
    role failed the whole criteria stage and was left with nothing but its
    title (the 2026-09-28 review).

    `brief` is the rest of what the caller knows (`WizardBrief`): the example
    companies and client description step 3 asks for, and fallback titles,
    years and location for when noon extracts none.

    `role_wait_seconds` is how long to wait for a just-created role to appear
    in noon's role list - see `fetch_role`.
    """
    jd = (job_description or "").strip()
    if not jd:
        raise PlatformError(
            "This document has no advert text, so there is no job description to "
            "give noon. Add an advert section, or set the criteria by hand."
        )
    if targeting.strip():
        jd = f"{targeting.strip()}\n\n{jd}"
    brief = brief or WizardBrief()

    wizard = SourcingWizard(
        session, role_id, role_name, source=source, start_sourcing=start_sourcing
    )
    report = wizard.report

    # -- step 1 --------------------------------------------------------------
    params = await wizard.read_job_description(jd, save=not dry_run)
    must_haves, promoted = tighten(
        params.get("must_haves"), params.get("nice_to_haves")
    )
    report.must_haves = must_haves
    report.promoted = promoted
    report.location = as_text(params.get("location")) or as_text(brief.location)
    report.titles = [t for t in as_lines(params.get("titles")) if t] or list(brief.titles)
    report.years = as_years(params.get("yoe")) or brief.years

    if not must_haves and fallback_must_haves:
        # noon read nothing out of the text, but the shared profile knows what
        # the role needs - better criteria from our own draft than a role with
        # a title and nothing else.
        must_haves = [m.strip() for m in fallback_must_haves if m.strip()]
        report.must_haves = must_haves
        report.promoted = []
        if must_haves:
            report.warnings.append(
                "noon extracted no requirements from this text, so the "
                "must-haves were written from the drafted sourcing profile "
                "instead - review them in the role's Control Panel"
            )
    if not must_haves:
        raise PlatformError(
            "noon found no requirements in this advert, so there is nothing to "
            "source on. Check that the advert section carries the role's "
            "requirements and not just the pitch."
        )

    if dry_run:
        # Nothing was written, so what was extracted is all there is to judge.
        if not report.location:
            _warn_no_location(report, targeting=targeting)
        if not report.titles:
            _warn_no_titles(report)
        report.warnings.append(
            f"dry run: would set {len(must_haves)} must-have(s) "
            f"({len(promoted)} promoted from nice-to-haves), "
            f"{len(brief.example_companies)} example company(ies), and let noon "
            "generate non-negotiables from them"
        )
        log.info(
            "noon sourcing dry run",
            extra={"role": role_id, "must_haves": len(must_haves)},
        )
        return report

    role = await fetch_role(session, role_id, wait_seconds=role_wait_seconds)

    # -- step 2 --------------------------------------------------------------
    await wizard.set_candidate_pool()

    # -- step 3 --------------------------------------------------------------
    # The whole "Confirm the search criteria" screen: noon's extraction where
    # it has one, the brief where it has not, the example companies resolved to
    # noon's own company records, then the one write that puts it all on the
    # role. Until 2026-10-09 this screen was skipped and roles searched the
    # world on criteria alone.
    company_ids = await wizard.resolve_companies(brief.example_companies)
    preferences = build_preferences(
        role.get("preferences"), params, brief=brief, example_company_ids=company_ids
    )
    extracted_location = list(preferences.get("location") or [])
    description = brief.client_description.strip()
    report.client_description = description

    await wizard.prepare_criteria(preferences, must_haves)
    confirmed = await wizard.confirm_criteria(
        preferences,
        must_haves,
        client_description=description,
        requires_visa_sponsorship=params.get("requires_visa_sponsorship"),
    )

    # Read back, fresh: `all_roles` would serve the copy from before the write,
    # which would make a save that worked look like one that failed.
    role = await fetch_role(session, role_id, fresh=True)
    located = _check_preferences(role, report)
    if not located and not extracted_location:
        _warn_no_location(report, targeting=targeting)

    criteria = await wizard.generate_criteria(must_haves)
    report.non_negotiables = criteria

    # -- step 4 --------------------------------------------------------------
    company_ratings = await wizard.rate_target_companies(
        preferences,
        client_description=description,
        wanted=brief.example_companies,
        employees=employee_band(confirmed),
    )

    # -- steps 5 to 7 --------------------------------------------------------
    autopilot = role.get("autopilot")
    autopilot = dict(autopilot) if isinstance(autopilot, dict) else {}

    # A search with no location is not a footnote on a successful run: every
    # candidate it returns comes from the wrong pool, and a shortlist that looks
    # ordinary is the most expensive way to find that out. So the role is saved
    # and left idle instead — the one outcome a recruiter cannot miss, and one
    # click in noon away from being right. Raising would be quieter, not louder:
    # `noon.py` catches a PlatformError from here into a warning and the row
    # still reads Posted, having already started the search.
    if extracted_location and not located:
        wizard.start_sourcing = False
        report.warnings.append(
            f"noon would not keep the location ({', '.join(extracted_location)}) "
            "on this role, so the search has NOT been started - it would have "
            "sourced from everywhere. Everything else is saved: open the role in "
            "noon, set the location in the Control Panel, and press Start."
        )

    autopilot = await wizard.select_non_negotiables(
        autopilot,
        criteria,
        preferences=preferences,
        must_haves=must_haves,
        company_ratings=company_ratings,
    )
    questions = await wizard.fetch_questions(criteria)
    autopilot = await wizard.rank(autopilot, criteria)

    answers = await wizard.answer_questions(questions)
    report.answers = answers
    skipped = [q for q, a in answers.items() if a == SKIP]
    if skipped:
        report.warnings.append(
            f"{len(skipped)} clarifying question(s) left unanswered - no option "
            "was clearly the stricter one; answer them in noon if they matter"
        )

    await wizard.finish(autopilot, answers)
    report.started_sourcing = wizard.start_sourcing

    log.info(
        "noon sourcing criteria set",
        extra={
            "role": role_id,
            "must_haves": len(must_haves),
            "promoted": len(promoted),
            "non_negotiables": len(criteria),
            "questions": len(answers),
            "example_companies": len(report.example_companies),
            "rated": report.rated_companies,
            "started": report.started_sourcing,
        },
    )
    return report
