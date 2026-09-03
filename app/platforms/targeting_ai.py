"""Search-filter targeting a sourcing screen asks for: titles, skills, years, companies.

Loxo's Source screen and Juicebox's search filters both want the same lists:
the job titles a matching candidate holds *today*, the hard skills to filter
on, the years of experience the role expects, and the companies a strong
candidate has worked at. Both platforms seed them thinly — Loxo starts from
the job title alone, Juicebox from whatever the JD paste extracts — and
Sohaib's review of the first live searches (2026-09-01) found them empty or
near-empty: no skills, one title, "not even 20 percent configured".

The client's JD states what the criteria layers only rank by, so it is the
source here too (D-018). Distinct from `criteria_ai` (free-text screening
criteria) and `skills.py` (job-board tags for an advert): filter skills are
searchable keywords a profile would carry, and titles are a list of what to
match, not a description of the role.

Target companies follow a rubric of their own (D-020). A candidate who has
built the same thing at a company of the same size is the one a recruiter
wants, and "the same size" for a startup means the same funding stage. So the
stage is read off the JD when it states one (`stage_from_text`), asked for
when it does not, and the list is drawn from companies at that stage or the
one after it - the people who have already seen the scale the client is
heading for.

Same key policy as every other drafting module: no ANTHROPIC_API_KEY means the
caller gets empty lists and reports the gap, never a failed run.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.logging_conf import get_logger

log = get_logger(__name__)

MAX_TOKENS = 1_500

# Enough breadth to widen a search without drowning it. Loxo's own UI caps
# nothing, but every extra chip is an autocomplete round trip.
MAX_TITLES = 10
MAX_SKILLS = 12
# Past-company filters narrow hard: a candidate must have worked at one of
# them. Fifteen well-chosen names keep a pool; fifty would be a wish list.
MAX_COMPANIES = 15
# A years figure outside this range is a parsing accident, not a requirement.
MAX_YEARS = 40

SYSTEM = """You configure candidate-search filters for a technical recruiter.

You are given a job description. Return the search filters:

`similar_titles` - job titles a strong candidate holds TODAY, most likely
first. These are search filters over current titles, so:
- Real, common titles as they appear on profiles: "Platform Engineer",
  "Site Reliability Engineer", "DevOps Engineer" - not invented hybrids.
- Cover the adjacent titles the same person hires under: infrastructure,
  platform, SRE, DevOps variants for an infra role.
- Include seniority variants only when the JD demands that seniority.

`skills` - hard, searchable skills a matching profile would list: languages,
clouds, tooling, frameworks, compliance regimes. The stack the JD states or
plainly implies, most central first. No soft skills, no duties.

