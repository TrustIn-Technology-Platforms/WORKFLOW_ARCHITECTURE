"""The Source screen writer's pure parts: band mapping, company matching, the summary.

The browser half is proven live (docs/platforms/loxo.md); what is pinned here
is the reasoning that decides *what* gets written, which a live run cannot
tell right from wrong.
"""

from __future__ import annotations

from app.platforms.loxo_source import (
    EXPERIENCE_BANDS,
    SourceReport,
    company_key,
    experience_bands,
    match_company,
)


def test_the_bands_are_loxos_five():
    assert [name for name, _, _ in EXPERIENCE_BANDS] == ["<1", "1-2", "3-5", "6-10", "10+"]


def test_an_open_ended_minimum_ticks_the_bands_above_it():
    """"5+ years" must not admit the 3-5 band, half of which is three-year people."""
    assert experience_bands(5, None) == ["6-10", "10+"]
    assert experience_bands(8, None) == ["6-10", "10+"]
    assert experience_bands(10, None) == ["10+"]
    assert experience_bands(3, None) == ["3-5", "6-10", "10+"]


def test_a_range_ticks_the_bands_inside_it():
    assert experience_bands(3, 5) == ["3-5"]
    assert experience_bands(2, 4) == ["3-5"]
    assert experience_bands(None, 3) == ["<1", "1-2"]
    assert experience_bands(0, 1) == ["<1"]


def test_a_narrow_requirement_still_ticks_the_band_that_holds_it():
    """A requirement no midpoint falls inside is not silently dropped."""
    assert experience_bands(5, 5) == ["3-5"]
    assert experience_bands(12, 15) == ["10+"]
    assert experience_bands(8, 3) == ["6-10"]


def test_no_years_means_no_bands():
    assert experience_bands(None, None) == []


def test_company_key_strips_what_is_not_identity():
    assert company_key("Stripe, Inc.") == "stripe"
    assert company_key("Monzo Bank Ltd") == "monzo bank"
    assert company_key("  Ramp ") == "ramp"
    assert company_key("") == ""


def test_match_company_is_exact_after_normalisation_and_never_a_prefix():
    """"Axle" must not pick "Axle Logistics": a past-company filter on the
    wrong company finds the wrong people, silently."""
    offered = ["Stripe\nstripe.com", "Stripe Logistics", "stripe.com"]
    assert match_company("Stripe", offered) == "Stripe\nstripe.com"
    assert match_company("Ramp, Inc.", ["Ramp\nramp.com"]) == "Ramp\nramp.com"
    assert match_company("Axle", ["Axle Logistics", "Axle Payments"]) is None
    assert match_company("", ["Anything"]) is None


def test_match_company_breaks_a_name_tie_on_the_domain():
    """Loxo offered three records called Alloy on 2026-09-07; the fintech is
    alloy.com, third in the list. The whole row comes back so the click lands
    on that record. With no domain that is the name, Loxo's first row stands."""
    alloys = ["Alloy\nalloycrew.com", "Alloy\nalloy.it", "Alloy\nalloy.com", "AllOY\nalloy.boats"]
    assert match_company("Alloy", alloys) == "Alloy\nalloy.com"
    heralds = ["Herald\nheraldapi.com", "Herald\nweareherald.co", "Herald Sun\nheraldsun.com.au"]
    assert match_company("Herald", heralds) == "Herald\nheraldapi.com"


def test_match_company_accepts_a_short_name_when_the_domain_is_the_full_name():
    """Loxo lists "Boost Insurance" as "Boost / boostinsurance.com" (refused
    live 2026-09-07). The domain must be the whole drafted name: "Axle" is
    still not "Axle Logistics" even through axlelogistics.com."""
    assert match_company("Boost Insurance", ["Boost\nboostinsurance.com"]) == "Boost\nboostinsurance.com"
    assert match_company("Axle", ["Axle Logistics\naxlelogistics.com"]) is None
    assert match_company("Axle Logistics", ["Axle\naxlelogistics.com"]) == "Axle\naxlelogistics.com"


def test_summary_reads_as_a_detail_line():
    report = SourceReport(
        added_titles=["Platform Engineer"],
        added_skills=["AWS", "Terraform"],
        added_experience=["6-10", "10+"],
        added_companies=["Ramp"],
        refused_companies=["Made Up Co"],
        search_name="Axle - auto",
        saved=True,
    )
    assert report.summary == (
        "1 title(s), 2 skill(s), experience 6-10/10+, 1 past company(ies), "
        "1 refused by Loxo's taxonomy, saved as 'Axle - auto'"
    )


def test_summary_without_the_new_sections_is_unchanged():
    report = SourceReport(added_titles=["a"], added_skills=["b"], search_name="n", saved=False)
    assert report.summary == "1 title(s), 1 skill(s), NOT saved"
