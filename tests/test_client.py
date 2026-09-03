"""The Notion client's write-back shape, without the network.

Where a successful run's notes land decides whether a recruiter reads them as
information or as a failure: on 2026-09-03 they landed in `Error` and were
reported as "I got this error".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import Settings
from app.notion.client import NotionClient, _parse_time


def _client(monkeypatch, columns: set[str]) -> tuple[NotionClient, dict]:
    settings = Settings(notion_token="t", notion_database_id="d")
    client = NotionClient(settings)
    written: dict = {}

    async def resolve(name: str):
        return (name, "rich_text") if name in columns else None

    async def update(page_id: str, values: dict) -> None:
        written.update(values)

    monkeypatch.setattr(client, "resolve_property", resolve)
    monkeypatch.setattr(client, "update_properties", update)
    return client, written


def test_notes_go_to_the_notes_column_and_error_is_cleared(monkeypatch):
    client, written = _client(monkeypatch, {"Notes", "Error", "Post Status"})
    asyncio.run(client.mark_posted("p", "https://x", "sourcing search: ok"))
    assert written["Notes"] == "sourcing search: ok"
    assert written["Error"] == ""
    assert written[client.settings.prop_status] == client.settings.status_posted


def test_without_a_notes_column_the_notes_say_posted_ok_in_error(monkeypatch):
    client, written = _client(monkeypatch, {"Error", "Post Status"})
    asyncio.run(client.mark_posted("p", "https://x", "4 refused"))
    assert written["Error"].startswith("Posted OK. Notes: 4 refused")
    assert "Notes" not in written


def test_a_run_with_nothing_to_say_leaves_error_empty(monkeypatch):
    client, written = _client(monkeypatch, {"Notes", "Error"})
    asyncio.run(client.mark_posted("p", "https://x", None))
    assert written["Error"] == ""
    assert "Notes" not in written


def test_notion_timestamps_parse_as_aware_datetimes():
    assert _parse_time("2026-09-03T09:34:00.000Z") == datetime(2026, 9, 3, 9, 34, tzinfo=timezone.utc)
    assert _parse_time(None) is None
    assert _parse_time("not a time") is None