`min_years` / `max_years` - the TOTAL years of professional experience the
role asks for, as the JD states them ("5+ years" -> min 5, no max; "3-5
years" -> 3 and 5). Use the overall requirement, not years with one tool.
Leave both null when the JD gives no figure; never guess one.

Fewer, right entries beat long padded lists."""

COMPANIES_SYSTEM = """You build a target-company list for a technical recruiter's candidate search.

You are given a job description, the hiring company's name and location, and
its funding stage - either stated in the description or marked unknown.

Return:

`stage` - the hiring company's funding stage. If one was given, repeat it
exactly. If it was unknown, infer the most likely stage from the description
(founding-team language, headcount, "scale" language, funding mentions) and
from what you know of the company, choosing from: Pre-seed, Seed, Series A,
Series B, Series C, Series D+, Growth stage, Public, Bootstrapped. Say
"Unknown" only when there is genuinely nothing to go on.

`stage_basis` - "stated" when the stage was given to you, "inferred" when you
worked it out, "unknown" when you could not.

`companies` - real companies whose engineers would be strong candidates for
this role, most relevant first:
- the same sector or product category, solving similar engineering problems;
- the same funding stage, or the one after it - candidates who have already
  seen the scale the hiring company is heading for;
- the same country or region when a location is given;
- named as they appear on LinkedIn profiles: the common name, no legal
  suffixes ("Stripe", not "Stripe, Inc.").
Exclude the hiring company itself and its subsidiaries. Exclude big-tech
names unless the stage is Public. Never invent a company; leave the list
short rather than pad it."""

# The stage as JDs actually write it. Series letters win over the vaguer
# words, and the latest letter wins when a JD narrates its funding history.
_SERIES = re.compile(r"\bseries[\s-]?([a-h])\b(\+)?", re.IGNORECASE)
_SEED = re.compile(
    r"\bpre[\s-]?seed\b"
    r"|\bseed[\s-]?(?:stage|round|funded|funding|startup|company|investment|capital)\b"
    r"|\b(?:raised|closed|announced|secured)\s+(?:a|an|our|its|their)?\s*(?:\$?[\d.]+\s*[mk]?\s+)?seed\b"
    r"|\bseed[\s-]?stage\b",
    re.IGNORECASE,
)
_OTHER = (
    (re.compile(r"\bpublicly[\s-]?(?:traded|listed)\b|\bpublic company\b|\bpost[\s-]?ipo\b|\b(?:nasdaq|nyse)[\s:-]", re.IGNORECASE), "Public"),
    (re.compile(r"\bgrowth[\s-]?stage\b", re.IGNORECASE), "Growth stage"),
    (re.compile(r"\blate[\s-]?stage\b", re.IGNORECASE), "Late stage"),
    (re.compile(r"\bbootstrapped\b|\bself[\s-]?funded\b", re.IGNORECASE), "Bootstrapped"),
    (re.compile(r"\bearly[\s-]?stage\b", re.IGNORECASE), "Early stage"),
)


class SearchTargeting(BaseModel):
    similar_titles: list[str] = Field(
        default_factory=list,
        description="Job titles matching candidates hold today, most likely first.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Hard searchable skills, most central first.",
    )
    min_years: int | None = Field(
        default=None,
        description="Minimum total years of experience the JD asks for; null when it gives none.",
    )
    max_years: int | None = Field(
        default=None,
        description="Maximum total years of experience the JD asks for; null when open-ended.",
    )


class CompanyTargeting(BaseModel):
    stage: str = Field(
        default="Unknown",
        description="The hiring company's funding stage, stated or inferred.",
    )
    stage_basis: str = Field(
        default="unknown",
        description="stated | inferred | unknown",
    )
    companies: list[str] = Field(
        default_factory=list,
        description="Target companies at the same stage or one later, most relevant first.",
    )

    @property
    def inferred(self) -> bool:
        return self.stage_basis == "inferred"


def configured(settings: Settings | None = None) -> bool:
    return bool((settings or get_settings()).anthropic_api_key)


def stage_from_text(*texts: str) -> str | None:
    """The funding stage a JD states, normalised, or None when it states none.

    The client's stage is a fact about the client, so it is looked for
    wherever the document mentions it — the JD first, then the advert
    ("a Series B MarTech startup" is how the adverts tend to put it). A
    Series letter beats the vaguer words, and when a JD narrates its funding
    history ("raised our Series A in 2023 and Series B this year") the latest
    round is the current stage.
    """
    for text in texts:
        if not text or not text.strip():
            continue
        letters = [match.group(1).upper() for match in _SERIES.finditer(text)]
        if letters:
            return f"Series {max(letters)}"
        if _SEED.search(text):
            return "Pre-seed" if re.search(r"\bpre[\s-]?seed\b", text, re.IGNORECASE) else "Seed"
        for pattern, label in _OTHER:
            if pattern.search(text):
                return label
    return None


async def draft_targeting(
    job_description: str,
    *,
    role_title: str = "",
    settings: Settings | None = None,
) -> SearchTargeting:
    """Titles, skills and years for a search, from the JD. Never raises.

    Swallows its own failures the way `draft_skills` does: this fills search
    filters, and a rate limit should cost the search its breadth, not the row
    its posting. What went wrong is logged, and empty lists tell the caller to
    say so on the row.
    """
    empty = SearchTargeting()
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        log.info("targeting not drafted - no ANTHROPIC_API_KEY")
        return empty
    if not job_description.strip():
        return empty

    try:
        from anthropic import AsyncAnthropic
    except ImportError:  # pragma: no cover - dependency guard
        log.warning("targeting not drafted - anthropic package not installed")
        return empty

    prompt = (
        f"Role: {role_title or 'unnamed'}\n\n"
        f"<job_description>\n{job_description.strip()}\n</job_description>"
    )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.parse(
            model=settings.criteria_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=SearchTargeting,
        )
        draft = response.parsed_output
    except Exception as exc:
        log.warning("targeting could not be drafted", extra={"error": str(exc)[:200]})
        return empty
    finally:
        await client.close()

    if draft is None:
        return empty

    min_years, max_years = _years(draft.min_years, draft.max_years)
    result = SearchTargeting(
        similar_titles=_clean(draft.similar_titles, MAX_TITLES),
        skills=_clean(draft.skills, MAX_SKILLS),
        min_years=min_years,
        max_years=max_years,
    )
    log.info(
        "search targeting drafted",
        extra={
            "model": settings.criteria_model,
            "titles": result.similar_titles,
            "skills": result.skills,
            "years": [result.min_years, result.max_years],
        },
    )
    return result


async def draft_companies(
    job_description: str,
    *,
    company: str = "",
    stage: str | None = None,
    location: str = "",
    role_title: str = "",
    limit: int = MAX_COMPANIES,
    settings: Settings | None = None,
) -> CompanyTargeting:
    """Target companies at the client's stage or one later, from the JD. Never raises.

    `stage` is what `stage_from_text` found, or None. When it is given the
    model is told to keep it; when it is not, the model infers one and the
    result says so through `stage_basis`, so the caller can warn the recruiter
    that the list rests on a guess.
    """
    empty = CompanyTargeting()
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        log.info("target companies not drafted - no ANTHROPIC_API_KEY")
        return empty
    if not job_description.strip():
        return empty

    try:
        from anthropic import AsyncAnthropic
    except ImportError:  # pragma: no cover - dependency guard
        log.warning("target companies not drafted - anthropic package not installed")
        return empty

    stage_line = (
        f"Funding stage: {stage} (stated in the description - keep it)"
        if stage
        else "Funding stage: unknown - infer it and say so in stage_basis"
    )
    prompt = (
        f"Hiring company: {company or 'not named'}\n"
        f"Role: {role_title or 'unnamed'}\n"
        f"Location: {location or 'not given'}\n"
        f"{stage_line}\n"
        f"Return up to {limit} companies.\n\n"
        f"<job_description>\n{job_description.strip()}\n</job_description>"
    )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.parse(
            model=settings.criteria_model,
            max_tokens=MAX_TOKENS,
            system=COMPANIES_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=CompanyTargeting,
        )
        draft = response.parsed_output
    except Exception as exc:
        log.warning("target companies could not be drafted", extra={"error": str(exc)[:200]})
        return empty
    finally:
        await client.close()

    if draft is None:
        return empty

    result = CompanyTargeting(
        stage=(stage or draft.stage or "Unknown").strip(),
        stage_basis="stated" if stage else (draft.stage_basis or "unknown").strip().lower(),
        companies=_clean_companies(draft.companies, company, limit),
    )
    log.info(
        "target companies drafted",
        extra={
            "model": settings.criteria_model,
            "stage": result.stage,
            "basis": result.stage_basis,
            "companies": result.companies,
        },
    )
    return result


def _clean(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = " ".join(str(value).split())
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned[:limit]


def _years(min_years: int | None, max_years: int | None) -> tuple[int | None, int | None]:
    """Keep a years pair only when it reads as a requirement.

    A negative or 200-year figure is the model misreading a date; a max below
    the min is a swapped pair. Drop what does not make sense rather than write
    a filter nobody asked for.
    """
    lo = min_years if isinstance(min_years, int) and 0 <= min_years <= MAX_YEARS else None
    hi = max_years if isinstance(max_years, int) and 0 <= max_years <= MAX_YEARS else None
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    if lo is None and hi is None:
        return None, None
    return lo, hi


def _company_key(name: str) -> str:
    """A company name reduced to what identifies it, for de-duplication.

    Legal suffixes and punctuation are noise: "Stripe, Inc." and "Stripe" are
    one company, and Loxo's autocomplete lists the plain name.
    """
    text = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    text = re.sub(
        r"\b(?:inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|ag|sa|bv|pty|holdings)\b",
        " ",
        text,
    )
    return " ".join(text.split())


def _clean_companies(
    values: list[str], hiring_company: str, limit: int = MAX_COMPANIES
) -> list[str]:
    """Distinct real-looking names, never the client itself."""
    own = _company_key(hiring_company) if hiring_company else ""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = " ".join(str(value).split()).strip(" .,;")
        key = _company_key(item)
        # "Axle" and "Axle Insurance" are the client twice over.
        is_client = bool(own) and (
            key == own or key.startswith(own + " ") or own.startswith(key + " ")
        )
        if not key or key in seen or is_client:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned[:limit]
