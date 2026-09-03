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
