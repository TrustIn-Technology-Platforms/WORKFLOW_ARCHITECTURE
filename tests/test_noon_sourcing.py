"""The sourcing wizard: the tightening policy, and the calls it makes.

The policy — every nice-to-have promoted, every criterion kept, the strictest
answer chosen — is the part a recruiter would notice getting quietly wrong, and
none of it needs a browser. The call sequence is checked against a stand-in
session so that a payload the portal does not send (or one it does, in the wrong
order) fails here rather than on a live role. The sequence and the payload
shapes are the ones recorded from the live wizard on 2026-10-09.
"""

from __future__ import annotations

import asyncio

import pytest

from app.models import PlatformError
from app.platforms.noon_sourcing import (
    SKIP,
    NoonSession,
    WizardBrief,
    as_lines,
    as_list,
    as_years,
    build_preferences,
    client_description,
    employee_band,
    format_feedback,
    match_company,
    parse_criteria,
    rate_company_cards,
    run_wizard,
    role_title,
    strictest_answer,
    targeting_preamble,
    tighten,
)


# ----------------------------------------------------------------------
# criteria
# ----------------------------------------------------------------------


def test_as_lines_reads_both_shapes_noon_uses():
    assert as_lines("a\nb\n") == ["a", "b"]
    assert as_lines(["a", "b"]) == ["a", "b"]
    assert as_lines("") == []
    assert as_lines(None) == []


def test_as_list_keeps_the_shape_preferences_stores():
    """`as_text` flattens for reading; this keeps the list noon writes back.

    A multi-part location stays split - joined into one string it would ask
    noon to find a single place called "New York, Atlanta, Georgia".
    """
    assert as_list(["New York", "Atlanta, Georgia, United States"]) == [
        "New York", "Atlanta, Georgia, United States",
    ]
    assert as_list("Manchester, UK") == ["Manchester, UK"]
    assert as_list("") == []
    assert as_list(None) == []
    assert as_list([]) == []


def test_as_years_takes_noon_s_band_and_nothing_else():
    assert as_years([5, 12]) == (5, 12)
    assert as_years(["5", "12"]) == (5, 12)
    assert as_years([12, 5]) is None
    assert as_years([5]) is None
    assert as_years("5-12") is None
    assert as_years([0, 99]) is None
    assert as_years(None) is None


def test_every_nice_to_have_becomes_a_must_have():
    must, promoted = tighten("5+ years on platform teams\nKubernetes", "Terraform\nGo")
    assert must == ["5+ years on platform teams", "Kubernetes", "Terraform", "Go"]
    assert promoted == ["Terraform", "Go"]


def test_a_repeated_requirement_is_not_counted_twice():
    """The same line in both lists would otherwise be scored twice."""
    must, promoted = tighten("Kubernetes\nPython", "kubernetes\nTerraform")
    assert must == ["Kubernetes", "Python", "Terraform"]
    assert promoted == ["Terraform"]


def test_criteria_survive_the_round_trip_noon_stores_them_in():
    feedback = "*Must have 5+ years in platform engineering\n*Require Kubernetes"
    criteria = parse_criteria(feedback)
    assert criteria == [
        "Must have 5+ years in platform engineering",
        "Require Kubernetes",
    ]
    assert parse_criteria(format_feedback(criteria)) == criteria


def test_no_criteria_is_not_an_empty_string():
    assert parse_criteria("No feedback provided.") == []
    assert parse_criteria("") == []


# ----------------------------------------------------------------------
# clarifying questions
# ----------------------------------------------------------------------


def test_wording_picks_the_stricter_option():
    answer, _ = strictest_answer(
        "How should Kubernetes experience be treated?",
        ["Nice to have", "Required for every candidate"],
    )
    assert answer == "Required for every candidate"


def test_not_required_does_not_read_as_required():
    """'not required' contains 'required'; the loose reading has to win."""
    answer, _ = strictest_answer(
        "How should a PhD be treated?",
        ["Not required", "Required"],
    )
    assert answer == "Required"


def test_a_question_offering_to_widen_the_search_is_answered_no():
    answer, why = strictest_answer(
        "Would you consider candidates from adjacent industries?", ["Yes", "No"]
    )
    assert answer == "No"
    assert "widen" in why


def test_a_question_asking_whether_something_is_demanded_is_answered_yes():
    answer, _ = strictest_answer(
        "Is a degree required for this role?", ["Yes", "No"]
    )
    assert answer == "Yes"


def test_an_unclear_question_is_left_unanswered():
    """Guessing could loosen the criteria, so silence is the safe answer."""
    answer, _ = strictest_answer(
        "Which of these matters more to you?", ["Depth", "Breadth"]
    )
    assert answer == SKIP


