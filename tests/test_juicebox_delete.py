"""Deleting a row's Juicebox sequence and project - the parts that need no browser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.platforms.juicebox_delete import (
    JuiceboxDeleteReport,
    _items,
    project_id,
    project_matches,
    sequence_id,
)

POSTED = datetime(2026, 9, 23, 17, 43, tzinfo=timezone.utc)


def _project(title: str, created: datetime = POSTED, **extra) -> dict:
    return {"id": "PJFFhvXqprbdhDfEoYFr", "title": title,
            "dateAdded": {"_seconds": int(created.timestamp()), "_nanoseconds": 0}, **extra}


def test_ids_come_out_of_every_url_shape_the_app_uses():
    assert sequence_id("https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW/sequences/2eWj7CrfhpTnY5QoqLXN") == "2eWj7CrfhpTnY5QoqLXN"
    assert sequence_id("https://app.juicebox.ai/project/x/sequences?step=edit&createdSequenceId=ooWdfjAZcZbS1kgOZuJA") == "ooWdfjAZcZbS1kgOZuJA"
    assert project_id("https://app.juicebox.ai/project/Yn6AP0NTqhYxlv1uMrbJ/search?search_id=1") == "Yn6AP0NTqhYxlv1uMrbJ"
    assert sequence_id("https://app.juicebox.ai/projects") is None


def test_the_project_list_is_read_across_all_its_groups():
    """`/api/projects` answers {result: {your_agents: [...], your_projects: [...]}}."""
    data = {"result": {"your_agents": [{"id": "a"}], "your_projects": [{"id": "b"}], "team_projects": []}}
    assert [p["id"] for p in _items(data)] == ["a", "b"]
    assert [s["id"] for s in _items({"result": [{"id": "s"}]})] == ["s"]


def test_the_project_the_run_created_passes_named_or_unrenamed():
    assert project_matches(_project("Axle - Infra Eng"), names=("Axle - Infra Eng",), posted_at=POSTED) is None
    # The rename does not always stick (18 such projects on 2026-09-23).
    assert project_matches(_project("New Project"), names=("Axle - Infra Eng",), posted_at=POSTED + timedelta(minutes=10)) is None


def test_a_real_client_project_behind_a_wrong_id_is_refused():
    """2026-09-23: create_project returned the project the browser was already
    in - 'Rowspace Infrastructure Engineer' - and a delete trusting that id
    would have taken a client's searches. Only Juicebox's own refusal saved it."""
    rowspace = _project("Rowspace Infrastructure Engineer", created=datetime(2026, 5, 6, tzinfo=timezone.utc))
    why = project_matches(rowspace, names=("ZZ TEST delete me",), posted_at=POSTED)
    assert why and "Rowspace" in why


def test_a_project_from_another_time_or_with_an_agent_is_refused():
    old = _project("New Project", created=POSTED - timedelta(days=3))
    assert "not during the run" in project_matches(old, names=(), posted_at=POSTED)
    agent = _project("New Project", isAgenticProject=True, agentStatus={"status": "in_progress"})
    assert "agent" in project_matches(agent, names=(), posted_at=POSTED)
    assert "could not be checked" in project_matches(_project("New Project"), names=(), posted_at=None)


def test_a_refused_project_keeps_the_row_from_reading_as_deleted():
    report = JuiceboxDeleteReport(sequence_id="s", sequence_deleted=True,
                                  project_id="p", project_refused="it is called 'X'")
    assert not report.complete
    assert "left alone" in report.summary
    assert JuiceboxDeleteReport(sequence_id="s", sequence_deleted=True).complete
