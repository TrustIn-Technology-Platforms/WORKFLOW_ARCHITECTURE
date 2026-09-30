"""The direct door: jobs over HTTP instead of Notion rows (app/direct.py).

RecruitOS queues a job per posting row, polls its per-platform states and asks
deletes per platform. The store is the door's whole memory, so the tests here
are about state: what queues, what refuses, what a crash leaves behind.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.direct import DirectJob, JobStore, stand_in_row
from app.models import DeleteResult, Outcome, PostResult


def _store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "direct-jobs.json")


def _posted(store: JobStore, job_id: str, platforms: list[str]) -> None:
    """Queue, claim and mark the platforms posted - a job as RecruitOS sees it
    after a good run."""
    store.queue(job_id, title="T", document_url="u", fields={}, platforms=platforms)
    store.claim(job_id)
    store.record_results(
        job_id,
        [PostResult(platform=p, outcome=Outcome.POSTED, post_url=f"https://{p}/x")
         for p in platforms],
        [],
    )


def test_a_job_queues_new_platforms_and_extends_with_more(tmp_path):
    """Adding Loxo to a role already queued on noon is the same job_id again;
    dashes and case must not make it a second job (the ledger strips them too)."""
    store = _store(tmp_path)
    queued, skipped = store.queue(
        "JOB-1", title="Axle - Eng", document_url="http://x/d.docx",
        fields={"Location": "London"}, platforms=["noon"],
    )
    assert queued == ["noon"] and skipped == {}

    queued, skipped = store.queue("job1", title="", document_url="", fields={}, platforms=["Loxo"])
    assert queued == ["loxo"] and skipped == {}

    job = store.get("JOB-1")
    assert set(job.platforms) == {"noon", "loxo"}
    assert job.title == "Axle - Eng"  # blanks on the re-ask keep what is there
    assert job.document_url == "http://x/d.docx"
    assert job.fields == {"Location": "London"}


def test_a_posted_platform_is_skipped_unless_forced(tmp_path):
    """noon, Juicebox and Wellfound all make a duplicate on a re-post, so
    posting twice must be said on purpose."""
    store = _store(tmp_path)
    _posted(store, "j", ["noon"])

    queued, skipped = store.queue("j", title="T", document_url="u", fields={}, platforms=["noon"])
    assert queued == [] and "force" in skipped["noon"]

    queued, _ = store.queue("j", title="T", document_url="u", fields={}, platforms=["noon"], force=True)
    assert queued == ["noon"]


def test_a_platform_mid_run_is_never_queueable(tmp_path):
    store = _store(tmp_path)
    store.queue("j", title="T", document_url="u", fields={}, platforms=["noon"])
    store.claim("j")

    queued, skipped = store.queue("j", title="T", document_url="u", fields={}, platforms=["noon"])
    assert queued == [] and "under way" in skipped["noon"]
    queued, skipped = store.queue_delete("j", ["noon"])
    assert queued == [] and "under way" in skipped["noon"]


def test_delete_marks_named_platforms_or_everything(tmp_path):
    """Per-platform take-down: "off noon" leaves Juicebox up; asking with no
    names means everything the job may have put up."""
    store = _store(tmp_path)
    _posted(store, "j", ["noon", "juicebox"])

    queued, skipped = store.queue_delete("j", ["noon", "wellfound"])
    assert queued == ["noon"]
    assert "never touched" in skipped["wellfound"]
    assert store.get("j").state("juicebox") == "posted"

    queued, _ = store.queue_delete("j", None)
    assert set(queued) == {"noon", "juicebox"}


def test_release_stuck_frees_what_a_dead_process_held(tmp_path):
    """A deploy mid-run leaves `posting` for ever; the poll must turn that into
    a failure a person can read, not a state nothing can leave."""
    store = _store(tmp_path)
    store.queue("j", title="T", document_url="u", fields={}, platforms=["noon", "loxo"])
    store.claim("j")

    path = tmp_path / "direct-jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    data["jobs"]["j"]["platforms"]["noon"]["at"] = stale
    path.write_text(json.dumps(data), encoding="utf-8")

    assert store.release_stuck(45) == [("j", "noon")]
    job = store.get("j")
    assert job.state("noon") == "failed"
    assert "restarted mid-run" in job.platforms["noon"]["detail"]
    assert job.state("loxo") == "posting"  # fresh, still someone's run
    assert store.release_stuck(45) == []


def test_an_unreadable_store_is_never_replaced_with_an_empty_one(tmp_path):
    """Same rule as the ledger: overwriting it would forget every job's state."""
    path = tmp_path / "direct-jobs.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        JobStore(path).queue("j", title="", document_url="u", fields={}, platforms=["noon"])
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_direct_job_reads_like_a_row_to_the_drivers(tmp_path):
    """The drivers read columns through the Notion property shape; a direct
    job's fields must come back through the same reader, blanks left out."""
    from app.notion.schema import plain_text_of

    job = DirectJob(job_id="j", title="Axle - Eng", fields={"Location": "London", "Salary": "  "})
    row = stand_in_row(job, ["noon"])

    assert row.title == "Axle - Eng" and row.platforms == ["noon"]
    assert plain_text_of(row.raw_properties["Location"]) == "London"
    assert "Salary" not in row.raw_properties