@pytest.mark.parametrize(
    "question,options,strict",
    [
        (
            "Is hands-on experience with both AWS and GCP truly required, or would strong "
            "expertise in one cloud plus some exposure to the other be acceptable?",
            ["Must have deep experience in both", "One primary cloud plus some exposure is fine"],
            "Must have deep experience in both",
        ),
        (
            "Is backend experience specifically in TypeScript a hard requirement, or would "
            "strong backend experience in another language be acceptable?",
            ["TypeScript required", "Other backend languages acceptable"],
            "TypeScript required",
        ),
        (
            "Is prior experience shipping self-hosted software specifically to enterprise "
            "customers a strict must-have, or is general on-prem experience sufficient?",
            [
                "Must have shipped self-hosted builds to enterprise customers",
                "General enterprise/on-prem experience is sufficient",
            ],
            "Must have shipped self-hosted builds to enterprise customers",
        ),
        (
            "Should prior experience at an early-stage AI integration/developer platform "
            "startup be treated as a hard filter, or just a strong plus?",
            ["Hard filter", "Strong plus"],
            "Hard filter",
        ),
    ],
)
def test_the_questions_noon_asked_on_2026_10_09_get_the_strict_answer(question, options, strict):
    """The three real questions from the live wizard, answered as the
    recruiter would: the option that keeps the criterion a dealbreaker."""
    answer, _ = strictest_answer(question, options)
    assert answer == strict


# ----------------------------------------------------------------------
# step 3 - the search criteria, as the role stores them
# ----------------------------------------------------------------------


_EXTRACTED = {
    "must_haves": "Production Kubernetes experience",
    "nice_to_haves": "",
    "titles": ["Platform Engineer", "Infrastructure Engineer"],
    "location": ["San Francisco"],
    "yoe": [5, 12],
    "company_specs": ["startups", "saas", "not big tech"],
    "requires_visa_sponsorship": True,
}


def test_preferences_carry_what_noon_extracted_under_the_portal_s_keys():
    """Recorded from a live role: the title list is `type`, the band is
    `experience`, the chips are `companySpecs`, the examples are ids."""
    prefs = build_preferences({"recruiters": ["a@b"]}, _EXTRACTED, example_company_ids=["16181286"])
    assert prefs["type"] == ["Platform Engineer", "Infrastructure Engineer"]
    assert prefs["experience"] == [5, 12]
    assert prefs["location"] == ["San Francisco"]
    assert prefs["companySpecs"] == ["startups", "saas", "not big tech"]
    assert prefs["required_companies_to_source_from"] == ["16181286"]
    # The portal's own defaults for a role confirmed through step 3.
    assert prefs["onlySourceFromTheseCompanies"] is False
    assert prefs["ban_past_candidates"] is True
    assert prefs["past_candidates_cooldown_days"] == 30
    # And nothing the role already held is lost.
    assert prefs["recruiters"] == ["a@b"]


def test_the_brief_fills_only_what_noon_left_empty():
    brief = WizardBrief(
        titles=["SRE"], years=(3, 8), location=["Manchester"], example_companies=["Vercel"]
    )
    nothing = {"must_haves": "x", "titles": [], "location": "", "yoe": None}
    prefs = build_preferences({}, nothing, brief=brief)
    assert prefs["type"] == ["SRE"]
    assert prefs["experience"] == [3, 8]
    assert prefs["location"] == ["Manchester"]

    prefs = build_preferences({}, _EXTRACTED, brief=brief)
    assert prefs["type"] == ["Platform Engineer", "Infrastructure Engineer"]
    assert prefs["experience"] == [5, 12]
    assert prefs["location"] == ["San Francisco"]


def test_existing_example_companies_are_kept_and_not_repeated():
    prefs = build_preferences(
        {"required_companies_to_source_from": ["1", "2"]},
        _EXTRACTED,
        example_company_ids=["2", "3"],
    )
    assert prefs["required_companies_to_source_from"] == ["1", "2", "3"]


def test_a_recruiter_s_own_step3_choices_survive():
    """setdefault, not assignment: a role already confirmed by hand keeps its
    cooldowns and its only-these-companies switch."""
    prefs = build_preferences(
        {"ban_past_candidates": False, "onlySourceFromTheseCompanies": True},
        _EXTRACTED,
    )
    assert prefs["ban_past_candidates"] is False
    assert prefs["onlySourceFromTheseCompanies"] is True


def test_match_company_takes_only_the_company_itself():
    results = [
        {"id": "16181286", "name": "Vercel", "website": "https://vercel.com"},
        {"id": "5041231", "name": "Vercel", "website": "https://vercelllc.com"},
        {"id": "9", "name": "Vercelli"},
    ]
    assert match_company("Vercel", results)["id"] == "16181286"
    assert match_company("vercel inc", results)["id"] == "16181286"
    assert match_company("Vercelli Palace Hotel", results) is None
    assert match_company("Stripe", results) is None
    assert match_company("Stripe", None) is None


