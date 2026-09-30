"""The direct door: jobs over HTTP, beside the Notion one.

RecruitOS (the team's ATS) generates the posting document and already knows
the role's advert fields; a Notion row adds a third system whose only job is
to carry that data here and the outcome back, one status for the whole row.
This module takes the same work as an HTTP job instead - document URL,
platforms, advert fields - and keeps a per-platform state the caller polls,
so "Wellfound failed" no longer hides "the other three posted" and a platform
can be added to a job later without re-posting the ones already up.

The Notion door stays. Both doors run the same pipeline (`load_document`,
`post_document`, `delete_records`) under the same row lock, and record posts
in the same ledger, so a job posted through either can be deleted. A job's
ledger key is the caller's `job_id` (a UUID); stripped of dashes it reads like
a Notion page id, which the trash sweep asks Notion about, gets a 404 for, and
leaves alone by design ("a row Notion will not answer for is left alone").

State lives in `<SESSION_DIR>/direct-jobs.json` beside the ledger: same
volume, same whole-file atomic replace, same "few rows a week" scale. One
process writes it, under the service's row lock; the file lock below only
guards concurrent readers of the HTTP handlers.

No Notion import anywhere in this file - that is the point of it.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.logging_conf import get_logger
from app.models import DeleteResult, NotionRow, Outcome, PostResult

log = get_logger(__name__)

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Platform states a job moves through. Posting: queued -> posting -> posted /
# failed / skipped / dry_run. Deleting: delete_queued -> deleting -> deleted /
# delete_failed / delete_by_hand. `skipped` is a platform with no recipe (a
# tag such as `TrustIn`); `delete_by_hand` is the adapter saying it cannot
# remove this one itself, with the what-and-where in `detail`.
BUSY = {"posting", "deleting"}
REPOSTABLE = {"failed", "skipped", "dry_run", "deleted", "delete_failed", "delete_by_hand"}
DELETABLE = {"posted", "failed", "dry_run", "delete_failed", "delete_by_hand"}


@dataclass(slots=True)
class DirectJob:
    job_id: str
    title: str = ""
    document_url: str = ""
    # Advert fields by the same column names the Notion door uses (Location,
    # Salary, ...), so `enrich_advert` and the drivers read them unchanged.
    fields: dict[str, str] = field(default_factory=dict)
    # platform -> {"status", "url", "detail", "at"}
    platforms: dict[str, dict[str, str]] = field(default_factory=dict)
    # A failure before any platform ran (fetch, parse); platform states carry
    # their own failures.
    error: str | None = None
    # None = the service's DRY_RUN setting decides, as on the Notion door.
    dry_run: bool | None = None
    created_at: str = ""
    updated_at: str = ""

    def state(self, platform: str) -> str:
        return (self.platforms.get(platform) or {}).get("status", "")


class JobStore:
    """`direct-jobs.json`: every job the direct door has taken, by job_id."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # -- storage -----------------------------------------------------------

    def _load(self) -> dict[str, DirectJob]:
        if not self.path.exists():
            return {}
        import json

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Same rule as the ledger: an unreadable store must not be
            # replaced with an empty one, or every job's state is forgotten.
            raise RuntimeError(f"the direct-jobs store at {self.path} is unreadable: {exc}") from exc
        jobs: dict[str, DirectJob] = {}
        for job_id, data in (raw.get("jobs") or {}).items():
            known = {k: v for k, v in data.items() if k in DirectJob.__dataclass_fields__}
            jobs[job_id] = DirectJob(**known)
        return jobs

    def _save(self, jobs: dict[str, DirectJob]) -> None:
        import json
        import os

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "jobs": {k: asdict(v) for k, v in jobs.items()}}
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- reads -------------------------------------------------------------

    def get(self, job_id: str) -> DirectJob | None:
        with _LOCK:
            return self._load().get(_key(job_id))

    def counts(self) -> dict[str, int]:
        """Platform states across every job, for /health."""
        with _LOCK:
            jobs = self._load()
        out: dict[str, int] = {}
        for job in jobs.values():
            for state in job.platforms.values():
                s = state.get("status", "?")
                out[s] = out.get(s, 0) + 1
        return out

    # -- queueing ----------------------------------------------------------

    def queue(
        self,
        job_id: str,
        *,
        title: str,
        document_url: str,
        fields: dict[str, str],
        platforms: list[str],
        force: bool = False,
        dry_run: bool | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Create the job, or extend an existing one with more platforms.

        Returns (queued, skipped). A platform already posted is skipped unless
        `force` - noon, Juicebox and Wellfound all create a duplicate on a
        re-post, so posting twice must be said on purpose. A platform mid-run
        is never queueable; `release_stuck` frees one a dead process held.
        """
        queued: list[str] = []
        skipped: dict[str, str] = {}
        with _LOCK:
            jobs = self._load()
            key = _key(job_id)
            job = jobs.get(key) or DirectJob(job_id=key, created_at=_now())
            job.title = title or job.title
            job.document_url = document_url or job.document_url
            if fields:
                job.fields = dict(fields)
            if dry_run is not None:
                job.dry_run = dry_run
            for name in [p.strip().lower() for p in platforms if p.strip()]:
                state = job.state(name)
                if state in BUSY:
                    skipped[name] = f"a run is under way ({state}); wait for it to finish"
                    continue
                if state == "posted" and not force:
                    skipped[name] = (
                        "already posted - posting again makes a second campaign there; "
                        "repeat with force to do that on purpose"
                    )
                    continue
                # "" (new), queued (idempotent re-ask) and every settled state queue.
                job.platforms[name] = {"status": "queued", "url": (job.platforms.get(name) or {}).get("url", ""), "detail": "", "at": _now()}
                queued.append(name)
            if queued:
                job.error = None
            job.updated_at = _now()
            jobs[key] = job
            self._save(jobs)
        if queued:
            log.info("direct job queued", extra={"job_id": job_id, "queued": queued, "skipped": sorted(skipped)})
        return queued, skipped

    def claim(self, job_id: str) -> list[str]:
        """queued -> posting; returns what was claimed."""
        return self._transition(job_id, {"queued"}, "posting")

    def queue_delete(self, job_id: str, platforms: list[str] | None) -> tuple[list[str], dict[str, str]]:
        """Mark platforms for deleting. None = everything that may exist."""
        queued: list[str] = []
        skipped: dict[str, str] = {}
        with _LOCK:
            jobs = self._load()
            job = jobs.get(_key(job_id))
            if job is None:
                return [], {}
            wanted = [p.strip().lower() for p in platforms if p.strip()] if platforms else list(job.platforms)
            for name in wanted:
                state = job.state(name)
                if not state:
                    skipped[name] = "this job never touched that platform"
                elif state in BUSY:
                    skipped[name] = f"a run is under way ({state})"
                elif state == "deleted":
                    skipped[name] = "already deleted"
                elif state in DELETABLE or state == "delete_queued":
                    job.platforms[name] = {**job.platforms[name], "status": "delete_queued", "at": _now()}
                    queued.append(name)
                else:
                    skipped[name] = f"nothing to delete while {state}"
            job.updated_at = _now()
            jobs[_key(job_id)] = job
            self._save(jobs)
        return queued, skipped

    def claim_delete(self, job_id: str) -> list[str]:
        return self._transition(job_id, {"delete_queued"}, "deleting")

    def _transition(self, job_id: str, from_states: set[str], to_state: str) -> list[str]:
        with _LOCK:
            jobs = self._load()
            job = jobs.get(_key(job_id))
            if job is None:
                return []
            moved: list[str] = []
            for name, state in job.platforms.items():
                if state.get("status") in from_states:
                    job.platforms[name] = {**state, "status": to_state, "at": _now()}
                    moved.append(name)
            if moved:
                job.updated_at = _now()
                self._save(jobs)
            return moved

    # -- outcomes ----------------------------------------------------------

    _POST_STATUS = {
        Outcome.POSTED: "posted",
        Outcome.FAILED: "failed",
        Outcome.SKIPPED: "skipped",
        Outcome.DRY_RUN: "dry_run",
    }
    _DELETE_STATUS = {
        Outcome.DELETED: "deleted",
        Outcome.FAILED: "delete_failed",
        # The adapter cannot remove this one itself; `detail` says what and where.
        Outcome.SKIPPED: "delete_by_hand",
        Outcome.DRY_RUN: "deleted",
    }

    def record_results(self, job_id: str, results: list[PostResult], warnings: list[str]) -> None:
        with _LOCK:
            jobs = self._load()
            job = jobs.get(_key(job_id))
            if job is None:
                return
            failures: list[str] = []
            for r in results:
                name = r.platform.strip().lower()
                status = self._POST_STATUS.get(r.outcome, "failed")
                detail = (r.detail or "")[:1000]
                if warnings and status == "posted":
                    detail = (detail + (" | " if detail else "") + "parse: " + "; ".join(warnings[:3]))[:1000]
                job.platforms[name] = {
                    "status": status,
                    "url": r.post_url or (job.platforms.get(name) or {}).get("url", ""),
                    "detail": detail,
                    "at": _now(),
                }
                if r.outcome is Outcome.FAILED:
                    failures.append(f"{name}: {r.detail or 'failed'}")
            job.error = "; ".join(failures) or None
            job.updated_at = _now()
            self._save(jobs)

    def record_delete_results(self, job_id: str, results: list[DeleteResult]) -> None:
        with _LOCK:
            jobs = self._load()
            job = jobs.get(_key(job_id))
            if job is None:
                return
            for r in results:
                name = r.platform.strip().lower()
                if name not in job.platforms:
                    continue  # a ledger record this job never queued; the ledger has it
                job.platforms[name] = {
                    **job.platforms[name],
                    "status": self._DELETE_STATUS.get(r.outcome, "delete_failed"),
                    "detail": (r.detail or "")[:1000],
                    "at": _now(),
                }
            job.updated_at = _now()
            self._save(jobs)

    def fail_platforms(self, job_id: str, platforms: list[str], message: str) -> None:
        """A failure before the platforms ran (fetch, parse, a crash)."""
        with _LOCK:
            jobs = self._load()
            job = jobs.get(_key(job_id))
            if job is None:
                return
            for name in platforms:
                state = job.platforms.get(name)
                if state is None:
                    continue
                to = "delete_failed" if state.get("status") == "deleting" else "failed"
                job.platforms[name] = {**state, "status": to, "detail": message[:1000], "at": _now()}
            job.error = message[:1000]
            job.updated_at = _now()
            self._save(jobs)

    def release_stuck(self, older_than_minutes: int) -> list[tuple[str, str]]:
        """Free platforms a dead process left mid-run, exactly like the Notion
        door's stuck-row sweep: `posting`/`deleting` untouched for longer than
        any live run takes is a run whose process died. Returns (job, platform)
        pairs released. Called on every read and queue, so a poll self-heals
        the store without another background loop."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        released: list[tuple[str, str]] = []
        with _LOCK:
            jobs = self._load()
            for key, job in jobs.items():
                for name, state in job.platforms.items():
                    if state.get("status") not in BUSY:
                        continue
                    try:
                        at = datetime.fromisoformat(state.get("at", ""))
                    except ValueError:
                        continue  # not knowing is not evidence
                    if at > cutoff:
                        continue
                    was_delete = state["status"] == "deleting"
                    job.platforms[name] = {
                        **state,
                        "status": "delete_failed" if was_delete else "failed",
                        "detail": (
                            "The run never finished - the posting service restarted mid-run. "
                            + ("Whatever was deleted stays deleted; ask the delete again to finish."
                               if was_delete else
                               "Parts may already be posted: check the platform for a saved "
                               "sequence or campaign before re-posting.")
                        ),
                        "at": _now(),
                    }
                    job.updated_at = _now()
                    released.append((key, name))
            if released:
                self._save(jobs)
        for job_id, name in released:
            log.warning("direct job released - stuck", extra={"job_id": job_id, "platform": name})
        return released

    def summary(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "title": job.title,
            "platforms": {k: dict(v) for k, v in job.platforms.items()},
            "error": job.error,
            "dry_run": job.dry_run,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }


def _key(job_id: str) -> str:
    """The same normalisation the ledger applies, so the two agree on one id."""
    return (job_id or "").replace("-", "").strip().lower()


def stand_in_row(job: DirectJob, platforms: list[str]) -> NotionRow:
    """The job as the row the drivers know how to read.

    Same shape as the CLI's `--set` stand-in: each field becomes a rich_text
    property, so `enrich_advert` and the drivers' column reads (`Location`,
    `Juicebox Project`, ...) work on a direct job with no driver change.
    """
    props: dict[str, Any] = {
        name: {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": str(value)}]}
        for name, value in job.fields.items()
        if str(value).strip()
    }
    return NotionRow(
        page_id=job.job_id,
        title=job.title,
        document_url=job.document_url,
        status=None,
        platforms=list(platforms),
        raw_properties=props,
    )


async def run_job(job_id: str, settings: Settings, lock) -> None:
    """Post a job's queued platforms, under the service's one row lock.

    The same shape as the Notion door's `process_row`, with the store where
    the row's status column was: claim, fetch, parse, post, record. Anything
    that fails is written to the job's platform states - the caller polls,
    nothing is returned.
    """
    from app.ledger import Ledger
    from app.pipeline import load_document, post_document

    store = JobStore(settings.direct_jobs_file)
    settings.ensure_dirs()
    async with lock:
        claimed = store.claim(job_id)
        if not claimed:
            log.info("direct job - nothing queued", extra={"job_id": job_id})
            return
        job = store.get(job_id)
        if job is None:  # claim() just saw it, but the store is a file
            return
        dry_run = settings.dry_run if job.dry_run is None else job.dry_run
        log.info(
            "direct job started",
            extra={"job_id": job_id, "title": job.title, "platforms": claimed, "dry_run": dry_run},
        )
        try:
            if not job.document_url:
                raise _no_document()
            document = await load_document(job.document_url, settings)
            if document.is_empty:
                raise _empty_document()
            for warning in document.warnings:
                log.warning("parse warning", extra={"job_id": job_id, "warning": warning})
            results = await post_document(
                document, claimed, row=stand_in_row(job, claimed), settings=settings, dry_run=dry_run
            )
        except Exception as exc:  # noqa: BLE001 - a background task reports, never crashes
            from app.models import PipelineError

            if not isinstance(exc, PipelineError):
                log.exception("direct job crashed", extra={"job_id": job_id})
            message = str(exc) if isinstance(exc, PipelineError) else (
                f"Unexpected error: {exc.__class__.__name__}. Check the logs."
            )
            store.fail_platforms(job_id, claimed, message)
            return
        if not dry_run:
            records = {r.platform: r.records for r in results if r.records}
            try:
                Ledger(settings.ledger_file).record_post(job_id, job.title, records)
            except Exception:  # noqa: BLE001 - posts happened; deleting later needs a person
                log.exception(
                    "could not record the job's posts - deleting it later will need a person",
                    extra={"job_id": job_id, "platforms": sorted(records)},
                )
        store.record_results(job_id, results, document.warnings)
    log.info(
        "direct job done",
        extra={"job_id": job_id, "outcomes": {r.platform: r.outcome.value for r in results}},
    )


async def run_delete(job_id: str, settings: Settings, lock) -> None:
    """Delete a job's marked platforms; what to remove comes from the ledger."""
    from app.pipeline import delete_records

    store = JobStore(settings.direct_jobs_file)
    settings.ensure_dirs()
    async with lock:
        todo = store.claim_delete(job_id)
        if not todo:
            return
        job = store.get(job_id)
        if job is None:
            return
        log.info("direct job delete started", extra={"job_id": job_id, "platforms": todo})
        try:
            # only_named: "take it off noon" must not also take it off the
            # other platforms the ledger remembers for this job.
            report = await delete_records(
                job.job_id, job.title, todo, settings, dry_run=False, only_named=True
            )
        except Exception as exc:  # noqa: BLE001 - the job must not sit on `deleting`
            log.exception("direct job delete crashed", extra={"job_id": job_id})
            store.fail_platforms(job_id, todo, f"Unexpected error: {exc.__class__.__name__}. Check the logs.")
            return
        store.record_delete_results(job_id, report.results)
    log.info(
        "direct job delete done",
        extra={"job_id": job_id, "outcomes": {r.platform: r.outcome.value for r in report.results}},
    )


def _no_document():
    from app.models import PipelineError

    return PipelineError("No document URL was provided on the job.")


def _empty_document():
    from app.models import PipelineError

    return PipelineError(
        "The document produced no advert and no emails. Check that its "
        "headings mark the advert and each email step."
    )