def test_run_job_posts_the_queued_platforms_and_records_everywhere(tmp_path, monkeypatch):
    """The whole posting path: claim, fetch, post; the outcome lands in the job
    store for the caller's poll and in the ledger for a later delete."""
    from types import SimpleNamespace

    from app import direct
    from app.config import Settings
    from app.ledger import Ledger

    settings = Settings(
        _env_file=None,
        session_dir=str(tmp_path / "sessions"),
        ledger_path=str(tmp_path / "posted-rows.json"),
        direct_jobs_path=str(tmp_path / "direct-jobs.json"),
    )
    store = JobStore(settings.direct_jobs_file)
    store.queue("j", title="Axle - Eng", document_url="http://x/d.docx",
                fields={"Location": "London"}, platforms=["noon"])

    seen: dict = {}

    async def fake_load(url, s):
        seen["url"] = url
        return SimpleNamespace(is_empty=False, warnings=[])

    async def fake_post(document, platforms, row=None, settings=None, dry_run=None):
        seen["platforms"] = list(platforms)
        seen["row"] = row
        return [PostResult(platform="noon", outcome=Outcome.POSTED,
                           post_url="https://noon/r1", records={"role": "r1"})]

    monkeypatch.setattr("app.pipeline.load_document", fake_load)
    monkeypatch.setattr("app.pipeline.post_document", fake_post)

    asyncio.run(direct.run_job("j", settings, asyncio.Lock()))

    assert seen["url"] == "http://x/d.docx" and seen["platforms"] == ["noon"]
    assert seen["row"].title == "Axle - Eng"
    job = store.get("j")
    assert job.state("noon") == "posted"
    assert job.platforms["noon"]["url"] == "https://noon/r1"
    assert Ledger(settings.ledger_file).get("j").records == {"noon": [{"role": "r1"}]}


def test_run_job_writes_a_fetch_failure_to_the_claimed_platforms(tmp_path, monkeypatch):
    """A document that cannot be fetched must land as a readable failure on the
    platforms that were claimed - the caller polls, nothing is returned."""
    from app import direct
    from app.config import Settings
    from app.models import PipelineError

    settings = Settings(
        _env_file=None,
        session_dir=str(tmp_path / "sessions"),
        direct_jobs_path=str(tmp_path / "direct-jobs.json"),
    )
    store = JobStore(settings.direct_jobs_file)
    store.queue("j", title="T", document_url="http://x/gone.docx", fields={}, platforms=["noon"])

    async def fake_load(url, s):
        raise PipelineError("The document link answered 404. Fix the link and post again.")

    monkeypatch.setattr("app.pipeline.load_document", fake_load)
    asyncio.run(direct.run_job("j", settings, asyncio.Lock()))

    job = store.get("j")
    assert job.state("noon") == "failed"
    assert "404" in job.platforms["noon"]["detail"]
    assert "404" in (job.error or "")


def test_run_delete_touches_only_what_was_asked(tmp_path, monkeypatch):
    """"Take it off noon" must reach delete_records with only noon named and
    only_named set - the ledger also remembers Juicebox, which must stay up."""
    from app import direct
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        session_dir=str(tmp_path / "sessions"),
        ledger_path=str(tmp_path / "posted-rows.json"),
        direct_jobs_path=str(tmp_path / "direct-jobs.json"),
    )
    store = JobStore(settings.direct_jobs_file)
    _posted(store, "j", ["noon", "juicebox"])
    store.queue_delete("j", ["noon"])

    asked: dict = {}

    async def fake_delete_records(page_id, title, platforms, s, *, post_url=None,
                                  dry_run=False, only_named=False):
        from app.pipeline import DeleteReport

        asked.update(platforms=list(platforms), only_named=only_named)
        return DeleteReport(page_id=page_id, title=title,
                            results=[DeleteResult(platform="noon", outcome=Outcome.DELETED)])

    monkeypatch.setattr("app.pipeline.delete_records", fake_delete_records)
    asyncio.run(direct.run_delete("j", settings, asyncio.Lock()))

    assert asked == {"platforms": ["noon"], "only_named": True}
    job = store.get("j")
    assert job.state("noon") == "deleted"
    assert job.state("juicebox") == "posted"