_CARDS = [
    {"company_id": "11869260", "name": "Retool", "employees": 300, "group": "anchor_yes", "dimension": None},
    {"company_id": "2135371", "name": "Stripe", "employees": 7000, "group": "boundary", "dimension": "caliber_high_outside"},
    {"company_id": "89816302", "name": "Together AI", "employees": 79, "group": "boundary", "dimension": "size_stage"},
    {"company_id": "552285", "name": "Bitly", "employees": 371, "group": "anchor_no", "dimension": None},
    {"company_id": "77", "name": "Mystery Co", "employees": None, "group": "boundary", "dimension": "adjacent_industry"},
    {"company_id": "16181286", "name": "Vercel", "employees": 943, "group": "boundary", "dimension": "criterion:saas"},
]


def test_target_companies_are_rated_from_the_brief_and_noon_s_own_anchors():
    ratings = rate_company_cards(_CARDS, wanted=["vercel"], employees=(10, 500))
    by_name = {c["name"]: r for c, r in zip(_CARDS, ratings)}
    assert by_name["Retool"]["rating"] == "great"        # noon's anchor_yes
    assert by_name["Bitly"]["rating"] == "no"            # noon's anchor_no
    assert by_name["Together AI"]["rating"] == "ok"      # boundary, inside the band
    assert by_name["Stripe"]["rating"] == "no"           # boundary, far outside it
    assert by_name["Mystery Co"]["rating"] == "ok"       # boundary, size unknown
    assert by_name["Vercel"]["rating"] == "great"        # named in the brief, band or not
    # Every card rated - the screen will not continue otherwise - and each
    # rating carries the card's own grouping back, as the portal's does.
    assert len(ratings) == len(_CARDS)
    assert ratings[1] == {
        "company_id": "2135371", "rating": "no", "group": "boundary",
        "dimension": "caliber_high_outside",
    }


def test_without_a_size_band_boundary_companies_are_rated_okay():
    ratings = rate_company_cards(_CARDS, employees=None)
    assert [r["rating"] for r in ratings] == ["great", "ok", "ok", "no", "ok", "ok"]


def test_the_size_band_is_read_off_the_step3_save():
    assert employee_band({"company_json": {"employees_min_number": 10, "employees_max_number": 500}}) == (10, 500)
    assert employee_band({"company_json": {"employees_max_number": 500}}) == (None, 500)
    assert employee_band({"company_json": {}}) is None
    assert employee_band("OK") is None


def test_the_client_description_is_the_sentence_about_the_employer():
    jd = (
        "Founding Platform Engineer\n\nAbout the role\n\n"
        "A seed-stage AI startup in San Francisco is hiring its first platform engineer "
        "to own cloud infrastructure. Five days on-site in SOMA.\n\nRequirements\n\n"
        "5+ years of backend or infrastructure engineering."
    )
    assert client_description(jd) == (
        "A seed-stage AI startup in San Francisco is hiring its first platform engineer "
        "to own cloud infrastructure."
    )
    # The stage is added when the sentence does not already say it, not otherwise.
    assert client_description(jd, stage="Seed").startswith("A seed-stage")
    assert client_description(
        "The company builds payment infrastructure for marketplaces across Europe.",
        stage="Series B",
    ) == "Series B stage. The company builds payment infrastructure for marketplaces across Europe."
    assert client_description("") == ""
    assert client_description("Requirements\nKubernetes\nPython") == ""


# ----------------------------------------------------------------------
# the call sequence
# ----------------------------------------------------------------------


class FakeSession(NoonSession):
    """Answers like noon did on 2026-10-09, and remembers what it was asked."""

    def __init__(self, **responses):
        self.calls: list[tuple[str, dict]] = []
        self.responses = {
            "generate_params": {
                "must_haves": "5+ years on platform teams\nKubernetes",
                "nice_to_haves": "Terraform",
                "titles": ["Platform Engineer"],
                "location": "Manchester, UK",
                "yoe": [5, 12],
                "company_specs": ["startups", "saas"],
                "requires_visa_sponsorship": False,
            },
            # The direct read of the role, as it reads back after step 3 was
            # written. `refetch_roles` lists it too (set below) unless a test
            # says otherwise.
            "poll_role_params": {
                "autopilot": {"emailCampaign": {"id": "c1"}, "enabled": False},
                "preferences": {
                    "name": "Halluminate",
                    "location": ["Manchester, UK"],
                    "type": ["Platform Engineer", "Site Reliability Engineer"],
                    "experience": [5, 12],
                },
                "pinned": [],
            },
            "role_summarized_jd_finished": {"finished": True},
            "update_role": {
                "company_json": {"employees_min_number": 10, "employees_max_number": 500},
                "search_tier": "x",
            },
            "gpt_stream": "*Must have 5+ years on platform teams\n*Require Kubernetes\n*Require Terraform",
            "company_rating_cards": {
                "show": True,
                "reason": "Startups is ambiguous",
                "cards": [
                    {"company_id": "1", "name": "Retool", "employees": 300, "group": "anchor_yes", "dimension": None},
                    {"company_id": "2", "name": "Stripe", "employees": 7000, "group": "boundary", "dimension": "caliber_high_outside"},
                ],
            },
            "save_company_ratings": {"company_ratings": {"1": {"rating": "great"}, "2": {"rating": "no"}}},
            "company_search_by_name": [
                {"id": "16181286", "name": "Vercel"},
                {"id": "9", "name": "Vercelli"},
            ],
            "clarifying_questions": {
                "Is Terraform required?": ["Yes", "No"],
                "Which matters more?": ["Depth", "Breadth"],
            },
            **responses,
        }
        self.responses.setdefault(
            "refetch_roles", [{"id": "role-1", "name": "Halluminate", "active": True}]
        )
        # NoonSession is a slots dataclass; only the fields it declares exist.
        super().__init__(page=None, token="tok", company="co", user="u-1", email="me@x")

    async def post(self, path, payload, *, forbidden_ok=False):  # type: ignore[override]
        self.calls.append((path, payload))
        return self.responses.get(path, {})

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]

    def payload(self, path: str) -> dict:
        return next(p for name, p in self.calls if name == path)

    def payloads(self, path: str) -> list[dict]:
        return [p for name, p in self.calls if name == path]


