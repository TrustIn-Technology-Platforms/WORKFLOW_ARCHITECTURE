"""Search-filter targeting a sourcing screen asks for: similar titles, skills.

Loxo's Source screen and Juicebox's search filters both want the same two
lists: the job titles a matching candidate holds *today*, and the hard skills
to filter on. Both platforms seed them thinly — Loxo starts from the job title
alone, Juicebox from whatever the JD paste extracts — and Sohaib's review of the
first live searches (2026-09-01) found them empty or near-empty: no skills, one
title, "not even 20 percent configured".

The client's JD states what the criteria layers only rank by, so it is the
source here too (D-018). Distinct from `criteria_ai` (free-text screening
criteria) and `skills.py` (job-board tags for an advert): filter skills are
searchable keywords a profile would carry, and titles are a list of what to
match, not a description of the role.

Same key policy as every other drafting module: no ANTHROPIC_API_KEY means the
caller gets empty lists and reports the gap, never a failed run.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.logging_conf import get_logger

log = get_logger(__name__)

MAX_TOKENS = 1_500

# Enough breadth to widen a search without drowning it. Loxo's own UI caps
# nothing, but every extra chip is an autocomplete round trip.
MAX_TITLES = 10
MAX_SKILLS = 12

SYSTEM = """You configure candidate-search filters for a technical recruiter.

You are given a job description. Return two lists for the search:

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

Fewer, right entries beat long padded lists."""


class SearchTargeting(BaseModel):
    similar_titles: list[str] = Field(
        default_factory=list,
        description="Job titles matching candidates hold today, most likely first.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Hard searchable skills, most central first.",
    )


def configured(settings: Settings | None = None) -> bool:
    return bool((settings or get_settings()).anthropic_api_key)


async def draft_targeting(
    job_description: str,
    *,
    role_title: str = "",
    settings: Settings | None = None,
) -> SearchTargeting:
    """Titles and skills for a search, from the JD. Never raises.

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

    result = SearchTargeting(
        similar_titles=_clean(draft.similar_titles, MAX_TITLES),
        skills=_clean(draft.skills, MAX_SKILLS),
    )
    log.info(
        "search targeting drafted",
        extra={
            "model": settings.criteria_model,
            "titles": result.similar_titles,
            "skills": result.skills,
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
