"""The health endpoint, which is the only way to see the server's state.

It must answer without touching Notion or launching a browser, and it must say
whether the logins actually reached the volume - otherwise the only way to find
that out is to spend a row and read the failure.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import create_app


def _health() -> dict:
    return TestClient(create_app()).get("/health").json()


def test_health_answers_without_notion_or_a_browser():
    body = _health()
    assert body["status"] == "ok"
    assert body["platforms_total"] >= 1
    assert "wellfound" in body["platforms_enabled"]


def test_health_reports_whether_each_platform_has_a_profile():
    """`profile: false` here is the whole cause of "<Platform> has no browser
    profile in ..." - the upload never arrived, or arrived somewhere else. The
    directory is reported alongside so the two can be told apart."""
    body = _health()

    assert body["profile_dir"], "the directory it looked in must be reported"
    for key in body["platforms_enabled"]:
        entry = body["profiles"][key]
        assert set(entry) == {
            "profile",
            "profile_age_days",
            "storage_state",
            "cookie_import_pending",
        }
        assert isinstance(entry["profile"], bool)
        assert isinstance(entry["storage_state"], bool)
        assert isinstance(entry["cookie_import_pending"], bool)


def test_health_flags_a_freshly_uploaded_profile_as_pending(tmp_path, monkeypatch):
    """The upload drops `.import-cookies`; the first browser launch consumes it.
    While it is there, the profile's cookies have not been injected yet."""
    from app.config import Settings, get_settings

    profiles = tmp_path / "profiles"
    (profiles / "wellfound").mkdir(parents=True)
    (profiles / "wellfound" / ".login-verified").write_text("{}", encoding="utf-8")
    (profiles / "wellfound" / ".import-cookies").touch()

    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(profiles))
    get_settings.cache_clear()
    try:
        body = TestClient(create_app()).get("/health").json()
        entry = body["profiles"]["wellfound"]
        assert entry["profile"] is True
        assert entry["cookie_import_pending"] is True
    finally:
        monkeypatch.delenv("BROWSER_PROFILE_DIR", raising=False)
        get_settings.cache_clear()