def _run(session, **kwargs):
    return asyncio.run(
        run_wizard(session, "role-1", "Halluminate", "JD text", **kwargs)
    )


def test_the_wizard_makes_the_portal_s_calls_in_the_portal_s_order():
    """The sequence recorded from the live wizard on 2026-10-09, screen by screen."""
    session = FakeSession()
    report = _run(session)

    assert session.paths() == [
        "generate_params",               # 1 · job description
        "poll_role_params",              #     the role, read directly
        "refetch_roles",                 #     ...and the list, for a tombstone
        "set_candidate_source",          # 2 · candidate pool
        "prepare_role_preferences",      # 3 · the draft, as the screen opens
        "role_summarized_jd_finished",   #     noon's summary of the JD
        "setup_clarifying_questions",    # 3 · Continue
        "update_role",                   #     the write the replay used to skip
        "poll_role_params",              #     read back, directly
        "gpt_stream",                    #     the criteria
        "company_rating_cards",          # 4 · target companies
        "save_company_ratings",
        "role_autopilot",                # 5 · non-negotiables selected
        "clarifying_questions",          # 6 · fetched before the ranking save
        "role_autopilot",                #     ranked, initialization: true
        "rank_non_negotiables",
        "mark_clarifying_question",      # 7 · one per question
        "mark_clarifying_question",
        "role_autopilot",                #     the one that starts the search
    ]
    assert report.started_sourcing is True


def test_example_companies_are_resolved_before_the_criteria_are_written():
    session = FakeSession()
    report = _run(session, brief=WizardBrief(example_companies=["Vercel", "Nowhere Ltd"]))

    searched = session.payloads("company_search_by_name")
    assert [p["query"] for p in searched] == ["Vercel", "Nowhere Ltd"]
    assert session.paths().index("company_search_by_name") < session.paths().index("prepare_role_preferences")
    # The exact match is kept as noon's id; the miss is reported, not guessed.
    assert session.payload("update_role")["preferences"]["required_companies_to_source_from"] == ["16181286"]
    assert report.example_companies == ["Vercel"]
    assert report.unresolved_companies == ["Nowhere Ltd"]
    assert any("Nowhere Ltd" in w for w in report.warnings)
    # And it rides on the autopilot block too, as the portal's does.
    assert session.payloads("role_autopilot")[0]["autopilot"]["required_companies_to_source_from"] == ["16181286"]


def test_step3_s_write_carries_the_whole_screen():
    """`update_role` as the portal sends it on Continue: name, the block whole,
    the lists twice (once as `feedback`), the client, noon's own visa reading,
    and the signed-in user."""
    session = FakeSession()
    _run(session, brief=WizardBrief(client_description="Seed-stage AI startup in SF."))

    written = session.payload("update_role")
    assert set(written) == {
        "token", "role", "name", "preferences", "user", "must_haves", "nice_to_haves",
        "feedback", "client_description", "client_name", "client_linkedin_alt",
        "requires_visa_sponsorship",
    }
    assert written["role"] == "role-1"
    assert written["name"] == "Halluminate"
    assert written["user"] == "u-1"
    assert written["must_haves"] == "5+ years on platform teams\nKubernetes\nTerraform"
    assert written["nice_to_haves"] == ""
    assert written["feedback"] == written["must_haves"]
    assert written["client_description"] == "Seed-stage AI startup in SF."
    assert written["client_name"] is None
    assert written["requires_visa_sponsorship"] is False
    prefs = written["preferences"]
    assert prefs["type"] == ["Platform Engineer"]
    assert prefs["experience"] == [5, 12]
    assert prefs["location"] == ["Manchester, UK"]
    assert prefs["companySpecs"] == ["startups", "saas"]

    staged = session.payload("prepare_role_preferences")
    assert staged["preferences"] == prefs
    assert staged["must_haves"] == written["must_haves"]


