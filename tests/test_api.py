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
            "storage_state_age_days",
            "cookie_import_pending",
        }
        assert isinstance(entry["profile"], bool)
        assert isinstance(entry["storage_state"], bool)
        assert isinstance(entry["cookie_import_pending"], bool)


def test_health_flags_a_freshly_uploaded_profile_as_pending(tmp_path, monkeypatch):
    """The upload drops `.import-cookies`; the first browser launch consumes it.
    While it is there, the profile's cookies have not been injected yet."""
    from app.config import get_settings

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


# ----------------------------------------------------------------------
# failure artifacts, retrievable
# ----------------------------------------------------------------------


def _artifact_app(tmp_path, monkeypatch):
    from app.config import get_settings

    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "20260901-wellfound-failed.png").write_bytes(b"png-bytes")
    (root / "loxo-criteria").mkdir()
    (root / "loxo-criteria" / "backup.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ARTIFACT_DIR", str(root))
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_artifacts_need_the_secret(tmp_path, monkeypatch):
    """Traces and screenshots can carry session data, so the listing is gated
    exactly like the upload that put the sessions there."""
    from app.config import get_settings

    client = _artifact_app(tmp_path, monkeypatch)
    try:
        assert client.get("/admin/artifacts").status_code == 401
        assert client.get(
            "/admin/artifacts", headers={"X-Webhook-Secret": "wrong"}
        ).status_code == 401
    finally:
        get_settings.cache_clear()


def test_artifacts_list_and_download(tmp_path, monkeypatch):
    """A server-side failure used to be diagnosed by guesswork, because the
    screenshot it saved sat on a volume nobody could read. Now: list, pull,
    look at what the browser actually saw."""
    from app.config import get_settings

    client = _artifact_app(tmp_path, monkeypatch)
    headers = {"X-Webhook-Secret": "s3cret"}
    try:
        listing = client.get("/admin/artifacts", headers=headers).json()
        names = [a["name"] for a in listing["artifacts"]]
        assert "20260901-wellfound-failed.png" in names
        assert "loxo-criteria/backup.json" in names

        got = client.get(
            "/admin/artifacts/20260901-wellfound-failed.png", headers=headers
        )
        assert got.status_code == 200
        assert got.content == b"png-bytes"
    finally:
        get_settings.cache_clear()


def test_artifact_paths_cannot_escape_the_directory(tmp_path, monkeypatch):
    """The volume holds the session files right next door, so ../ must be a
    404, never a file."""
    from app.config import get_settings

    client = _artifact_app(tmp_path, monkeypatch)
    (tmp_path / "secret.txt").write_text("cookies", encoding="utf-8")
    headers = {"X-Webhook-Secret": "s3cret"}
    try:
        response = client.get("/admin/artifacts/../secret.txt", headers=headers)
        assert response.status_code == 404
        response = client.get("/admin/artifacts/..%2Fsecret.txt", headers=headers)
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()


# -- one row at a time, only while it still says Ready ---------------------------


def test_a_row_is_run_only_while_it_still_reads_ready(monkeypatch):
    """The webhook and the poller can both see a row; whichever takes it first
    marks it Posting, and the other must then leave it alone."""
    import asyncio

    from app import api
    from app.models import NotionRow

    rows = {
        "ready": NotionRow(page_id="ready", title="R", document_url="x", status="Ready to Post"),
        "taken": NotionRow(page_id="taken", title="T", document_url="x", status="Posting"),
    }
    processed: list[str] = []

    class FakeClient:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get_row(self, page_id):
            return rows[page_id]

    async def fake_process_row(row, client, settings, dry_run):
        processed.append(row.page_id)

        class Report:
            ok = True
            post_urls_text = None

        return Report()

    monkeypatch.setattr("app.notion.client.NotionClient", FakeClient)
    monkeypatch.setattr("app.pipeline.process_row", fake_process_row)
    monkeypatch.setattr("app.api.get_settings", lambda: __import__("app.config").config.Settings(
        notion_token="t", notion_database_id="d"))

    asyncio.run(api._run_if_ready("ready", None, source="test"))
    asyncio.run(api._run_if_ready("taken", None, source="test"))
    assert processed == ["ready"]


