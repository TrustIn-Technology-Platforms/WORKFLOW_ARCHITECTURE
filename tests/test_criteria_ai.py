"""Filling empty criteria buckets from the advert.

The model call itself is stubbed - what matters here is the contract around it:
only empty buckets are touched, a missing key is not a failure, and the drafted
items land in the right bucket with their category intact.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.models import PipelineError
from app.platforms import criteria_ai
from app.platforms.criteria_ai import (
    DraftCriteria,
    DraftCriterion,
    configured,
    fill_gaps,
)
from app.platforms.loxo_criteria import (
    AVOID,
    BASELINE,
    DEALBREAKER,
    NICE_TO_HAVE,
    Criterion,
    SkillDNA,
    parse_skill_dna,
)

ADVERT = """
<p>About Acme</p>
<p>We need a platform engineer with 5 years of Kubernetes experience.</p>
"""

DRAFT = DraftCriteria(
    dealbreakers=[
        DraftCriterion(category="Hard skills", text="Deep hands-on Kubernetes experience"),
        DraftCriterion(category="Location", text="Based in London or willing to relocate"),
    ],
    baseline=[DraftCriterion(category="Seniority", text="5+ years in platform engineering")],
    traits_to_avoid=["Requires visa sponsorship"],
)


def _settings(**kwargs) -> Settings:
    return Settings(anthropic_api_key="test-key", **kwargs)


def _stub(monkeypatch, draft=DRAFT, seen=None):
    async def fake(advert_text, *, wanted, role_name="", settings=None):
        if seen is not None:
            seen.extend(wanted)
        return draft

    monkeypatch.setattr(criteria_ai, "draft_criteria", fake)


def test_a_key_is_what_makes_it_configured():
    assert configured(_settings()) is True
    assert configured(Settings(anthropic_api_key="")) is False


def test_every_empty_bucket_is_filled_from_the_advert(monkeypatch):
    asked: list[str] = []
    _stub(monkeypatch, seen=asked)
    dna = parse_skill_dna(ADVERT)

    filled, added = asyncio.run(fill_gaps(dna, "advert text", settings=_settings()))

    assert added == [DEALBREAKER, BASELINE, AVOID]
    assert [c.text for c in filled.items(DEALBREAKER)] == [
        "Deep hands-on Kubernetes experience",
        "Based in London or willing to relocate",
    ]
    assert filled.items(DEALBREAKER)[0].category == "Hard skills"
    assert [c.text for c in filled.items(BASELINE)] == ["5+ years in platform engineering"]
    assert [c.text for c in filled.items(AVOID)] == ["Requires visa sponsorship"]
    # Nice-to-haves are never requested: the tightening policy would empty the
    # bucket again a moment later.
    assert NICE_TO_HAVE not in asked
    assert filled.items(NICE_TO_HAVE) == []


def test_a_bucket_the_platform_already_filled_is_left_alone(monkeypatch):
    """Loxo's own wording carries market intelligence ours does not."""
    asked: list[str] = []
    _stub(monkeypatch, seen=asked)
    dna = SkillDNA(
        advert_html="<p>advert</p>",
        buckets={
            DEALBREAKER: [Criterion(text="Built exchange-grade matching engines")],
            BASELINE: [],
            NICE_TO_HAVE: [],
            AVOID: [],
        },
    )

    filled, added = asyncio.run(fill_gaps(dna, "advert text", settings=_settings()))

    assert [c.text for c in filled.items(DEALBREAKER)] == [
        "Built exchange-grade matching engines"
    ]
    assert DEALBREAKER not in asked
    assert DEALBREAKER not in added
    assert added == [BASELINE, AVOID]


def test_nothing_to_fill_means_no_call(monkeypatch):
    called = False

    async def fake(*args, **kwargs):
        nonlocal called
        called = True
        return DRAFT

    monkeypatch.setattr(criteria_ai, "draft_criteria", fake)
    dna = SkillDNA(
        advert_html="",
        buckets={
            DEALBREAKER: [Criterion(text="a")],
            BASELINE: [Criterion(text="b")],
            NICE_TO_HAVE: [Criterion(text="c")],
            AVOID: [Criterion(text="d")],
        },
    )

    filled, added = asyncio.run(fill_gaps(dna, "advert", settings=_settings()))

    assert added == []
    assert called is False
    assert filled is dna


def test_no_api_key_leaves_the_gaps_rather_than_failing(monkeypatch):
    """A missing key must not fail a run whose campaign already posted."""
    called = False

    async def fake(*args, **kwargs):
        nonlocal called
        called = True
        return DRAFT

    monkeypatch.setattr(criteria_ai, "draft_criteria", fake)
    dna = parse_skill_dna(ADVERT)

    filled, added = asyncio.run(
        fill_gaps(dna, "advert", settings=Settings(anthropic_api_key=""))
    )

    assert added == []
    assert called is False
    assert filled.has_criteria is False


def test_drafting_without_a_key_is_an_error_worth_reading():
    """Called directly, it should say what to do rather than fail obscurely."""
    with pytest.raises(PipelineError, match="ANTHROPIC_API_KEY"):
        asyncio.run(
            criteria_ai.draft_criteria(
                "advert", wanted=[DEALBREAKER], settings=Settings(anthropic_api_key="")
            )
        )


def test_an_empty_draft_does_not_wipe_a_bucket(monkeypatch):
    _stub(monkeypatch, draft=DraftCriteria())
    dna = parse_skill_dna(ADVERT)

    filled, added = asyncio.run(fill_gaps(dna, "advert", settings=_settings()))

    assert added == []
    assert filled.has_criteria is False