def test_the_criteria_generator_gets_the_must_haves_both_ways():
    session = FakeSession()
    _run(session)
    sent = session.payload("gpt_stream")
    assert sent["must_haves"] == "5+ years on platform teams\nKubernetes\nTerraform"
    assert sent["nice_to_haves"] == ""
    assert sent["rerun_prep"] is False
    assert "<must_haves>" in sent["msg"]
    assert sent["company"] == "co"


def test_target_companies_are_rated_against_the_band_noon_derived():
    session = FakeSession()
    report = _run(session)

    ratings = session.payload("save_company_ratings")["ratings"]
    assert ratings == [
        {"company_id": "1", "rating": "great", "group": "anchor_yes", "dimension": None},
        {"company_id": "2", "rating": "no", "group": "boundary", "dimension": "caliber_high_outside"},
    ]
    assert report.rated_companies == 2
    # What noon answered rides on the autopilot block, as the portal's does.
    assert session.payloads("role_autopilot")[0]["autopilot"]["company_ratings"] == {
        "company_ratings": {"1": {"rating": "great"}, "2": {"rating": "no"}}
    }


def test_no_rating_cards_means_no_rating_call():
    session = FakeSession(company_rating_cards={"show": False, "cards": []})
    report = _run(session)
    assert "save_company_ratings" not in session.paths()
    assert report.rated_companies == 0
    assert "company_ratings" not in session.payloads("role_autopilot")[0]["autopilot"]


def test_the_promoted_nice_to_have_reaches_noon_as_a_must_have():
    session = FakeSession()
    report = _run(session)

    autopilot = session.payloads("role_autopilot")[0]["autopilot"]
    assert autopilot["must_haves"].splitlines() == [
        "5+ years on platform teams",
        "Kubernetes",
        "Terraform",
    ]
    # Left empty rather than deleted: a leftover nice-to-have would be scored
    # again as a preference.
    assert autopilot["nice_to_haves"] == ""
    assert report.promoted == ["Terraform"]


def test_the_autopilot_block_carries_the_step3_facts_the_agent_reads():
    session = FakeSession()
    _run(session)
    autopilot = session.payloads("role_autopilot")[0]["autopilot"]
    assert autopilot["enabled"] is True
    assert autopilot["source"] == "public"
    assert autopilot["sourcing_type"] == "recruiting"
    assert autopilot["companySpecs"] == ["startups", "saas"]
    assert autopilot["examples"] == [""]
    assert autopilot["calibration_stage"] == "calibrating"
    # The role's own fields travel untouched.
    assert autopilot["emailCampaign"] == {"id": "c1"}
    # The first autopilot save carries the token, as the portal's does.
    assert "token" in session.payloads("role_autopilot")[0]


def test_every_generated_criterion_is_kept_as_a_non_negotiable():
    session = FakeSession()
    report = _run(session)

    selected = session.payloads("role_autopilot")[0]["autopilot"]
    assert [item["text"] for item in selected["pending_non_negotiables"]] == [
        "Must have 5+ years on platform teams",
        "Require Kubernetes",
        "Require Terraform",
    ]
    assert session.payload("rank_non_negotiables")["non_negotiables"] == report.non_negotiables
    ranked = session.payloads("role_autopilot")[1]
    assert ranked["initialization"] is True
    assert ranked["autopilot"]["non_negotiables"] == report.non_negotiables
    assert ranked["autopilot"]["use_ordering"] is True
    assert len(report.non_negotiables) == 3


def test_the_last_call_is_what_starts_the_search():
    """`initialization: false` is the go signal - true only saves."""
    started = FakeSession()
    _run(started)
    last = started.payloads("role_autopilot")[-1]
    assert last["initialization"] is False
    assert last["autopilot"]["enabled"] is True
    assert last["autopilot"]["clarifying_answers"] == {
        "Is Terraform required?": "Yes",
        "Which matters more?": SKIP,
    }

    held = FakeSession()
    report = _run(held, start_sourcing=False)
    last = held.payloads("role_autopilot")[-1]
    assert last["initialization"] is True
    # Held back means off, the way a paused role reads in the portal.
    assert last["autopilot"]["enabled"] is False
    assert report.started_sourcing is False


def test_an_unanswerable_question_is_skipped_and_said_out_loud():
    session = FakeSession()
    report = _run(session)

    assert report.answers == {
        "Is Terraform required?": "Yes",
        "Which matters more?": SKIP,
    }
    assert [p["answer"] for p in session.payloads("mark_clarifying_question")] == ["Yes", SKIP]
    assert any("clarifying question" in w for w in report.warnings)


