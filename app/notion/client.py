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

        if response.status_code >= 500:
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
        if detail:
            if await self.resolve_property(s.prop_notes) is not None:
                values[s.prop_notes] = detail[:1800]
            else:
                values[s.prop_error] = f"Posted OK. Notes: {detail}"[:1800]
        await self.update_properties(page_id, values)

    async def mark_failed(self, page_id: str, error: str) -> None:
        s = self.settings
        await self.update_properties(
            page_id,
            {s.prop_status: s.status_failed, s.prop_error: error[:1800]},
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
