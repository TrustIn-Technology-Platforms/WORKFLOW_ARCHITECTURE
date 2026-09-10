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

    def __init__(self, role):
        self.role = role
        self.calls: list[tuple[str, dict]] = []
        self.deleted = False

    async def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "role_autopilot":
            self.role["autopilot"] = dict(payload["autopilot"])
        if path == "delete_role":
            self.deleted = True
        if path in ("all_roles", "refetch_roles"):
            return [] if self.deleted else [self.role]
        return {}


def test_retire_stops_then_deletes_through_the_portals_own_calls(monkeypatch):
    role = {"id": "r1", "name": "ZZ TEST", "active": True, "autopilot": {"enabled": True, "feedback": ""}}
    session = _Session(role)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=False))

    paths = [c[0] for c in session.calls]
    assert paths[:1] == ["all_roles"]
    stop = next(c for c in session.calls if c[0] == "role_autopilot")
    assert stop[1] == {"id": "r1", "autopilot": {"enabled": False, "feedback": ""}}
    assert "token" not in stop[1]
    delete = next(c for c in session.calls if c[0] == "delete_role")
    assert delete[1] == {"token": "tok", "id": "r1"}
    assert report.sourcing_stopped and report.deleted and not report.warnings


def test_a_dry_run_only_reads(monkeypatch):
    role = {"id": "r1", "name": "ZZ TEST", "active": False, "autopilot": {"enabled": False}}
    session = _Session(role)

    async def fake_capture(page):
        return session

    monkeypatch.setattr("app.platforms.noon_retire.capture_session", fake_capture)
    report = asyncio.run(retire_role(object(), "r1", delete=True, dry_run=True))
    assert {c[0] for c in session.calls} <= {"all_roles", "refetch_roles"}
    assert report.sourcing_was_enabled is False and not report.deleted