def test_a_dry_run_saves_nothing():
    session = FakeSession()
    report = _run(session, dry_run=True, brief=WizardBrief(example_companies=["Vercel"]))

    assert session.paths() == ["generate_params"]
    assert session.payload("generate_params")["dont_save"] is True
    assert report.must_haves == [
        "5+ years on platform teams",
        "Kubernetes",
        "Terraform",
    ]
    assert report.started_sourcing is False
    assert any("1 example company" in w for w in report.warnings)


def test_an_advert_noon_finds_no_requirements_in_stops_before_writing():
    session = FakeSession(generate_params={"must_haves": "", "nice_to_haves": ""})
    with pytest.raises(PlatformError, match="no requirements"):
        _run(session)
    assert session.paths() == ["generate_params"]


def test_criteria_that_do_not_generate_stop_the_run():
    session = FakeSession(gpt_stream="No feedback provided.")
    with pytest.raises(PlatformError, match="no criteria"):
        _run(session)


def test_a_missing_role_is_named_in_the_error():
    session = FakeSession(poll_role_params=None, refetch_roles=[{"id": "someone-elses-role"}])
    with pytest.raises(PlatformError, match="no role"):
        _run(session)
    assert session.payload("refetch_roles") == {"token": "tok", "company": "co", "email": "me@x"}


def test_a_deleted_role_is_a_tombstone_in_the_list():
    """A deleted role still answers the direct read; only the list says it is
    gone - as a record with `obsolete: true` and nothing else on it."""
    session = FakeSession(
        refetch_roles=[{"obsolete": True, "id": "role-1", "name": "Halluminate"}],
    )
    with pytest.raises(PlatformError, match="deleted"):
        _run(session)


def test_a_role_noon_has_not_listed_yet_is_read_directly():
    """The 2026-10-08 failure, seen for what it was: the list lags by minutes,
    the direct read does not, so a role missing from the list is not missing."""
    session = FakeSession(refetch_roles=[])
    report = _run(session)
    assert report.started_sourcing is True
    assert session.paths()[:3] == ["generate_params", "poll_role_params", "refetch_roles"]


# ----------------------------------------------------------------------
# a role noon has not listed yet
# ----------------------------------------------------------------------


class LateSession(FakeSession):
    """noon catching up: neither read knows the role for the first `misses` reads."""

    def __init__(self, misses: int, **responses):
        super().__init__(**responses)
        self.misses = misses

    async def post(self, path, payload, *, forbidden_ok=False):  # type: ignore[override]
        if path in ("poll_role_params", "refetch_roles") and self.misses > 0:
            self.misses -= 1
            self.calls.append((path, payload))
            return None if path == "poll_role_params" else []
        return await super().post(path, payload)


def test_a_role_noon_does_not_know_yet_is_waited_for(monkeypatch):
    """Seconds after creation noon may know the role in neither read; the
    posting run waits rather than leaving the role bare."""
    monkeypatch.setattr("app.platforms.noon_sourcing.ROLE_POLL_SECONDS", 0.01)
    # the direct read and the list, then two more rounds of misses while waiting
    session = LateSession(misses=6)
    report = _run(session, role_wait_seconds=5)

    reads = [p for p in session.paths() if p in ("poll_role_params", "refetch_roles")]
    assert reads[:2] == ["poll_role_params", "refetch_roles"]
    assert reads.count("poll_role_params") >= 4
    assert report.started_sourcing is True


def test_a_role_that_never_appears_says_how_long_it_was_waited_for(monkeypatch):
    monkeypatch.setattr("app.platforms.noon_sourcing.ROLE_POLL_SECONDS", 0.01)
    session = LateSession(misses=999)
    with pytest.raises(PlatformError, match="after waiting"):
        _run(session, role_wait_seconds=0.05)


def test_without_a_wait_a_missing_role_is_not_polled_for():
    """A delete or a read-back expects the role to exist; a miss means gone."""
    session = LateSession(misses=999)
    with pytest.raises(PlatformError, match="no role"):
        _run(session)
    assert session.paths() == ["generate_params", "poll_role_params", "refetch_roles"]


# ----------------------------------------------------------------------
# the targeting preamble
# ----------------------------------------------------------------------


def test_the_preamble_states_the_facts_the_advert_leaves_out():
    text = targeting_preamble(
        title="Senior Recruitment Consultant",
        location="Manchester (hybrid)",
        employment_type="Permanent",
        skills=["Kubernetes", "Terraform"],
    )
    assert text.splitlines() == [
        "Job title: Senior Recruitment Consultant",
        "Location: Manchester (hybrid)",
        "Employment type: Permanent",
        "Key skills: Kubernetes, Terraform",
    ]
    assert targeting_preamble() == ""
    assert targeting_preamble(title="  ", location="") == ""


