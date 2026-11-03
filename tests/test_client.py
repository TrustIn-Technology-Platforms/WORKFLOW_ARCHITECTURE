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


def test_with_a_notes_column_the_final_update_leaves_the_log_alone(monkeypatch):
    """`Notes` is the row's log, written a line at a time by `add_note`; the
    final status update must not overwrite it (2026-11-03)."""
    client, written = _client(monkeypatch, {"Notes", "Error", "Post Status"})
    asyncio.run(client.mark_posted("p", "https://x", "sourcing search: ok"))
    assert "Notes" not in written
    assert written["Error"] == ""
    assert written[client.settings.prop_status] == client.settings.status_posted


def test_a_note_line_carries_time_platform_action_link_and_detail():
    from app.notion.client import format_note_line

    at = datetime(2026, 11, 3, 18, 14, tzinfo=timezone.utc)
    line = format_note_line(at, "Noon", "posted", url="https://noon/r/1",
                            detail="criteria:  3 titles\n2 skills", tz_name="Europe/London")
    assert line == "2026-11-03 18:14 · noon · posted · https://noon/r/1 — criteria: 3 titles 2 skills"
    # British Summer Time shifts the stamp; an unknown zone falls back to UTC.
    summer = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert format_note_line(summer, "loxo", "deleted", tz_name="Europe/London").startswith("2026-07-01 10:00")
    assert format_note_line(summer, "loxo", "deleted", tz_name="Nowhere/Town").startswith("2026-07-01 09:00")
    assert format_note_line(at, "loxo", "failed", detail="x" * 500).endswith("…")


def _notes_client(monkeypatch, existing: str):
    from app.models import NotionRow

    client, written = _client(monkeypatch, {"Notes", "Error", "Post Status"})

    async def get_row(page_id):
        return NotionRow(page_id=page_id, title="Row", document_url=None, status="Posted",
                         raw_properties={"Notes": {"type": "rich_text", "rich_text": [
                             {"plain_text": existing}]}})

    monkeypatch.setattr(client, "get_row", get_row)
    return client, written


def test_add_note_prepends_newest_first(monkeypatch):
    client, written = _notes_client(monkeypatch, "2026-11-03 18:12 · loxo · posted · https://l/1")
    asyncio.run(client.add_note("p", "noon", "deleted", detail="role gone"))
    lines = written["Notes"].splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("· noon · deleted — role gone")
    assert lines[1] == "2026-11-03 18:12 · loxo · posted · https://l/1"


def test_add_note_drops_the_oldest_lines_at_notions_limit(monkeypatch):
    from app.notion.client import NOTES_MAX_CHARS

    old = "\n".join(f"2026-10-0{i % 9 + 1} 09:00 · loxo · posted · https://l/{i}" + "x" * 40 for i in range(60))
    client, written = _notes_client(monkeypatch, old)
    asyncio.run(client.add_note("p", "noon", "posted", url="https://n/1"))
    text = written["Notes"]
    assert len(text) <= NOTES_MAX_CHARS
    assert text.splitlines()[0].endswith("· noon · posted · https://n/1")
    assert text.splitlines()[1].endswith("https://l/0" + "x" * 40)


def test_add_note_is_a_no_op_without_a_notes_column(monkeypatch):
    client, written = _client(monkeypatch, {"Error", "Post Status"})
    asyncio.run(client.add_note("p", "noon", "posted"))
    assert written == {}


def test_without_a_notes_column_the_notes_say_posted_ok_in_error(monkeypatch):
    client, written = _client(monkeypatch, {"Error", "Post Status"})
    asyncio.run(client.mark_posted("p", "https://x", "4 refused"))
    assert written["Error"].startswith("Posted OK. Notes: 4 refused")
    assert "Notes" not in written




def test_notion_timestamps_parse_as_aware_datetimes():
    assert _parse_time("2026-09-03T09:34:00.000Z") == datetime(2026, 9, 3, 9, 34, tzinfo=timezone.utc)
    assert _parse_time(None) is None
    assert _parse_time("not a time") is None


def test_a_failed_run_keeps_the_log_and_writes_the_error(monkeypatch):
    client, written = _client(monkeypatch, {"Notes", "Error", "Post Status"})
    asyncio.run(client.mark_failed("p", "loxo: the session has expired"))
    assert "Notes" not in written
    assert "expired" in written["Error"]


def test_a_partial_delete_puts_the_row_back_on_posted(monkeypatch):
    client, written = _client(monkeypatch, {"Notes", "Error", "Post Status"})
    asyncio.run(client.mark_still_posted("p"))
    assert written[client.settings.prop_status] == client.settings.status_posted
    assert written["Error"] == ""
    assert "Notes" not in written


def test_mark_failed_without_a_notes_column_writes_only_the_error(monkeypatch):
    client, written = _client(monkeypatch, {"Error", "Post Status"})
    asyncio.run(client.mark_failed("p", "boom"))
    assert written["Error"] == "boom"
    assert "Notes" not in written


def test_a_status_option_the_board_lacks_is_an_empty_result_not_an_error(monkeypatch):
    """The first poll after the delete feature shipped asked for `Delete` on a
    board whose Post Status had no such option; Notion's 400 took the whole
    poll down and the Ready to Post row beside it never ran (2026-10-09)."""
    import asyncio

    from app.config import Settings
    from app.notion.client import NotionAPIError, NotionClient

    client = NotionClient(Settings(notion_token="t", notion_database_id="d"))

    async def missing(status, limit=None):
        raise NotionAPIError(
            400, "validation_error",
            'select option "Delete" not found for property "Post Status". '
            'Available options: "Ready to Post", "Posting", "Posted", "Failed".',
        )

    async def broken(status, limit=None):
        raise NotionAPIError(401, "unauthorized", "API token is invalid.")

    monkeypatch.setattr(client, "query_rows_by_status", missing)
    assert asyncio.run(client.query_rows_by_status_if_present("Delete")) == []

    monkeypatch.setattr(client, "query_rows_by_status", broken)
    try:
        asyncio.run(client.query_rows_by_status_if_present("Delete"))
    except NotionAPIError as exc:
        assert exc.status == 401
    else:
        raise AssertionError("a real failure must still raise")
