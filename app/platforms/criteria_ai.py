"""Fill the criteria a platform's own generator left empty, from the advert.

Loxo's `Write with AI` writes candidate criteria into a job's description —
dealbreakers, baseline requirements, traits to avoid. It does not always fill
every bucket, and on a job whose advert it has not been run against it fills
none of them. The advert itself says what the role needs, so the gaps are
answerable: this asks Claude for them, in Loxo's own shape, and hands back a
`SkillDNA` with the empty buckets populated.

Two deliberate limits:

- **Only empty buckets are filled.** Whatever the platform's own generator
  produced is left exactly as it is — its wording carries the market
  intelligence ours does not, and second-guessing it would be worse than
  useless. `missing_fields()` decides what to ask for.
- **Nice-to-haves are never generated.** Every criterion this produces is one
  that must filter, because the tightening policy would promote a nice-to-have
  into a dealbreaker immediately afterwards
  ([loxo_criteria.tighten](loxo_criteria.py)). Asking for a bucket only to empty
  it again would waste a call and invite the model to pad the list.

Without `ANTHROPIC_API_KEY` this is skipped rather than fatal: the run reports
which buckets stayed empty and carries on with what the platform generated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PipelineError
from app.platforms.loxo_criteria import (
    AVOID,
    BASELINE,
    CATEGORIES,
    DEALBREAKER,
    Criterion,
    SkillDNA,
    missing_fields,
)

log = get_logger(__name__)

# Long enough to hold the schema and a dozen criteria; nowhere near the cap.
MAX_TOKENS = 4_000

SYSTEM = """You write candidate screening criteria for a technical recruiter.

You are given a job advert. Extract the criteria a sourcing agent should filter
candidates on. Work only from what the advert states or plainly implies — do not
invent requirements, and do not restate the company's pitch as a requirement.

Rules:
- Each criterion is one testable fact about a candidate's history, written as a
  recruiter would phrase it: "Built payments infrastructure at a regulated B2B
  company", not "Passionate about fintech".
- `dealbreakers` are the requirements a candidate must meet. If the advert calls
  something essential, required, or a must-have, it belongs here.
- `baseline` is the floor: years of experience, seniority, degree requirements.
- `traits_to_avoid` are disqualifiers the advert states or clearly implies.
- Prefer few, sharp criteria over many vague ones. Omit a bucket entirely rather
  than padding it."""


class DraftCriterion(BaseModel):
    category: str = Field(
        description=f"One of: {', '.join(CATEGORIES)}. Use the closest fit."
    )
    text: str = Field(description="The criterion, as one sentence.")


class DraftCriteria(BaseModel):
    dealbreakers: list[DraftCriterion] = Field(default_factory=list)
    baseline: list[DraftCriterion] = Field(default_factory=list)
    traits_to_avoid: list[str] = Field(default_factory=list)


_BUCKET_FIELDS = {
    DEALBREAKER: "dealbreakers",
    BASELINE: "baseline",
    AVOID: "traits_to_avoid",
}


def configured(settings: Settings | None = None) -> bool:
    return bool((settings or get_settings()).anthropic_api_key)


async def draft_criteria(
    advert_text: str,
    *,
    wanted: list[str],
    role_name: str = "",
    phrasing: str = "",
    settings: Settings | None = None,
) -> DraftCriteria:
    """Ask Claude for the named buckets, validated against the schema.

    `phrasing` is appended to the instructions when a platform wants the
    criteria worded its own way — Juicebox scores full sentences about one
    candidate, Loxo lists recruiter shorthand. Asking for the right shape is
    more reliable than reformatting the answer afterwards.
    """
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        raise PipelineError(
            "ANTHROPIC_API_KEY is not set, so the empty criteria cannot be "
            "filled from the advert. Set it, or accept the criteria the "
            "platform generated on its own."
        )

    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise PipelineError(
            "The anthropic package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    asked = ", ".join(_BUCKET_FIELDS[name] for name in wanted if name in _BUCKET_FIELDS)
    prompt = (
        f"Role: {role_name or 'unnamed'}\n\n"
        f"Fill only these: {asked}. Leave every other list empty.\n"
        + (f"{phrasing.strip()}\n" if phrasing.strip() else "")
        + f"\n<advert>\n{advert_text.strip()}\n</advert>"
    )

    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=4)
    try:
        response = await client.messages.parse(
            model=settings.criteria_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=DraftCriteria,
        )
    except Exception as exc:  # surfaced to the recruiter on the row
        raise PipelineError(f"Claude could not draft the criteria: {exc}") from exc
    finally:
        await client.close()

    draft = response.parsed_output
    if draft is None:
        raise PipelineError("Claude returned no criteria for this advert.")

    log.info(
        "criteria drafted from the advert",
        extra={
            "model": settings.criteria_model,
            "dealbreakers": len(draft.dealbreakers),
            "baseline": len(draft.baseline),
            "avoid": len(draft.traits_to_avoid),
        },
    )
    return draft


async def fill_gaps(
    dna: SkillDNA,
    advert_text: str,
    *,
    role_name: str = "",
    settings: Settings | None = None,
) -> tuple[SkillDNA, list[str]]:
    """Populate whichever of the three filtering buckets came back empty.

    Returns the amended DNA and the names of the buckets that were filled. A
    bucket the platform already populated is never touched, and an unset API key
    leaves everything as it was rather than failing the run.
    """
    settings = settings or get_settings()
    gaps = [name for name in missing_fields(dna) if name in _BUCKET_FIELDS]
    if not gaps:
        return dna, []

    if not settings.anthropic_api_key:
        log.info("criteria gaps left unfilled - no API key", extra={"gaps": gaps})
        return dna, []

    draft = await draft_criteria(
        advert_text, wanted=gaps, role_name=role_name, settings=settings
    )

    filled = SkillDNA(
        advert_html=dna.advert_html,
        buckets={name: list(items) for name, items in dna.buckets.items()},
    )

    added: list[str] = []
    if DEALBREAKER in gaps and draft.dealbreakers:
        filled.buckets[DEALBREAKER] = [
            Criterion(text=item.text, category=item.category)
            for item in draft.dealbreakers
        ]
        added.append(DEALBREAKER)
    if BASELINE in gaps and draft.baseline:
        filled.buckets[BASELINE] = [
            Criterion(text=item.text, category=item.category) for item in draft.baseline
        ]
        added.append(BASELINE)
    if AVOID in gaps and draft.traits_to_avoid:
        filled.buckets[AVOID] = [Criterion(text=text) for text in draft.traits_to_avoid]
        added.append(AVOID)

    log.info("criteria gaps filled", extra={"filled": added, "asked_for": gaps})
    return filled, added