def test_the_preamble_carries_the_whole_brief_from_the_profile():
    text = targeting_preamble(
        title="Platform Engineer",
        similar_titles=["Platform Engineer", "Infrastructure Engineer", "SRE"],
        location="San Francisco",
        skills=["Kubernetes", "Terraform"],
        nice_to_have=["Go"],
        companies=["Vercel", "Render"],
    )
    assert text.splitlines() == [
        "Job title: Platform Engineer",
        "Also matching job titles: Infrastructure Engineer, SRE",
        "Location: San Francisco",
        "Key skills: Kubernetes, Terraform",
        "Nice-to-have skills: Go",
        "Ideal past companies: Vercel, Render",
    ]


def test_noon_reading_nothing_falls_back_to_the_profile_must_haves():
    session = FakeSession(generate_params={"must_haves": "", "nice_to_haves": "", "titles": [], "location": ""})
    report = _run(
        session,
        fallback_must_haves=["5-8 years of professional experience", "Experience with Kubernetes"],
    )
    assert report.must_haves == ["5-8 years of professional experience", "Experience with Kubernetes"]
    assert report.promoted == []
    assert any("drafted sourcing profile" in w for w in report.warnings)
    assert session.payload("update_role")["must_haves"] == (
        "5-8 years of professional experience\nExperience with Kubernetes"
    )


def test_without_a_fallback_an_empty_extraction_still_stops_the_run():
    session = FakeSession(generate_params={"must_haves": "", "nice_to_haves": ""})
    with pytest.raises(PlatformError, match="no requirements"):
        _run(session, fallback_must_haves=[])


def test_the_facts_reach_noon_above_the_job_description():
    session = FakeSession()
    _run(session, targeting="Location: Manchester (hybrid)\nEmployment type: Permanent")

    sent = session.payload("generate_params")["jd"]
    assert sent.startswith("Location: Manchester (hybrid)\nEmployment type: Permanent\n\n")
    assert sent.endswith("JD text")


def test_a_role_that_would_be_searched_globally_says_so():
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": ["Platform Engineer"],
            "location": "",
        },
        poll_role_params={"autopilot": {}, "preferences": {"type": ["Platform Engineer"]}},
    )
    report = _run(session)

    assert report.location == ""
    assert any("searched globally" in w for w in report.warnings)
    assert "no location" in report.summary


def test_a_location_only_the_brief_knows_is_written_too():
    """Nothing in the text, but the row's Location column knew: written, not
    just warned about - the write exists now, so the preamble is no longer
    the only way a location can reach the role."""
    session = FakeSession(
        generate_params={"must_haves": "Kubernetes", "nice_to_haves": "", "titles": ["X"], "location": ""},
    )
    _run(session, brief=WizardBrief(location=["Manchester, UK"]))
    assert session.payload("update_role")["preferences"]["location"] == ["Manchester, UK"]


def test_a_role_that_already_has_a_location_is_not_called_global():
    """The warning is decided after the role is read, not after the extraction.

    Deciding it early made a run contradict itself: the warnings said the role
    would be searched globally while the summary of the same run named the
    location it was restricted to.
    """
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": ["Platform Engineer"],
            "location": "",
        },
    )
    report = _run(session)

    assert report.location == "Manchester, UK"
    assert "location Manchester, UK" in report.summary
    assert not any("searched globally" in w for w in report.warnings)


def test_the_location_write_leaves_the_rest_of_the_role_alone():
    """`preferences` is sent back whole, so a partial block cannot blank the
    keys this code has never heard of. The block here is the live-recorded one.
    """
    recorded = {
        "jd": "", "type": [], "experience": [0, 20], "industry": [],
        "location": [], "location_distance": 0, "competitors": [],
        "companyBlacklist": [], "skills": [], "notes": "",
        "startupExp": False, "managementExp": "No preference",
        "recruiters": ["someone@example.com"],
    }
    session = FakeSession(
        poll_role_params={"autopilot": {}, "preferences": dict(recorded)},
    )
    _run(session, targeting="Location: Manchester, UK")

    sent = session.payload("update_role")["preferences"]
    assert sent["location"] == ["Manchester, UK"]
    for key, value in recorded.items():
        if key not in ("location", "type", "experience"):
            assert sent[key] == value, f"{key} was not carried through"


def test_the_read_back_is_the_direct_read_not_the_list():
    """The list serves a copy that can be minutes old - which once made a save
    that worked look like one that failed. The list and the direct read
    disagree here on purpose: the direct read must win, and the read-back
    after the write must not consult the list at all.
    """
    session = FakeSession(
        refetch_roles=[{"id": "role-1", "name": "Halluminate", "preferences": {"location": [], "type": ["X"]}}],
        poll_role_params={"autopilot": {}, "preferences": {"location": ["Manchester, UK"], "type": ["X"]}},
    )
    report = _run(session, targeting="Location: Manchester, UK")

    assert session.paths().count("refetch_roles") == 1   # the tombstone check, before the write
    assert session.paths().count("poll_role_params") == 2
    assert report.location == "Manchester, UK"
    # Reading the cache instead would report the location lost and hold the
    # search back on a role that is in fact correctly set up.
    assert not any("has NOT been started" in w for w in report.warnings)
    assert report.started_sourcing is True


