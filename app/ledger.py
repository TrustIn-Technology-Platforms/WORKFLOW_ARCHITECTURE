"""What every posted row created, kept so the row can be deleted later.

Notion's `Post URL` column holds one link, and a row that posted to three
platforms keeps only the first (schema.build_value).
Deleting a row needs all of them - and more than a link: noon deletes by role
uuid, Juicebox by sequence and project id. So each post's `PostResult.records`
is written here, keyed by the Notion page, one entry per post: a row re-run
after a failure posted twice, and both copies have to go.

A JSON file beside the sessions, so on Railway it lives on the volume and
survives deploys. Rows are few (tens a week), and every write is a whole-file
replace through a temporary file, so a crash mid-write leaves the old ledger
rather than half a new one. One process writes it, under the service's row
lock.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.logging_conf import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class LedgerEntry:
    page_id: str
    title: str = ""
    posted_at: str = ""
    # platform -> one records dict per post of this row
    records: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    # platform -> when its records were confirmed deleted
    deleted: dict[str, str] = field(default_factory=dict)
    # When a sweep first found the row in Notion's trash; cleared if restored.
    trashed_seen_at: str | None = None
    # Set once every platform is deleted; the entry is kept as a history.
    done_at: str | None = None
    # The last delete attempt's per-platform outcome, for /health and the CLI.
    last_attempt: dict[str, str] = field(default_factory=dict)

    @property
    def open_platforms(self) -> list[str]:
        return [p for p in self.records if p not in self.deleted]

    @property
    def is_open(self) -> bool:
        return self.done_at is None and bool(self.open_platforms)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # -- storage ---------------------------------------------------------

    def _load(self) -> dict[str, LedgerEntry]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A ledger that cannot be read must not be overwritten with an
            # empty one - that would forget every row's records at once.
            raise RuntimeError(f"the posted-rows ledger at {self.path} is unreadable: {exc}") from exc
        entries: dict[str, LedgerEntry] = {}
        for page_id, data in (raw.get("rows") or {}).items():
            known = {k: v for k, v in data.items() if k in LedgerEntry.__dataclass_fields__}
            entries[page_id] = LedgerEntry(**known)
        return entries

    def _save(self, entries: dict[str, LedgerEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "rows": {k: asdict(v) for k, v in entries.items()}}
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- reads -----------------------------------------------------------

    def get(self, page_id: str) -> LedgerEntry | None:
        with _LOCK:
            return self._load().get(_key(page_id))

    def entries(self) -> list[LedgerEntry]:
        with _LOCK:
            return list(self._load().values())

    def open_entries(self) -> list[LedgerEntry]:
        return [e for e in self.entries() if e.is_open]

    # -- writes ----------------------------------------------------------

    def record_post(
        self, page_id: str, title: str, records: dict[str, dict[str, str]]
    ) -> None:
        """Add one post's records. Platforms with nothing recorded are left out."""
        records = {p: r for p, r in records.items() if r}
        if not records:
            return
        with _LOCK:
            entries = self._load()
            key = _key(page_id)
            entry = entries.get(key) or LedgerEntry(page_id=key)
            entry.title = title or entry.title
            entry.posted_at = _now()
            for platform, record in records.items():
                entry.records.setdefault(platform, []).append(dict(record))
                # A re-post makes something new to delete, even on a platform
                # an earlier delete had cleared.
                entry.deleted.pop(platform, None)
            entry.done_at = None
            entries[key] = entry
            self._save(entries)
        log.info("ledger recorded post", extra={"page_id": key, "platforms": sorted(records)})

    def record_delete(self, page_id: str, outcomes: dict[str, tuple[bool, str]]) -> LedgerEntry | None:
        """`outcomes` is platform -> (deleted, detail). Returns the updated entry."""
        with _LOCK:
            entries = self._load()
            entry = entries.get(_key(page_id))
            if entry is None:
                return None
            for platform, (ok, detail) in outcomes.items():
                entry.last_attempt[platform] = detail[:300]
                if ok:
                    entry.deleted[platform] = _now()
            if not entry.open_platforms:
                entry.done_at = _now()
            self._save(entries)
            return entry

    def set_trashed_seen(self, page_id: str, seen: bool) -> str | None:
        """Note (or clear) the row being in Notion's trash. Returns the first
        time it was seen there."""
        with _LOCK:
            entries = self._load()
            entry = entries.get(_key(page_id))
            if entry is None:
                return None
            if seen and not entry.trashed_seen_at:
                entry.trashed_seen_at = _now()
            elif not seen:
                entry.trashed_seen_at = None
            self._save(entries)
            return entry.trashed_seen_at


def _key(page_id: str) -> str:
    """One key per page whatever form the id arrived in (dashed or not)."""
    return (page_id or "").replace("-", "").strip().lower()


def summary(entry: LedgerEntry) -> dict[str, Any]:
    """Plain values for /health and the CLI."""
    return {
        "page_id": entry.page_id,
        "title": entry.title,
        "posted_at": entry.posted_at,
        "platforms": sorted(entry.records),
        "deleted": sorted(entry.deleted),
        "trashed_seen_at": entry.trashed_seen_at,
        "done_at": entry.done_at,
        "last_attempt": dict(entry.last_attempt),
    }