def test_the_poller_stands_down_in_a_dry_run(monkeypatch):
    """A dry run writes no row back, so every poll would find the same rows and
    drive the real platforms again, for ever (review, 2026-09-03)."""
    import asyncio

    from app import api
    from app.config import Settings

    called: list[str] = []

    class Boom:
        def __init__(self, settings):
            called.append("client")

    monkeypatch.setattr("app.notion.client.NotionClient", Boom)
    settings = Settings(notion_token="t", notion_database_id="d", dry_run=True)
    asyncio.run(asyncio.wait_for(api._poll_ready_rows(settings), timeout=5))
    assert called == [], "the poller queried Notion during a dry run"


# -- the service keeping its own logins alive ----------------------------------------


def test_health_says_which_platforms_can_sign_themselves_in(monkeypatch):
    """Booleans only. The values are secrets; whether they exist is what an
    operator needs to know when a row says 'no stored login'."""
    from app.config import get_settings

    monkeypatch.setenv("NOON_LOGIN_USERNAME", "nicholas@trust-in.co.uk")
    monkeypatch.setenv("NOON_LOGIN_PASSWORD", "hunter2")
    get_settings.cache_clear()
    try:
        body = _health()
    finally:
        get_settings.cache_clear()

    assert body["credentials"]["noon"] is True
    assert body["credentials"]["wellfound"] is False
    assert body["relogin_steps"]["noon"] is True
    assert body["relogin_steps"]["wellfound"] is True
    assert "hunter2" not in str(body)
    assert body["keepalive"]["every_hours"] == 24
    assert body["keepalive"]["relogin"] is True
    assert set(body["keepalive"]) >= {"last_run", "running", "results"}


def test_keepalive_endpoints_are_secret_gated(monkeypatch):
    from app import api
    from app.config import get_settings

    monkeypatch.setenv("WEBHOOK_SECRET", "s3")
    get_settings.cache_clear()
    started: list = []

    async def fake_now(settings, keys):
        started.append(keys)

    monkeypatch.setattr(api, "_keepalive_now", fake_now)
    try:
        client = TestClient(api.create_app())
        assert client.get("/admin/keepalive").status_code == 401
        assert client.post("/admin/keepalive").status_code == 401

        ok = client.get("/admin/keepalive", headers={"X-Webhook-Secret": "s3"})
        assert ok.status_code == 200
        assert ok.json()["results"] == []

        accepted = client.post(
            "/admin/keepalive?platform=noon,Loxo", headers={"X-Webhook-Secret": "s3"}
        )
        assert accepted.status_code == 202
        assert accepted.json()["platforms"] == ["noon", "loxo"]
        everything = client.post("/admin/keepalive", headers={"X-Webhook-Secret": "s3"})
        assert everything.json()["platforms"] == "all"
    finally:
        get_settings.cache_clear()

    assert started == [["noon", "loxo"], None]


def test_keepalive_timer_stands_down_when_switched_off():
    """SESSION_KEEPALIVE_HOURS=0 must return at once, not sleep for ever."""
    import asyncio

    from app import api
    from app.config import Settings

    settings = Settings(_env_file=None, session_keepalive_hours=0)
    asyncio.run(asyncio.wait_for(api._keepalive_sessions(settings), timeout=5))


def test_a_by_hand_round_replaces_only_the_platforms_it_visited(monkeypatch):
    import asyncio

    from app import api
    from app.config import Settings
    from app.platforms.keepalive import KeepaliveResult

    async def fake_keepalive(settings, keys=None, recipes=None):
        return [KeepaliveResult(k, k, "t", ok=True, logged_in=True) for k in (keys or ["noon", "loxo"])]

    monkeypatch.setattr("app.platforms.keepalive.keepalive", fake_keepalive)
    monkeypatch.setattr(api, "_KEEPALIVE_STATE", {"last_run": None, "running": False, "results": []})
    settings = Settings(_env_file=None)

    asyncio.run(api._keepalive_round(settings))
    assert [r["platform"] for r in api._KEEPALIVE_STATE["results"]] == ["noon", "loxo"]
    asyncio.run(api._keepalive_round(settings, ["loxo"]))
    assert sorted(r["platform"] for r in api._KEEPALIVE_STATE["results"]) == ["loxo", "noon"]
    assert api._KEEPALIVE_STATE["last_run"]
    assert api._KEEPALIVE_STATE["running"] is False
