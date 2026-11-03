"""noon retire: the payload shapes and the report, offline. The calls are
proven live against a ZZ TEST role (docs/platforms/noon.md, "Retiring a role")."""

from __future__ import annotations

import asyncio

from app.platforms.noon_retire import RetireReport, retire_role, stopped_autopilot


def test_stopping_keeps_the_block_and_turns_enabled_off():
    block = {"enabled": True, "feedback": "x", "non_negotiables": ["a"]}
    stopped = stopped_autopilot(block)
    assert stopped == {"enabled": False, "feedback": "x", "non_negotiables": ["a"]}
    assert block["enabled"] is True  # the caller's copy is untouched


def test_the_summary_says_what_happened():
    r = RetireReport(role_id="r1", name="ZZ TEST", sourcing_was_enabled=True,
                     sourcing_stopped=True, deleted=True)
    assert r.summary == "noon role 'ZZ TEST': sourcing stopped, deleted"
    r = RetireReport(role_id="r1", name="ZZ TEST", sourcing_was_enabled=False, deleted=True)
    assert r.summary == "noon role 'ZZ TEST': sourcing was already off, deleted"
    r = RetireReport(role_id="r1", name="ZZ TEST", sourcing_was_enabled=True, dry_run=True)
    assert r.summary == "dry run: noon role 'ZZ TEST' found (sourcing on); nothing changed"


class _Session:
    token = "tok"
    company = "co"
    email = "me@x"

    def __init__(self, role, *, listed=True, tombstone=True):
        self.role = role
        self.calls: list[tuple[str, dict]] = []
        self.deleted = False
        # `listed`: the role list has caught up with the role (it lags by
        # minutes for a new one). `tombstone`: a delete turns the row obsolete,
        # rather than leaving it live (the failure case).
        self.listed = listed
        self.tombstone = tombstone

    async def post(self, path, payload, *, forbidden_ok=False):
        self.calls.append((path, payload))
        if path == "role_autopilot":
            self.role["autopilot"] = dict(payload["autopilot"])
        if path == "delete_role":
            self.deleted = True
        if path == "poll_role_params":
            # The direct read keeps answering for a deleted role (2026-10-09);
            # only the list says it is gone, as a tombstone.
            return {"autopilot": dict(self.role["autopilot"]), "preferences": {}}
        if path == "refetch_roles":
            if not self.listed:
                return []
            if self.deleted and self.tombstone:
                return [{"obsolete": True, "id": self.role["id"]}]
            return [self.role]
        return {}


def test_retire_stops_then_deletes_through_the_portals_own_calls(monkeypatch):
    role = {"id": "r1", "name": "ZZ TEST", "active": True, "autopilot": {"enabled": True, "feedback": ""}}
    session = _Session(role)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=False))

    paths = [c[0] for c in session.calls]
    assert paths[:2] == ["poll_role_params", "refetch_roles"]
    stop = next(c for c in session.calls if c[0] == "role_autopilot")
    assert stop[1] == {"id": "r1", "autopilot": {"enabled": False, "feedback": ""}}
    assert "token" not in stop[1]
    delete = next(c for c in session.calls if c[0] == "delete_role")
    assert delete[1] == {"token": "tok", "id": "r1"}
    assert report.sourcing_stopped and report.deleted and not report.warnings


def test_a_role_the_list_never_knew_counts_as_deleted_with_a_note(monkeypatch):
    # 2026-11-04: a ZZ TEST role deleted minutes after it was made. The list
    # had not caught up, so no tombstone ever appeared, and the direct read
    # still answered - the old read-back called that "still listed".
    role = {"id": "r1", "name": "ZZ TEST", "active": True, "autopilot": {"enabled": True}}
    session = _Session(role, listed=False)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=False))
    assert report.deleted
    assert [c[0] for c in session.calls].count("delete_role") == 1
    assert len(report.warnings) == 1 and "had not caught up" in report.warnings[0]
    assert "delete it by hand" not in report.summary


def test_a_row_still_live_after_the_delete_is_a_failure(monkeypatch):
    role = {"id": "r1", "name": "ZZ TEST", "active": True, "autopilot": {"enabled": False}}
    session = _Session(role, tombstone=False)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    monkeypatch.setattr("app.platforms.noon_retire.LIST_SETTLE_SECONDS", 0.0)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=False))
    assert not report.deleted
    assert any("delete it by hand" in w for w in report.warnings)


def test_a_dry_run_only_reads(monkeypatch):
    role = {"id": "r1", "name": "ZZ TEST", "active": False, "autopilot": {"enabled": False}}
    session = _Session(role)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=True))
    assert {c[0] for c in session.calls} <= {"poll_role_params", "refetch_roles"}
    assert report.sourcing_was_enabled is False and not report.deleted
