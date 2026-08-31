"""Loxo's Skill DNA: parsing it out of a description, and tightening it.

The fixture below is the shape Loxo's own generator produces, observed on a real
job on 2026-08-31 — bucket headings as bare paragraphs, criterion types grouped
under them, an "Updated" marker after edited items, spacer paragraphs
throughout. The wording here is invented; only the structure is copied, because
the real one is client copy.
"""

from __future__ import annotations

from app.platforms.loxo_criteria import (
    AVOID,
    BASELINE,
    DEALBREAKER,
    NICE_TO_HAVE,
    missing_fields,
    parse_skill_dna,
    render,
    tighten,
)

GENERATED = """
<p>About Acme</p>
<p>Acme builds settlement infrastructure for freight.</p>
<p><br></p>
<p><strong>What you will be doing</strong></p>
<ul><li>Running the platform team</li><li>Migrating to AWS</li></ul>
<p><br></p>
<p>Dealbreaker</p>
<p><br></p>
<p>Work experience</p>
<p>Built payments infrastructure at a <strong>regulated</strong> B2B company</p>
<p>Updated</p>
<p>Hard skills</p>
<p>Deep hands-on AWS experience</p>
<p>Updated</p>
<p><br></p>
<p>Baseline</p>
<p><br></p>
<p>Seniority</p>
<p>5 - 12 years building infrastructure</p>
<p>Hard skills</p>
<p>CI/CD pipeline design and observability tooling</p>
<p>Updated</p>
<p><br></p>
<p>Nice-to-have</p>
<p><br></p>
<p>Work experience</p>
<p>0-to-1 experience standing up security infrastructure</p>
<p>Updated</p>
<p>Managed SOC 2 compliance in production</p>
<p><br></p>
<p>Traits to avoid</p>
<p><br></p>
<p>Contractors or consultants</p>
<p>Updated</p>
<p>Job hoppers with no growth at any single company</p>
"""

ADVERT_ONLY = """
<p>About Acme</p>
<p>Acme builds settlement infrastructure for freight.</p>
<p>We are hiring a platform engineer.</p>
"""


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------


def test_the_four_buckets_come_back_with_their_items():
    dna = parse_skill_dna(GENERATED)

    assert [c.text for c in dna.items(DEALBREAKER)] == [
        "Built payments infrastructure at a regulated B2B company",
        "Deep hands-on AWS experience",
    ]
    assert [c.text for c in dna.items(BASELINE)] == [
        "5 - 12 years building infrastructure",
        "CI/CD pipeline design and observability tooling",
    ]
    assert len(dna.items(NICE_TO_HAVE)) == 2
    assert len(dna.items(AVOID)) == 2


def test_each_item_keeps_the_category_it_sat_under():
    dna = parse_skill_dna(GENERATED)
    dealbreakers = dna.items(DEALBREAKER)

    assert dealbreakers[0].category == "Work experience"
    assert dealbreakers[1].category == "Hard skills"
    # "Traits to avoid" lists items bare, with no category heading.
    assert dna.items(AVOID)[0].category == ""


def test_the_edited_marker_is_not_a_criterion():
    """Loxo stamps 'Updated' after an item someone edited."""
    dna = parse_skill_dna(GENERATED)
    every = [c.text for items in dna.buckets.values() for c in items]
    assert "Updated" not in every


def test_the_advert_above_the_criteria_is_kept_verbatim():
    dna = parse_skill_dna(GENERATED)

    assert "Acme builds settlement infrastructure" in dna.advert_html
    assert "<strong>What you will be doing</strong>" in dna.advert_html
    assert "Migrating to AWS" in dna.advert_html
    # The criteria are not left behind in the advert half.
    assert "Dealbreaker" not in dna.advert_html


def test_a_job_with_no_criteria_is_all_advert():
    """The state a job is in before anyone runs Write with AI on it."""
    dna = parse_skill_dna(ADVERT_ONLY)

    assert dna.has_criteria is False
    assert "We are hiring a platform engineer." in dna.advert_html
    assert missing_fields(dna) == [DEALBREAKER, BASELINE, NICE_TO_HAVE, AVOID]


# ----------------------------------------------------------------------
# the policy
# ----------------------------------------------------------------------


def test_every_nice_to_have_becomes_a_dealbreaker():
    tightened, promoted = tighten(parse_skill_dna(GENERATED))

    assert [c.text for c in tightened.items(DEALBREAKER)] == [
        "Built payments infrastructure at a regulated B2B company",
        "Deep hands-on AWS experience",
        "0-to-1 experience standing up security infrastructure",
        "Managed SOC 2 compliance in production",
    ]
    assert tightened.items(NICE_TO_HAVE) == []
    assert len(promoted) == 2


def test_baseline_and_avoid_are_left_alone():
    """Both already filter. Promoting them would say nothing new."""
    original = parse_skill_dna(GENERATED)
    tightened, _ = tighten(original)

    assert tightened.items(BASELINE) == original.items(BASELINE)
    assert tightened.items(AVOID) == original.items(AVOID)


def test_a_requirement_in_both_buckets_is_not_duplicated():
    both = GENERATED.replace(
        "<p>0-to-1 experience standing up security infrastructure</p>",
        "<p>Deep hands-​on AWS experience</p>",  # soft hyphen, as Loxo's editor writes it
    )
    tightened, promoted = tighten(parse_skill_dna(both))

    texts = [c.text for c in tightened.items(DEALBREAKER)]
    assert len(texts) == 3
    assert len(promoted) == 1


def test_tightening_does_not_touch_the_advert():
    tightened, _ = tighten(parse_skill_dna(GENERATED))
    assert "Acme builds settlement infrastructure" in tightened.advert_html


# ----------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------


def test_rendered_html_parses_back_to_what_went_in():
    dna = parse_skill_dna(GENERATED)
    again = parse_skill_dna(render(dna))

    for bucket in (DEALBREAKER, BASELINE, NICE_TO_HAVE, AVOID):
        assert [c.text for c in again.items(bucket)] == [c.text for c in dna.items(bucket)]
        assert [c.category for c in again.items(bucket)] == [
            c.category for c in dna.items(bucket)
        ]


def test_an_emptied_bucket_leaves_no_heading_behind():
    tightened, _ = tighten(parse_skill_dna(GENERATED))
    html = render(tightened)

    assert "Nice-to-have" not in html
    assert "Dealbreaker" in html
    assert "Traits to avoid" in html


def test_the_advert_survives_the_round_trip():
    html = render(parse_skill_dna(GENERATED))
    assert "<strong>What you will be doing</strong>" in html
    assert "Migrating to AWS" in html
