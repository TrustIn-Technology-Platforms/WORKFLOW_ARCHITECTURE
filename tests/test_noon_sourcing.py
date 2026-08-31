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
    strictest_answer,
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
            },
            "all_roles": [{"id": "role-1", "name": "Halluminate", "autopilot": {"emailCampaign": {"id": "c1"}}}],
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
