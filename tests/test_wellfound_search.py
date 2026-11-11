"""Wellfound saved search: the pure parts - what is asked for, and the filter
configuration rebuilt from what Wellfound stores."""

from __future__ import annotations

from app.platforms.wellfound_search import (
    clamp_years,
    filter_configuration,
    function_for,
    has_name,
    option_order,
    stored,
    stored_min_years,
    with_min_years,
)


def _criterion(kind, cid, name=None, *, qualifier=None, minimum=None, value=None, required=True):
    return {
        "criterion": {"criterionType": kind, "id": cid, "name": name, "minimum": minimum, "maximum": None, "value": value},
        "criterionQualifier": qualifier,
        "required": required,
    }


# The reply to SearchFilters for saved search 2869922, as recorded 2026-11-12.
SAVED = {
    "diversity": [],
    "officePreference": "ONSITE_ONLY",
    "remoteCandidateOption": "ALL_CANDIDATES",
    "searchKeyword": "",
    "searchCriteria": [
        _criterion("ROLE", "3", "Software Engineer", qualifier="MUST_BE_CURRENT_ROLE"),
        _criterion("ONSITE_LOCATION", "16", "Redwood City, CA"),
        _criterion("ROLE_TYPE", "1", "Full-time"),
        _criterion("TOTAL_EXPERIENCE_RANGE", "17941", minimum=2),
        _criterion("CANONICAL_SKILL", "20", "Python"),
        _criterion("CANONICAL_SKILL", "51", "PostgreSQL"),
        _criterion("REQUIRES_SPONSORSHIP", "2", value=False),
    ],
}


def test_the_stored_filters_rebuild_the_configuration_the_page_sent():
    """Field for field what the app's own RecruitSearchResult carried for
    this search (the probe recorded both)."""
    config = filter_configuration(SAVED)
    assert config["role"] == [
        {"criterionId": "3", "criterionType": "ROLE", "criterionQualifier": "MUST_BE_CURRENT_ROLE"}
    ]
    assert config["onsiteLocation"] == [{"criterionId": "16", "criterionType": "ONSITE_LOCATION"}]
    assert config["roleType"] == [{"criterionId": "1", "criterionType": "ROLE_TYPE"}]
    assert config["skill"] == [
        {"criterionId": "20", "criterionType": "CANONICAL_SKILL", "required": True},
        {"criterionId": "51", "criterionType": "CANONICAL_SKILL", "required": True},
    ]
    assert config["totalExperience"] == {
        "criterionRangeMax": None, "criterionRangeMin": 2,
        "criterionType": "TOTAL_EXPERIENCE_RANGE", "required": True,
    }
    assert config["requiresSponsorship"] == {"criterionType": "REQUIRES_SPONSORSHIP", "required": True, "value": False}
    assert config["officePreference"] == "ONSITE_ONLY"
    assert config["remoteCandidateOption"] == "ALL_CANDIDATES"
    for empty in ("companySize", "education", "experience", "market", "remoteLocation", "timeZone"):
        assert config[empty] == []
    for unset in ("averageExperience", "latestExperience", "salary", "dealSize", "lookingForRemoteWork"):
        assert config[unset] is None


def test_setting_the_years_keeps_every_other_filter_and_the_original():
    config = filter_configuration(SAVED)
    changed = with_min_years(config, 6)
    assert changed["totalExperience"]["criterionRangeMin"] == 6
    assert changed["totalExperience"]["required"] is True
    assert changed["skill"] == config["skill"]
    assert config["totalExperience"]["criterionRangeMin"] == 2


def test_years_on_a_search_that_had_none():
    bare = {"searchCriteria": [], "officePreference": None, "remoteCandidateOption": None}
    assert with_min_years(filter_configuration(bare), 4)["totalExperience"] == {
        "criterionRangeMax": None, "criterionRangeMin": 4,
        "criterionType": "TOTAL_EXPERIENCE_RANGE", "required": True,
    }


def test_what_a_search_holds_is_read_by_kind():
    criteria = SAVED["searchCriteria"]
    assert stored(criteria, "CANONICAL_SKILL") == ["Python", "PostgreSQL"]
    assert stored(criteria, "ONSITE_LOCATION") == ["Redwood City, CA"]
    assert stored_min_years(criteria) == 2
    assert stored_min_years([]) is None


def test_a_stored_place_counts_as_the_typed_city():
    assert has_name(["Redwood City, CA"], "Redwood City")
    assert has_name(["Python"], "python")
    assert not has_name(["New York City, NY"], "New York")
    assert not has_name(["AWS CloudFormation"], "AWS")


def test_years_are_clamped_to_the_slider():
    assert clamp_years(15) == 10
    assert clamp_years(6) == 6
    assert clamp_years(0) is None
    assert clamp_years(None) is None


def test_the_function_comes_from_the_title_and_defaults_to_engineering():
    assert function_for("Platform Infra Engineer / Series A") == "Engineering"
    assert function_for("Engineering Manager / Agent Orchestration") == "Engineering"
    assert function_for("Senior Product Manager - Payments") == "Product"
    assert function_for("Account Executive / SaaS") == "Sales"
    assert function_for("Product Designer") == "Design"


def test_a_skill_is_never_matched_by_prefix():
    # What "AWS" was offered on 2026-11-12.
    assert option_order(["AWS CloudFormation"], "AWS") == []
    assert option_order(["Terraform", "Terraform Cloud"], "terraform") == [0]


def test_a_place_is_matched_exactly_then_by_region_then_by_prefix():
    offered = ["New York City, NY", "New York, NY", "York, PA"]
    assert option_order(offered, "New York", loose=True) == [1, 0]
    assert option_order(offered, "New York") == [1]
