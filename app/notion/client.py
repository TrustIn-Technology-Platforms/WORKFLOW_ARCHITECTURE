"""Async Notion API client, scoped to what this pipeline needs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import NotionRow, PipelineError
from app.notion import schema

log = get_logger(__name__)

API_ROOT = "https://api.notion.com/v1"

# The `Notes` log is written as one rich_text item, and Notion caps one at
# 2000 characters; a little under that keeps a chunking edge case away.
NOTES_MAX_CHARS = 1900
# How much of a platform's own words one log line carries.
NOTE_DETAIL_CHARS = 220


def format_note_line(
    at: datetime,
    platform: str,
    action: str,
    *,
    detail: str | None = None,
    url: str | None = None,
    tz_name: str = "UTC",
) -> str:
    """One `Notes` line: `2026-11-03 18:14 · noon · deleted · <url> — <detail>`."""
    try:
        from zoneinfo import ZoneInfo

        stamp = at.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 - an unknown zone name is not worth a lost line
        stamp = at.astimezone(timezone.utc)
    parts = [stamp.strftime("%Y-%m-%d %H:%M"), platform.strip().lower(), action.strip()]
    if url:
        parts.append(url.strip())
    line = " · ".join(parts)
    if detail:
        text = " ".join(detail.split())
        if len(text) > NOTE_DETAIL_CHARS:
            text = text[: NOTE_DETAIL_CHARS - 1].rstrip() + "…"
        line += f" — {text}"
    return line


def _row_property_text(row: NotionRow, column: str) -> str | None:
    """`property_text` with the same loose name match `resolve_property` uses."""
    exact = row.property_text(column)
    if exact:
        return exact
    wanted = _loose(column)
    for name in row.raw_properties:
        if _loose(name) == wanted:
            return row.property_text(name)
    return None


class NotionAPIError(PipelineError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"Notion API {status} ({code}): {message}")
        self.status = status
        self.code = code


class _Retryable(Exception):
    """Internal marker so tenacity retries transport errors and 429/5xx only."""


class NotionClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.notion_token:
            raise PipelineError(
                "NOTION_TOKEN is not set. Create an internal integration at "
                "https://www.notion.so/my-integrations and share the database with it."
            )
        self._client = httpx.AsyncClient(
            base_url=API_ROOT,
            timeout=self.settings.notion_timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.settings.notion_token}",
                "Notion-Version": self.settings.notion_version,
                "Content-Type": "application/json",
            },
        )
        self._schema_cache: dict[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> "NotionClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_Retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        reraise=True,
    )
    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.TransportError as exc:
            raise _Retryable(str(exc)) from exc

        if response.status_code == 429:
            wait_for = float(response.headers.get("Retry-After", "2"))
            log.warning("notion rate limited, sleeping", extra={"seconds": wait_for})
            await asyncio.sleep(wait_for)
            raise _Retryable("rate limited")

        # 409 is Notion's own "a concurrent edit won; try again", and it is
        # the last write of a row - after every platform has posted - that
        # would otherwise die on it.
        if response.status_code == 409 or response.status_code >= 500:
            raise _Retryable(f"server error {response.status_code}")

        if response.status_code >= 400:
            body = _safe_json(response)
            raise NotionAPIError(
                response.status_code,
                body.get("code", "unknown"),
                body.get("message", response.text[:400]),
            )

        return _safe_json(response)

    # ------------------------------------------------------------------
    # database schema
    # ------------------------------------------------------------------

    async def database_schema(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """Property name -> property definition, cached for the client lifetime."""
        if self._schema_cache is not None and not force:
            return self._schema_cache
        data = await self._request(
            "GET", f"/databases/{self.settings.notion_database_id}"
        )
        self._schema_cache = data.get("properties", {}) or {}
        return self._schema_cache

    async def resolve_property(self, name: str) -> tuple[str, str] | None:
        """Find a property by name, falling back to a loose match.

        Column names get renamed between "Post URL", "post_url" and "Post Url".
        Matching loosely stops a rename from silently killing the write-back.
        """
        props = await self.database_schema()
        if name in props:
            return name, props[name]["type"]
        lowered = _loose(name)
        for actual, definition in props.items():
            if _loose(actual) == lowered:
                return actual, definition["type"]
        return None

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    async def query_ready_rows(self, limit: int | None = None) -> list[NotionRow]:
        """Rows sitting at the configured ready status that carry a document."""
        s = self.settings
        status_prop = await self.resolve_property(s.prop_status)
        doc_prop = await self.resolve_property(s.prop_final_document)
        if doc_prop is None:
            raise PipelineError(
                f"Database has no {s.prop_final_document!r} property. "
                "Set PROP_FINAL_DOCUMENT to the real column name."
            )

        filters: list[dict[str, Any]] = []
        if status_prop:
            name, ptype = status_prop
            if ptype in ("status", "select"):
                filters.append({"property": name, ptype: {"equals": s.status_ready}})
            elif ptype == "checkbox":
                filters.append({"property": name, "checkbox": {"equals": True}})

        doc_name, doc_type = doc_prop
        if doc_type in ("url", "rich_text", "title"):
            filters.append({"property": doc_name, doc_type: {"is_not_empty": True}})

        rows = await self._query(filters, limit or s.poll_limit)
        log.info("queried notion", extra={"ready_rows": len(rows)})
        return rows

    async def query_rows_by_status(
        self, status: str, limit: int | None = None
    ) -> list[NotionRow]:
        """Every row whose status column holds `status` - `Posting`, say.

        The status column is a select or a status property; a checkbox column
        has no such value and returns nothing.
        """
        s = self.settings
        status_prop = await self.resolve_property(s.prop_status)
        if status_prop is None:
            raise PipelineError(
                f"Database has no {s.prop_status!r} property. "
                "Set PROP_STATUS to the real column name."
            )
        name, ptype = status_prop
        if ptype not in ("status", "select"):
            return []
        return await self._query(
            [{"property": name, ptype: {"equals": status}}], limit or 100
        )

    async def query_rows_by_status_if_present(
        self, status: str, limit: int | None = None
    ) -> list[NotionRow]:
        """`query_rows_by_status`, but a status the board does not offer is
        an empty result, not an error.

        Notion rejects a filter on a select option that does not exist with a
        400 naming the options it does have. The delete statuses are optional -
        a board that never deletes need not carry them - and the poll that
        asks for `Delete` right after `Ready to Post` must not lose the ready
        rows because the option is missing (2026-10-09, the first poll after
        deploy). Logged, so an operator sees which option to add.
        """
        try:
            return await self.query_rows_by_status(status, limit)
        except NotionAPIError as exc:
            if exc.status == 400 and "not found for property" in str(exc):
                log.warning(
                    "status option missing on the board - skipped",
                    extra={"status": status, "detail": str(exc)[:200]},
                )
                return []
            raise

    async def _query(self, filters: list[dict[str, Any]], limit: int) -> list[NotionRow]:
        s = self.settings
        payload: dict[str, Any] = {"page_size": min(limit, 100)}
        if len(filters) == 1:
            payload["filter"] = filters[0]
        elif filters:
            payload["filter"] = {"and": filters}
        data = await self._request(
            "POST", f"/databases/{s.notion_database_id}/query", json=payload
        )
        return [self._to_row(page) for page in data.get("results", [])][:limit]

    async def get_row(self, page_id: str) -> NotionRow:
        data = await self._request("GET", f"/pages/{normalise_page_id(page_id)}")
        return self._to_row(data)

    async def page_in_trash(self, page_id: str) -> bool | None:
        """Whether the page sits in Notion's trash. None when Notion will not
        say - a 404 is also what an integration that lost access to the
        database gets, and that is no reason to delete anything."""
        try:
            data = await self._request("GET", f"/pages/{normalise_page_id(page_id)}")
        except NotionAPIError as exc:
            if exc.status in (403, 404):
                return None
            raise
        return bool(data.get("in_trash") or data.get("archived"))

    def _to_row(self, page: dict[str, Any]) -> NotionRow:
        s = self.settings
        props = page.get("properties", {}) or {}
        by_lower = {_loose(k): v for k, v in props.items()}

        def pick(name: str) -> dict[str, Any] | None:
            return props.get(name) or by_lower.get(_loose(name))

        title = ""
        for value in props.values():
            if value.get("type") == "title":
                title = schema.plain_text_of(value) or ""
                break

        return NotionRow(
            page_id=page.get("id", ""),
            title=title or "(untitled)",
            document_url=schema.url_of(pick(s.prop_final_document)),
            status=schema.plain_text_of(pick(s.prop_status)),
            platforms=schema.multi_select_names(pick(s.prop_platforms)),
            url=page.get("url"),
            raw_properties=props,
            last_edited=_parse_time(page.get("last_edited_time")),
        )

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------

    async def update_properties(self, page_id: str, values: dict[str, Any]) -> None:
        """Write logical-name -> value, coercing each to its real column type.

        Unknown or unwritable columns are logged and skipped, so a missing
        optional field never turns a successful post into a failed row.
        """
        payload: dict[str, Any] = {}
        for name, value in values.items():
            resolved = await self.resolve_property(name)
            if resolved is None:
                log.warning(
                    "skipping write-back, no such property", extra={"property": name}
                )
                continue
            actual, ptype = resolved
            built = schema.build_value(ptype, value)
            if built is None:
                log.warning(
                    "skipping write-back, unsupported type",
                    extra={"property": actual, "prop_type": ptype},
                )
                continue
            payload[actual] = built

        if not payload:
            return
        await self._request(
            "PATCH",
            f"/pages/{normalise_page_id(page_id)}",
            json={"properties": payload},
        )

    async def mark_posting(self, page_id: str) -> None:
        await self.update_properties(
            page_id, {self.settings.prop_status: self.settings.status_posting}
        )

    async def set_platform_columns(
        self, page_id: str, *, posted: list[str], failed: list[str]
    ) -> None:
        """`Posted On` and `Failed On`, written whole after every platform.

        Called as each platform finishes rather than once at the end, so a
        recruiter watching the row sees `loxo` arrive under Posted On while
        Wellfound is still filling its form - and a run that dies halfway
        leaves an honest record of how far it got.
        """
        s = self.settings
        for column in (s.prop_posted_on, s.prop_failed_on):
            resolved = await self.resolve_property(column)
            if resolved is not None and resolved[1] == "select":
                # Written as one "loxo + noon" option, which works but hides
                # the point: a recruiter cannot tick one platform off. Said
                # once per run rather than failing the row over a column type.
                log.warning(
                    "platform column is a single select - make it a multi-select",
                    extra={"property": resolved[0]},
                )
        await self.update_properties(
            page_id,
            {s.prop_posted_on: sorted(set(posted)), s.prop_failed_on: sorted(set(failed))},
        )

    async def add_note(
        self,
        page_id: str,
        platform: str,
        action: str,
        detail: str | None = None,
        url: str | None = None,
    ) -> None:
        """Prepend one event line to the row's `Notes` log.

        `Notes` is the one place a row says what happened to it per platform
        and when: `Post Status` is a single state for four destinations, and
        `Post URL` keeps the first link only. So the column is a log, newest
        first, and nothing else writes to it whole. The current text is read
        back first; two services writing at once would lose a line, and one
        line lost is better than a column wiped. The oldest lines fall off
        when the column would exceed Notion's limit.
        """
        s = self.settings
        if await self.resolve_property(s.prop_notes) is None:
            return
        row = await self.get_row(page_id)
        current = _row_property_text(row, s.prop_notes) or ""
        line = format_note_line(
            datetime.now(timezone.utc), platform, action, detail=detail, url=url,
            tz_name=s.notes_timezone,
        )
        kept: list[str] = [line]
        size = len(line)
        for old in current.splitlines():
            if not old.strip():
                continue
            if size + 1 + len(old) > NOTES_MAX_CHARS:
                break
            kept.append(old)
            size += 1 + len(old)
        await self.update_properties(page_id, {s.prop_notes: "\n".join(kept)})

    async def mark_posted(
        self, page_id: str, post_url: str | None, detail: str | None = None
    ) -> None:
        """Status, URL, time - and the run's notes, kept out of `Error`.

        `detail` is what the platforms reported about a run that succeeded:
        the search built, what a taxonomy refused, a stage Claude inferred. It
        goes to the `Notes` column when the database has one, and `Error` is
        cleared. Without that column it has nowhere else to go, so it lands in
        `Error` behind a "Posted OK" prefix - read as a failure on 2026-09-03.
        """
        s = self.settings
        values: dict[str, Any] = {
            s.prop_status: s.status_posted,
            s.prop_post_url: post_url,
            s.prop_posted_at: datetime.now(timezone.utc),
            s.prop_error: "",
        }
        # With a `Notes` column the run's notes are already on the row, one
        # line per platform (`add_note`), and the log is never overwritten.
        # Without one they have nowhere to go but `Error`, behind a prefix
        # that says this is not a failure.
        if detail and await self.resolve_property(s.prop_notes) is None:
            values[s.prop_error] = f"Posted OK. Notes: {detail}"[:1800]
        await self.update_properties(page_id, values)

    async def mark_deleting(self, page_id: str) -> None:
        await self.update_properties(
            page_id, {self.settings.prop_status: self.settings.status_deleting}
        )

    async def mark_deleted(self, page_id: str, detail: str | None = None) -> None:
        """Status Deleted, `Error` cleared. What each platform said is already
        in the `Notes` log, line by line; `detail` is kept for a board with
        no such column, where it has nowhere to go."""
        s = self.settings
        values: dict[str, Any] = {
            s.prop_status: s.status_deleted,
            s.prop_error: "",
            # Nothing is up anywhere once the row reads Deleted.
            s.prop_posted_on: [],
            s.prop_failed_on: [],
        }
        if detail and await self.resolve_property(s.prop_notes) is None:
            values[s.prop_error] = f"Deleted OK. Notes: {detail}"[:1800]
        await self.update_properties(page_id, values)

    async def mark_still_posted(self, page_id: str, note: str | None = None) -> None:
        """A delete that took some platforms down and left others up, as
        asked: the row reads Posted again, and `Posted On` says which.
        `note` is why a record was left in place, for `Error`."""
        s = self.settings
        await self.update_properties(
            page_id, {s.prop_status: s.status_posted, s.prop_error: (note or "")[:1800]}
        )

    async def mark_failed(self, page_id: str, error: str) -> None:
        s = self.settings
        await self.update_properties(
            page_id, {s.prop_status: s.status_failed, s.prop_error: error[:1800]}
        )


def _loose(name: str) -> str:
    return name.strip().lower().replace("_", " ").replace("-", " ")


def _parse_time(value: object) -> datetime | None:
    """Notion's ISO-8601 stamps ("2026-09-03T09:34:00.000Z") as aware datetimes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def normalise_page_id(page_id: str) -> str:
    """Accept a bare id, a dashed id, or a full Notion page URL."""
    value = (page_id or "").strip()
    if value.startswith("http"):
        value = value.split("?")[0].rstrip("/").split("/")[-1]
        if "-" in value:
            value = value.split("-")[-1]
    value = value.replace("-", "")
    if len(value) == 32:
        return f"{value[0:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"
    return page_id


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
