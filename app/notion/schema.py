"""Read/write helpers for Notion's property value shapes.

Notion returns every property as a tagged union keyed on its type, and expects
the same shape back on update. These helpers keep that noise out of the
pipeline, and - importantly - build the *write* payload from the database's
real property type rather than a guess, because `status` and `select` look
identical to a human but reject each other's payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

TEXTUAL_TYPES = ("title", "rich_text")


def plain_text_of(prop: dict[str, Any] | None) -> str | None:
    """Best-effort flatten of any property value to a plain string."""
    if not prop:
        return None

    ptype = prop.get("type")
    value = prop.get(ptype) if ptype else None

    if ptype in TEXTUAL_TYPES:
        return _join_rich_text(value) or None
    if ptype == "url":
        return value or None
    if ptype == "email":
        return value or None
    if ptype == "phone_number":
        return value or None
    if ptype == "number":
        return None if value is None else str(value)
    if ptype == "checkbox":
        return "true" if value else "false"
    if ptype in ("select", "status"):
        return (value or {}).get("name") or None
    if ptype == "multi_select":
        names = [opt.get("name", "") for opt in (value or [])]
        return ", ".join(n for n in names if n) or None
    if ptype == "date":
        return (value or {}).get("start") or None
    if ptype == "people":
        names = [p.get("name", "") for p in (value or [])]
        return ", ".join(n for n in names if n) or None
    if ptype == "files":
        return _first_file_url(value or [])
    if ptype == "formula":
        return _plain_text_of_formula(value or {})
    if ptype == "rollup":
        return _plain_text_of_rollup(value or {})
    if ptype == "unique_id":
        prefix = (value or {}).get("prefix") or ""
        number = (value or {}).get("number")
        return f"{prefix}{number}" if number is not None else None
    if ptype == "created_time":
        return value or None
    if ptype == "last_edited_time":
        return value or None
    return None


def _join_rich_text(items: list[dict[str, Any]] | None) -> str:
    if not items:
        return ""
    return "".join(item.get("plain_text", "") for item in items).strip()


def _first_file_url(files: list[dict[str, Any]]) -> str | None:
    for entry in files:
        ftype = entry.get("type")
        if ftype == "external":
            url = (entry.get("external") or {}).get("url")
        else:
            url = (entry.get("file") or {}).get("url")
        if url:
            return url
    return None


def _plain_text_of_formula(value: dict[str, Any]) -> str | None:
    ftype = value.get("type")
    inner = value.get(ftype) if ftype else None
    if inner is None:
        return None
    if ftype == "date":
        return (inner or {}).get("start")
    return str(inner)


def _plain_text_of_rollup(value: dict[str, Any]) -> str | None:
    rtype = value.get("type")
    if rtype == "array":
        parts = [plain_text_of(item) for item in value.get("array", [])]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    inner = value.get(rtype) if rtype else None
    return None if inner is None else str(inner)


def url_of(prop: dict[str, Any] | None) -> str | None:
    """Pull a URL out of a property regardless of how it was typed.

    `final_document` is very often a URL column, but people paste links into
    rich_text or attach them as files just as readily, so accept all of them.
    """
    if not prop:
        return None
    text = plain_text_of(prop)
    if not text:
        return None
    candidate = text.strip().split()[0] if text.strip() else ""
    return candidate if candidate.lower().startswith(("http://", "https://")) else None


def multi_select_names(prop: dict[str, Any] | None) -> list[str]:
    if not prop:
        return []
    ptype = prop.get("type")
    if ptype == "multi_select":
        return [o["name"] for o in prop.get("multi_select") or [] if o.get("name")]
    if ptype in ("select", "status"):
        name = (prop.get(ptype) or {}).get("name")
        return [name] if name else []
    text = plain_text_of(prop)
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


# --------------------------------------------------------------------------
# Write payloads
# --------------------------------------------------------------------------

MAX_RICH_TEXT = 2000  # Notion rejects a single rich_text item beyond this.


def build_value(prop_type: str, value: Any) -> dict[str, Any] | None:
    """Build an update payload for `prop_type`, or None if unsupported.

    Returning None (rather than raising) lets the caller skip a write-back
    field it cannot satisfy instead of failing an otherwise successful post.
    """
    if prop_type in ("title", "rich_text"):
        return {prop_type: rich_text(str(value) if value is not None else "")}
    if prop_type == "url":
        return {"url": str(value) if value else None}
    if prop_type == "email":
        return {"email": str(value) if value else None}
    if prop_type == "select":
        return {"select": {"name": str(value)} if value else None}
    if prop_type == "status":
        return {"status": {"name": str(value)} if value else None}
    if prop_type == "multi_select":
        names = value if isinstance(value, (list, tuple)) else [value]
        return {"multi_select": [{"name": str(n)} for n in names if n]}
    if prop_type == "checkbox":
        return {"checkbox": bool(value)}
    if prop_type == "number":
        try:
            return {"number": None if value is None else float(value)}
        except (TypeError, ValueError):
            return None
    if prop_type == "date":
        return {"date": {"start": _as_iso(value)} if value else None}
    if prop_type == "people":
        return None
    return None


def rich_text(text: str) -> list[dict[str, Any]]:
    """Chunk text into rich_text items that respect Notion's per-item limit."""
    text = text or ""
    if not text:
        return []
    chunks = [
        text[i : i + MAX_RICH_TEXT] for i in range(0, len(text), MAX_RICH_TEXT)
    ]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks[:100]]


def _as_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
