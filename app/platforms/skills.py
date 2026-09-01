"""The skills list a job board asks for, which the document does not carry.

Wellfound's advert form has a `Skills` field — a tag input bound to Wellfound's
own vocabulary, filled one skill at a time. Our documents have no skills
section: the stack appears only inside the advert prose and, by TrustIn's own
title convention, in the job title itself ("… / AWS, Python, K8s, Terraform").
So the list has to be assembled.

Two sources, in this order:

1. **The Notion row's `Skills` column**, when a recruiter has filled it. A human
   naming the stack beats anything inferred, and it is the same "column fills a
   gap the document left" pattern as Location and Salary.
2. **Claude reading the advert**, when the column is empty.

Neither is required. The field is optional on the form, and an advert with no
skills is better than an advert with invented ones — so no key, no advert text
or a failed call leaves the list empty and the recipe step skips itself. The
board's own taxonomy is the final filter: `action_tags` drops any skill Wellfound
does not offer, so a plausible-but-unknown name costs nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import ParsedDocument

log = get_logger(__name__)

# Wellfound's own posts run to five or six skills; ten is past the point where a
# tag adds any signal, and every extra one is another autocomplete round trip.
MAX_SKILLS = 10

# Small answer, small budget.
MAX_TOKENS = 1_000

SYSTEM = """You extract the skills tag list for a job advert on a job board.

You are given a job advert. Return the technical skills, tools and languages a
candidate would be expected to have, as a job board's tag vocabulary would name
them.

Rules:
- Use the plain, canonical name a job board would list: "Python", "Kubernetes",
  "PostgreSQL", "React" — not "Python 3.12", "k8s at scale", or "strong Python".
- Only skills the advert states or plainly implies. Do not pad the list with
  adjacent technologies the advert never mentions.
- Name concrete things: languages, frameworks, databases, clouds, tools. Not
  soft skills, not seniority, not "communication".
- Order them by how central they are to the role.
- Fewer, right ones. An empty list is a valid answer for an advert with no
  technical content."""


class DraftSkills(BaseModel):
    skills: list[str] = Field(
        default_factory=list,
        description="Canonical skill names, most central first.",
    )


def split_skills(text: str, *, limit: int = MAX_SKILLS) -> list[str]:
    """A human-written list into clean names, deduplicated, order kept.

    Commas, semicolons, pipes and newlines separate. `/` deliberately does not:
    it appears inside skill names ("CI/CD", "TCP/IP") more often than between
    them, and a recruiter separating with slashes will still be caught by the
    comma they use everywhere else.
    """
    working = text or ""
    for separator in (";", "|", ","):
        working = working.replace(separator, "\n")
    seen: set[str] = set()
    values: list[str] = []
    for line in working.split("\n"):
        value = line.strip().strip("-•·").strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return values[:limit] if limit > 0 else values


async def draft_skills(
    advert_text: str,
    *,
    title: str = "",
    settings: Settings | None = None,
) -> list[str]:
    """Ask Claude for the skills the advert implies. Never raises.

    Deliberately swallows its own failures. This fills an optional field on one
    platform; a rate limit or a schema change should cost the advert its tags,
    not the row its posting. What went wrong is logged.
    """
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        log.info("skills not drafted - no ANTHROPIC_API_KEY")
        return []
    if not advert_text.strip():
        return []

    try:
        from anthropic import AsyncAnthropic
    except ImportError:  # pragma: no cover - dependency guard
        log.warning("skills not drafted - anthropic package not installed")
        return []

    prompt = (
        f"Job title: {title or 'unnamed'}\n\n"
        f"<advert>\n{advert_text.strip()}\n</advert>"
    )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.parse(
            model=settings.criteria_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=DraftSkills,
        )
        draft = response.parsed_output
    except Exception as exc:
        log.warning("skills could not be drafted", extra={"error": str(exc)[:200]})
        return []
    finally:
        await client.close()

    if draft is None:
        return []
    skills = split_skills("\n".join(draft.skills))
    log.info(
        "skills drafted from the advert",
        extra={"model": settings.criteria_model, "skills": skills},
    )
    return skills


async def ensure_skills(
    document: ParsedDocument,
    settings: Settings | None = None,
) -> list[str]:
    """Put a skills list on the advert if it has none. Returns what was added.

    Called once per row by the orchestrator, before any platform runs, so that
    the Notion column and the drafted fallback are resolved in one place rather
    than per recipe. A `tags` list already present — from the column — wins.

    Every advert is covered, not just the general one: a board advert built at
    parse time never sees what enrichment or drafting adds later, and Wellfound
    reads *its* advert — so tags left only on the general advert would silently
    vanish from the one platform that uses them (the location equivalent of
    this failed a live row on 2026-09-01).
    """
    adverts = [
        a for a in (document.advert, *document.platform_adverts.values())
        if a is not None
    ]
    missing = [a for a in adverts if not a.tags]
    if not missing:
        return []

    # A list one advert already carries — the Skills column, spread by
    # enrich_advert — is copied before Claude is asked for anything.
    donor = next((a.tags for a in adverts if a.tags), None)
    if donor:
        for advert in missing:
            advert.tags = list(donor)
        return []

    settings = settings or get_settings()
    # Drafted from the client's JD when the document carries one, and the
    # general advert otherwise — `job_description` is exactly that choice. The
    # advert sells the role and softens the stack it is selling; the JD states
    # it, which is the same reason the sourcing criteria read it (D-018). A
    # board section is never the source: it is deliberately cut down, where the
    # general advert is the fullest statement of the role.
    source = document.advert or missing[0]
    text = document.job_description or source.body_text
    skills = await draft_skills(text, title=source.title, settings=settings)
    for advert in missing:
        advert.tags = list(skills)
    return skills
