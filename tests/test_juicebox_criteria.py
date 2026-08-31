"""Juicebox ranks criteria rather than splitting them, so the ranking is the policy."""

from __future__ import annotations

from app.platforms.criteria_ai import DraftCriteria, DraftCriterion
from app.platforms.juicebox_criteria import MAX_CRITERIA, rank_criteria


def _draft(**kwargs) -> DraftCriteria:
    return DraftCriteria(
        dealbreakers=[
            DraftCriterion(category="Hard skills", text="The candidate has deep AWS experience."),
            DraftCriterion(category="Location", text="The candidate is based in London."),
        ],
        baseline=[
            DraftCriterion(category="Seniority", text="The candidate has 5+ years in platform roles.")
        ],
        traits_to_avoid=["The candidate does not require visa sponsorship."],
        **kwargs,
    )


def test_dealbreakers_rank_above_baseline_above_disqualifiers():
    """Position is the only weighting Juicebox has, so order is the policy."""
    assert rank_criteria(_draft()) == [
        "The candidate has deep AWS experience.",
        "The candidate is based in London.",
        "The candidate has 5+ years in platform roles.",
        "The candidate does not require visa sponsorship.",
    ]


def test_a_requirement_stated_twice_does_not_take_two_slots():
    draft = _draft()
    draft.baseline.append(
        DraftCriterion(category="Hard skills", text="The candidate has deep AWS experience")
    )
    ranked = rank_criteria(draft)
    assert len([c for c in ranked if "AWS" in c]) == 1


def test_blank_criteria_are_dropped():
    draft = _draft()
    draft.baseline.append(DraftCriterion(category="Hard skills", text="   "))
    assert all(c.strip() for c in rank_criteria(draft))


def test_the_list_is_capped_so_the_ranking_stays_meaningful():
    draft = DraftCriteria(
        dealbreakers=[
            DraftCriterion(category="Hard skills", text=f"The candidate knows tool number {n}.")
            for n in range(MAX_CRITERIA + 5)
        ]
    )
    assert len(rank_criteria(draft)) == MAX_CRITERIA


def test_whitespace_is_normalised():
    draft = DraftCriteria(
        dealbreakers=[DraftCriterion(category="Hard skills", text="The  candidate\n has   AWS.")]
    )
    assert rank_criteria(draft) == ["The candidate has AWS."]