def test_a_location_that_will_not_stick_stops_the_search():
    """The 2026-09-22 row: noon quoted the location back, the role kept `[]`,
    and the run started the search anyway and reported Posted. Every candidate
    it found came from the wrong pool.

    The role is saved and left idle instead - visible, and one click from
    right. Raising would be quieter: `noon.py` catches a PlatformError from
    here into a warning and the row still reads Posted.
    """
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": ["Platform Engineer"],
            "location": ["New York", "Atlanta, Georgia, United States"],
        },
        poll_role_params={"autopilot": {}, "preferences": {"location": [], "type": ["X"]}},
    )
    report = _run(session, targeting="Location: New York")

    # It tried.
    assert session.payload("update_role")["preferences"]["location"] == [
        "New York", "Atlanta, Georgia, United States",
    ]
    # It did not pretend it worked.
    assert report.started_sourcing is False
    assert session.payloads("role_autopilot")[-1]["initialization"] is True
    assert "not started" in report.summary

    loud = [w for w in report.warnings if "has NOT been started" in w]
    assert len(loud) == 1
    assert "New York" in loud[0]
    assert "Control Panel" in loud[0]


def test_a_location_that_sticks_lets_the_search_start():
    """The other side of the same guard: the ordinary run must be unaffected."""
    session = FakeSession()
    report = _run(session, targeting="Location: Manchester, UK")

    assert report.started_sourcing is True
    assert session.payloads("role_autopilot")[-1]["initialization"] is False
    assert not any("has NOT been started" in w for w in report.warnings)


def test_no_titles_on_the_role_is_worth_a_warning():
    """Judged on the role, not on the extraction - the role is what searches."""
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": [],
            "location": "Manchester, UK",
        },
        poll_role_params={"autopilot": {}, "preferences": {"location": ["Manchester, UK"]}},
    )
    report = _run(session)
    assert any("no job titles" in w for w in report.warnings)


def test_titles_already_on_the_role_are_not_called_missing():
    """This document yielded none, but the role carries them from an earlier
    run - so it is not matching on criteria alone, and saying it is would send
    a recruiter to the Control Panel to fix something that is already right.
    The title list on a real role is `preferences.type`.
    """
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": [],
            "location": "Manchester, UK",
        }
    )
    report = _run(session)
    assert not any("no job titles" in w for w in report.warnings)
    assert report.titles == ["Platform Engineer", "Site Reliability Engineer"]
    assert report.years == (5, 12)
    assert "5-12 years" in report.summary


def test_a_dry_run_still_reports_the_filters_it_would_have_set():
    """The cheapest place to find out the location is missing is before the run."""
    session = FakeSession()
    report = _run(session, dry_run=True, targeting="Location: Manchester, UK")

    assert report.location == "Manchester, UK"
    assert report.titles == ["Platform Engineer"]
    assert report.years == (5, 12)


@pytest.mark.parametrize(
    "written,role",
    [
        # TrustIn's own title convention: the role, then what sells it.
        ("Backend Platform Engineer - NYC / Series A / Kubernetes", "Backend Platform Engineer"),
        ("Platform Engineer / AWS, Kubernetes", "Platform Engineer"),
        ("Head of Data - London", "Head of Data"),
        # Nothing to strip.
        ("Senior Recruitment Consultant", "Senior Recruitment Consultant"),
        # An unspaced hyphen is part of the name, not a separator.
        ("Front-End Engineer", "Front-End Engineer"),
        # The filename shape, which is "Company - Role - Location". Its leading
        # segment is the company, so it must not survive as a search title.
        ("Kepler - Backend Platform Engineer - NYC", ""),
        ("Engineer", ""),
        ("", ""),
    ],
)
def test_only_the_role_reaches_noon_as_a_title(written, role):
    """noon turns this line into its title list and searches for people who
    hold them. "NYC" and "Series A" are not titles, and a company name is
    worse than none - a wrong filter excludes the right people silently.
    """
    assert role_title(written) == role


def test_a_decorated_title_is_cleaned_inside_the_preamble():
    assert targeting_preamble(
        title="Backend Platform Engineer - NYC / Series A / Kubernetes",
        location="New York, NY",
    ).splitlines() == [
        "Job title: Backend Platform Engineer",
        "Location: New York, NY",
    ]


def test_a_title_that_does_not_parse_leaves_the_line_out():
    """Silence beats a company name: noon reads the titles out of the JD too."""
    assert targeting_preamble(title="Kepler", location="New York, NY") == (
        "Location: New York, NY"
    )
