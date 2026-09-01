"""The sourcing wizard: the tightening policy, and the calls it makes.

The policy — every nice-to-have promoted, every criterion kept, the strictest
answer chosen — is the part a recruiter would notice getting quietly wrong, and
none of it needs a browser. The call sequence is checked against a stand-in
session so that a payload the portal does not send (or one it does, in the wrong
order) fails here rather than on a live role.
"""

from __future__ import annotations

import asyncio

import pytest

from app.models import PlatformError
from app.platforms.noon_sourcing import (
    SKIP,
    NoonSession,
    as_lines,
    format_feedback,
    parse_criteria,
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


# ----------------------------------------------------------------------
# the call sequence
# ----------------------------------------------------------------------


class FakeSession(NoonSession):
    """Answers like noon does, and remembers what it was asked."""

    def __init__(self, **responses):
        self.calls: list[tuple[str, dict]] = []
        self.responses = {
            "generate_params": {
                "must_haves": "5+ years on platform teams\nKubernetes",
                "nice_to_haves": "Terraform",
                "titles": ["Platform Engineer"],
                "location": "Manchester, UK",
            },
            "all_roles": [{
                "id": "role-1",
                "name": "Halluminate",
                "autopilot": {"emailCampaign": {"id": "c1"}},
                # The filters `generate_params` saved on its way through.
                "preferences": {
                    "location": ["Manchester, UK"],
                    "titles": ["Platform Engineer", "Site Reliability Engineer"],
                },
            }],
            "gpt_stream": "*Must have 5+ years on platform teams\n*Require Kubernetes\n*Require Terraform",
            "clarifying_questions": {
                "Is Terraform required?": ["Yes", "No"],
                "Which matters more?": ["Depth", "Breadth"],
            },
            **responses,
        }
        # NoonSession is a slots dataclass; only the fields it declares exist.
        super().__init__(page=None, token="tok", company="co")

    async def post(self, path, payload):  # type: ignore[override]
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
    session = FakeSession()
    report = _run(session)

    assert session.paths() == [
        "generate_params",
        "all_roles",
        "set_candidate_source",
        "setup_clarifying_questions",
        "gpt_stream",
        "role_autopilot",       # non-negotiables selected
        "rank_non_negotiables",
        "role_autopilot",       # ranked, initialization: true
        "clarifying_questions",
        "mark_clarifying_question",
        "mark_clarifying_question",
        "role_autopilot",       # the one that starts the search
    ]
    assert report.started_sourcing is True


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
    assert len(report.non_negotiables) == 3


def test_the_last_call_is_what_starts_the_search():
    """`initialization: false` is the go signal - true only saves."""
    started = FakeSession()
    _run(started)
    assert started.payloads("role_autopilot")[-1]["initialization"] is False

    held = FakeSession()
    report = _run(held, start_sourcing=False)
    assert held.payloads("role_autopilot")[-1]["initialization"] is True
    assert report.started_sourcing is False


def test_an_unanswerable_question_is_skipped_and_said_out_loud():
    session = FakeSession()
    report = _run(session)

    assert report.answers == {
        "Is Terraform required?": "Yes",
        "Which matters more?": SKIP,
    }
    assert any("clarifying question" in w for w in report.warnings)


def test_a_dry_run_saves_nothing():
    session = FakeSession()
    report = _run(session, dry_run=True)

    assert session.paths() == ["generate_params"]
    assert session.payload("generate_params")["dont_save"] is True
    assert report.must_haves == [
        "5+ years on platform teams",
        "Kubernetes",
        "Terraform",
    ]
    assert report.started_sourcing is False


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
    session = FakeSession(all_roles=[{"id": "someone-elses-role"}])
    with pytest.raises(PlatformError, match="no role"):
        _run(session)


# ----------------------------------------------------------------------
# the search filters - location and titles
# ----------------------------------------------------------------------


def test_the_preamble_states_the_facts_the_advert_leaves_out():
    """Only what is known, and only what noon filters on.

    An empty column contributes no line at all - "Location:" with nothing after
    it is worse than silence. Salary is not offered at all: noon has no
    compensation preference, so it could only become a criterion, and every
    criterion here gets starred as a non-negotiable.
    """
    assert targeting_preamble(
        title="Senior Platform Engineer",
        location="Manchester, UK",
        employment_type="Permanent",
        skills=["Kubernetes", "Terraform", ""],
    ).splitlines() == [
        "Job title: Senior Platform Engineer",
        "Location: Manchester, UK",
        "Employment type: Permanent",
        "Key skills: Kubernetes, Terraform",
    ]
    assert targeting_preamble() == ""


def test_the_facts_reach_noon_above_the_job_description():
    """`generate_params` is the only call that writes the role's filters, and it
    writes what it can read - so the location has to be in the text.
    """
    session = FakeSession()
    report = _run(session, targeting="Location: Manchester, UK")

    jd = session.payload("generate_params")["jd"]
    assert jd.startswith("Location: Manchester, UK")
    assert jd.endswith("JD text")
    assert report.location == "Manchester, UK"
    assert report.titles == ["Platform Engineer", "Site Reliability Engineer"]
    assert "location Manchester, UK" in report.summary


def test_a_role_that_would_be_searched_globally_says_so():
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": ["Platform Engineer"],
            "location": "",
        },
        all_roles=[{"id": "role-1", "preferences": {"titles": ["Platform Engineer"]}}],
    )
    report = _run(session)

    assert report.location == ""
    assert any("searched globally" in w for w in report.warnings)
    assert "no location" in report.summary


def test_a_location_read_but_not_saved_is_reported_as_not_saved():
    """Extracting it and saving it are two different things, and only the second
    one narrows the search.
    """
    session = FakeSession(
        all_roles=[{"id": "role-1", "preferences": {"titles": ["Platform Engineer"]}}]
    )
    report = _run(session, targeting="Location: Manchester, UK")

    assert any("did not save it" in w for w in report.warnings)


def test_no_titles_on_the_role_is_worth_a_warning():
    session = FakeSession(
        generate_params={
            "must_haves": "Kubernetes",
            "nice_to_haves": "",
            "titles": [],
            "location": "Manchester, UK",
        }
    )
    report = _run(session)
    assert any("no job titles" in w for w in report.warnings)


def test_a_dry_run_still_reports_the_filters_it_would_have_set():
    """The cheapest place to find out the location is missing is before the run."""
    session = FakeSession()
    report = _run(session, dry_run=True, targeting="Location: Manchester, UK")

    assert report.location == "Manchester, UK"
    assert report.titles == ["Platform Engineer"]


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
    """noon turns this line into `preferences.titles` and searches for people
    who hold them. "NYC" and "Series A" are not titles, and a company name is
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
