"""Orchestrator-level behaviour that no single layer owns."""

from __future__ import annotations

from app.models import Advert, NotionRow, ParsedDocument
from app.pipeline import enrich_advert


class _Settings:
    prop_location = "Location"
    prop_salary = "Salary"
    prop_employment_type = "Employment Type"
    prop_skills = "Skills"


def _rich(text: str) -> dict:
    return {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": text}]}


def _row(**props: dict) -> NotionRow:
    return NotionRow(
        page_id="p", title="t", document_url=None, status=None, raw_properties=props
    )


def test_row_columns_fill_only_the_gaps():
    """The document keeps what it has; the row supplies what it lacks.

    Recruiters' adverts are prose with no Location/Salary lines, and Wellfound
    requires both - so they live on the Notion row. A value the document does
    carry must not be overwritten by the column.
    """
    doc = ParsedDocument(advert=Advert(title="x", body_text="", body_html="", location="London"))
    row = _row(**{"Location": _rich("San Francisco"), "Salary": _rich("$180k-$220k")})

    filled = enrich_advert(doc, row, _Settings())

    assert doc.advert.location == "London"
    assert doc.advert.salary == "$180k-$220k"
    assert filled == ["salary <- Salary"]


def test_column_name_matches_loosely_like_the_notion_client():
    """`employment_type`, `Employment type` and `Employment Type` are one column."""
    doc = ParsedDocument(advert=Advert(title="x", body_text="", body_html=""))
    row = _row(**{"employment_type": _rich("Permanent")})

    enrich_advert(doc, row, _Settings())

    assert doc.advert.employment_type == "Permanent"


def test_no_row_or_no_advert_is_a_no_op():
    doc = ParsedDocument(advert=Advert(title="x", body_text="", body_html=""))
    assert enrich_advert(doc, None, _Settings()) == []
    assert enrich_advert(ParsedDocument(), _row(Location=_rich("SF")), _Settings()) == []


def test_blank_and_select_columns():
    """An empty column fills nothing; a select column reads its option name."""
    doc = ParsedDocument(advert=Advert(title="x", body_text="", body_html=""))
    row = _row(**{
        "Location": _rich("   "),
        "Employment Type": {"type": "select", "select": {"name": "Contract"}},
    })

    filled = enrich_advert(doc, row, _Settings())

    assert doc.advert.location is None
    assert doc.advert.employment_type == "Contract"
    assert filled == ["employment_type <- Employment Type"]


def test_skills_column_becomes_the_advert_tag_list():
    """One column, many tags. Wellfound's Skills field takes them one at a time,
    so the split happens here rather than in the recipe."""
    doc = ParsedDocument(advert=Advert(title="x", body_text="", body_html=""))
    row = _row(**{"Skills": _rich("Python, Kubernetes; CI/CD")})

    filled = enrich_advert(doc, row, _Settings())

    assert doc.advert.tags == ["Python", "Kubernetes", "CI/CD"]
    assert "tags <- Skills" in filled


def test_skills_already_on_the_advert_are_not_replaced_by_the_column():
    doc = ParsedDocument(
        advert=Advert(title="x", body_text="", body_html="", tags=["Go"])
    )
    row = _row(**{"Skills": _rich("Python")})

    filled = enrich_advert(doc, row, _Settings())

    assert doc.advert.tags == ["Go"]
    assert filled == []


def test_row_columns_reach_a_board_advert_too():
    """A board advert is built at parse time, before the row exists, so its
    inherited fields are already frozen. Enrichment must fill it directly:
    the first live row with a `Wellfound` section failed for want of a
    location its own column plainly held (2026-09-01)."""
    from app.documents.parser import parse_document
    from app.models import Block

    def block(text: str, style: str = "body", level: int = 0) -> Block:
        return Block(style=style, level=level, text=text, html=f"<p>{text}</p>")

    doc = parse_document(
        [
            block("Platform Engineer", style="heading", level=1),
            block("General advert."),
            block("Wellfound", style="heading", level=2),
            block("Board copy."),
        ]
    )
    row = _row(**{"Location": _rich("NY"), "Skills": _rich("Python, Go")})

    enrich_advert(doc, row, _Settings())

    wellfound = doc.advert_for("wellfound")
    assert wellfound is not doc.advert
    assert wellfound.location == "NY", "the recipe reads THIS advert"
    assert wellfound.tags == ["Python", "Go"]
    assert doc.advert.location == "NY"


def test_a_multi_select_skills_column_reads_as_a_list():
    """The column type a recruiter would actually pick for skills.

    `Skills` is a natural multi-select, and `plain_text_of` flattens one to
    "A, B, C" - which `split_skills` then splits on the comma. Worth pinning:
    the list feeds Wellfound's Skills field and noon's targeting preamble, and a
    silently-empty read would leave both looking like the recruiter named
    nothing.
    """
    doc = ParsedDocument(advert=Advert(title="t", body_text="b", body_html="<p>b</p>"))
    row = _row(
        Skills={
            "type": "multi_select",
            "multi_select": [
                {"name": "Kubernetes"}, {"name": "Terraform"}, {"name": "Go"},
            ],
        }
    )
    filled = enrich_advert(doc, row, _Settings())

    assert doc.advert.tags == ["Kubernetes", "Terraform", "Go"]
    assert "tags <- Skills" in filled


# -- rows a dead process left on Posting ----------------------------------------


def _posting_row(page_id: str, minutes_ago: int | None) -> NotionRow:
    from datetime import datetime, timedelta, timezone

    edited = None if minutes_ago is None else datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return NotionRow(page_id=page_id, title=page_id.title(), document_url=None,
                     status="Posting", last_edited=edited)


class _StuckClient:
    def __init__(self, rows):
        self.rows = rows
        self.failed: dict[str, str] = {}

    async def query_rows_by_status(self, status, limit=None):
        assert status in ("Posting", "Deleting")
        return [r for r in self.rows if r.status == status]

    async def mark_failed(self, page_id, error):
        self.failed[page_id] = error


def test_only_rows_stale_past_the_threshold_are_released():
    """The Axle row of 2026-09-03: claimed, then the container was replaced.
    A fresh claim may still be a live run; a row with no stamp is left alone."""
    import asyncio

    from app.config import Settings
    from app.pipeline import recover_stuck_rows

    client = _StuckClient([_posting_row("old", 50), _posting_row("fresh", 5), _posting_row("unknown", None)])
    stuck = asyncio.run(recover_stuck_rows(client, Settings(), older_than_minutes=45))
    assert [r.page_id for r in stuck] == ["old"]
    assert set(client.failed) == {"old"}
    assert "restarted" in client.failed["old"] and "Ready to Post" in client.failed["old"]


def test_a_row_left_on_deleting_is_released_with_the_delete_note():
    """A restart mid-delete leaves the row on Deleting; the note says the
    second attempt finishes the job rather than 'check for a saved sequence'."""
    import asyncio

    from app.config import Settings
    from app.pipeline import recover_stuck_rows

    row = _posting_row("gone", 50)
    row.status = "Deleting"
    client = _StuckClient([row])
    asyncio.run(recover_stuck_rows(client, Settings(), older_than_minutes=45))
    assert "set the status back to Delete" in client.failed["gone"]


def test_recover_dry_run_lists_but_changes_nothing():
    import asyncio

    from app.config import Settings
    from app.pipeline import recover_stuck_rows

    client = _StuckClient([_posting_row("old", 50)])
    stuck = asyncio.run(recover_stuck_rows(client, Settings(), older_than_minutes=45, dry_run=True))
    assert [r.page_id for r in stuck] == ["old"]
    assert client.failed == {}


# -- the last write of a row ----------------------------------------------------


class _WriteBackClient:
    """A Notion client whose final update is rejected, as a 409 would be."""

    def __init__(self, fail_posted: bool = True) -> None:
        self.fail_posted = fail_posted
        self.posting: list[str] = []
        self.posted: list[tuple] = []
        self.failed: list[tuple] = []

    async def mark_posting(self, page_id):
        self.posting.append(page_id)

    async def mark_posted(self, page_id, post_url, detail=None):
        if self.fail_posted:
            from app.notion.client import NotionAPIError

            raise NotionAPIError(409, "conflict_error", "Conflict occurred while saving")
        self.posted.append((page_id, post_url, detail))

    async def mark_failed(self, page_id, error):
        self.failed.append((page_id, error))


def _posted_report(monkeypatch, results):
    """Run process_row far enough to reach the write-back, with no browsers."""
    import asyncio

    from app.config import Settings
    from app.models import NotionRow
    from app.pipeline import process_row

    document = ParsedDocument(
        advert=Advert(title="T", body_text="b", body_html="<p>b</p>"), emails=[]
    )

    async def fake_load(url, settings=None):
        return document

    async def fake_post(doc, platforms, row=None, settings=None, dry_run=None):
        return results

    monkeypatch.setattr("app.pipeline.load_document", fake_load)
    monkeypatch.setattr("app.pipeline.post_document", fake_post)
    row = NotionRow(page_id="p1", title="Row", document_url="http://x/doc.docx",
                    status="Ready to Post", platforms=["loxo"])
    return row, document, asyncio, process_row, Settings


def test_a_rejected_final_update_is_recorded_instead_of_left_on_posting(monkeypatch):
    """Review, 2026-09-03: every platform posted, Notion rejected the last
    PATCH, the row stayed on Posting, and 45 minutes later the sweep blamed a
    restart and invited a re-run - which would post everything twice."""
    from app.models import Outcome, PostResult

    row, _doc, asyncio, process_row, Settings = _posted_report(
        monkeypatch,
        [PostResult(platform="loxo", outcome=Outcome.POSTED,
                    post_url="https://app.loxo.co/x", detail=None)],
    )
    client = _WriteBackClient()
    report = asyncio.run(process_row(row, client, Settings(), dry_run=False))

    assert report.ok  # the posting itself succeeded
    assert client.failed, "the failed write-back was not recorded on the row"
    _page, message = client.failed[0]
    assert "Do NOT re-run" in message
    assert "https://app.loxo.co/x" in message


def test_a_row_whose_platform_has_no_recipe_says_nothing_in_its_notes(monkeypatch):
    """`TrustIn` is a tag, not a destination. Reporting its skip on every row
    read as a problem - and with no Notes column, as 'Posted OK' in Error."""
    from app.models import Outcome, PostResult

    row, _doc, asyncio, process_row, Settings = _posted_report(
        monkeypatch,
        [
            PostResult(platform="TrustIn", outcome=Outcome.SKIPPED,
                       detail="no recipe for 'TrustIn' - nothing to post to"),
            PostResult(platform="loxo", outcome=Outcome.POSTED,
                       post_url="https://app.loxo.co/x", detail=None),
        ],
    )
    client = _WriteBackClient(fail_posted=False)
    asyncio.run(process_row(row, client, Settings(), dry_run=False))

    assert client.posted, "the row was not marked posted"
    _page, _url, detail = client.posted[0]
    assert detail is None, f"nothing should have been written as notes, got {detail!r}"


def test_a_real_platforms_note_still_reaches_the_row(monkeypatch):
    from app.models import Outcome, PostResult

    row, _doc, asyncio, process_row, Settings = _posted_report(
        monkeypatch,
        [PostResult(platform="juicebox", outcome=Outcome.POSTED,
                    post_url="https://app.juicebox.ai/x",
                    detail="sourcing search: 26 companies, 4 refused")],
    )
    client = _WriteBackClient(fail_posted=False)
    asyncio.run(process_row(row, client, Settings(), dry_run=False))

    _page, _url, detail = client.posted[0]
    assert "26 companies" in detail


# -- deleting a row -------------------------------------------------------------


class _FakeDeleteAdapter:
    """Stands in for a platform: records what it was asked, answers as told."""

    calls: list[tuple[str, list]] = []

    def __init__(self, name, outcome):
        self.name, self.outcome = name, outcome

    async def delete(self, records, *, row_title=""):
        from app.models import DeleteResult

        _FakeDeleteAdapter.calls.append((self.name, records))
        return DeleteResult(platform=self.name, outcome=self.outcome, detail=f"{self.name} done")


class _NoBrowser:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _delete_setup(monkeypatch, tmp_path, outcomes):
    from app.config import Settings

    _FakeDeleteAdapter.calls = []
    settings = Settings(ledger_path=str(tmp_path / "posted-rows.json"))
    recipes = {"noon": object(), "juicebox": object(), "loxo": object(), "wellfound": object()}
    monkeypatch.setattr("app.pipeline.load_recipes", lambda s: recipes)
    monkeypatch.setattr("app.pipeline.resolve", lambda name, r: r.get(name.lower()))
    monkeypatch.setattr("app.pipeline.BrowserRunner", _NoBrowser)
    monkeypatch.setattr(
        "app.pipeline.get_adapter",
        lambda name, **k: _FakeDeleteAdapter(name, outcomes.get(name)),
    )
    return settings


class _DeleteClient:
    def __init__(self, trash=None):
        self.trash = trash or {}
        self.deleting, self.deleted, self.failed = [], [], []

    async def mark_deleting(self, page_id):
        self.deleting.append(page_id)

    async def mark_deleted(self, page_id, detail=None):
        self.deleted.append((page_id, detail))

    async def mark_failed(self, page_id, error):
        self.failed.append((page_id, error))

    async def page_in_trash(self, page_id):
        return self.trash.get(page_id)


def test_a_posted_row_records_what_each_platform_created(monkeypatch, tmp_path):
    """Notion's Post URL keeps one link; the ledger keeps every platform's ids."""
    from app.ledger import Ledger
    from app.models import Outcome, PostResult

    row, _doc, asyncio, process_row, Settings = _posted_report(
        monkeypatch,
        [PostResult(platform="noon", outcome=Outcome.POSTED, post_url="https://noon/x",
                    records={"role": "r1", "post_url": "https://noon/x"}),
         PostResult(platform="juicebox", outcome=Outcome.POSTED, post_url="https://jb/x",
                    records={"sequence": "s1"})],
    )
    settings = Settings(ledger_path=str(tmp_path / "posted-rows.json"))
    asyncio.run(process_row(row, _WriteBackClient(fail_posted=False), settings, dry_run=False))

    entry = Ledger(settings.ledger_file).get("p1")
    assert entry.records == {
        "noon": [{"role": "r1", "post_url": "https://noon/x"}],
        "juicebox": [{"sequence": "s1"}],
    }


def test_a_delete_row_clears_every_platform_and_says_deleted(monkeypatch, tmp_path):
    import asyncio

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import delete_row

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DELETED, "juicebox": Outcome.DELETED})
    Ledger(settings.ledger_file).record_post("p1", "Row", {"noon": {"role": "r1"}, "juicebox": {"sequence": "s1"}})
    row = NotionRow(page_id="p1", title="Row", document_url=None, status="Delete",
                    platforms=["noon", "juicebox", "TrustIn"])
    client = _DeleteClient()

    report = asyncio.run(delete_row(row, client, settings, dry_run=False))

    assert report.ok and client.deleting == ["p1"] and client.deleted and not client.failed
    assert _FakeDeleteAdapter.calls == [("noon", [{"role": "r1"}]), ("juicebox", [{"sequence": "s1"}])]
    assert Ledger(settings.ledger_file).get("p1").done_at is not None


def test_a_half_deleted_row_fails_and_the_retry_skips_what_went(monkeypatch, tmp_path):
    """Setting the row back to Delete finishes the job; it does not visit noon
    again for a role that is already gone."""
    import asyncio

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import delete_row

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DELETED, "juicebox": Outcome.FAILED})
    Ledger(settings.ledger_file).record_post("p1", "Row", {"noon": {"role": "r1"}, "juicebox": {"sequence": "s1"}})
    row = NotionRow(page_id="p1", title="Row", document_url=None, status="Delete",
                    platforms=["noon", "juicebox"])
    client = _DeleteClient()

    asyncio.run(delete_row(row, client, settings, dry_run=False))
    assert client.failed and "juicebox" in client.failed[0][1] and "back to Delete" in client.failed[0][1]

    _FakeDeleteAdapter.calls = []
    asyncio.run(delete_row(row, client, settings, dry_run=False))
    assert [name for name, _ in _FakeDeleteAdapter.calls] == ["juicebox"]


def test_a_platform_whose_post_recorded_nothing_does_not_hold_the_row_back(monkeypatch, tmp_path):
    """A tracked row whose Loxo post failed made nothing recordable there;
    that must not leave the row Failed for ever."""
    import asyncio

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import delete_row

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DELETED})
    Ledger(settings.ledger_file).record_post("p1", "Row", {"noon": {"role": "r1"}})
    row = NotionRow(page_id="p1", title="Row", document_url=None, status="Delete",
                    platforms=["noon", "loxo"])
    client = _DeleteClient()
    report = asyncio.run(delete_row(row, client, settings, dry_run=False))

    assert report.ok and client.deleted
    assert [name for name, _ in _FakeDeleteAdapter.calls] == ["noon"]
    assert "recorded anything on loxo" in report.detail


def test_a_named_delete_leaves_the_other_platforms_alone(monkeypatch, tmp_path):
    """The direct door deletes per platform: "take it off noon" must not also
    take it off Juicebox, which the ledger remembers for the same job. Without
    only_named every recorded platform joins in (the Notion door's whole-row
    Delete)."""
    import asyncio

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import delete_records

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DELETED})
    Ledger(settings.ledger_file).record_post(
        "p1", "Row", {"noon": {"role": "r1"}, "juicebox": {"sequence": "s1"}}
    )

    report = asyncio.run(delete_records(
        "p1", "Row", ["noon"], settings, dry_run=False, only_named=True,
    ))

    assert report.ok
    assert [name for name, _ in _FakeDeleteAdapter.calls] == ["noon"]
    entry = Ledger(settings.ledger_file).get("p1")
    assert entry.open_platforms == ["juicebox"]  # still up, still deletable later


def test_a_row_posted_before_the_ledger_falls_back_to_its_one_link(monkeypatch, tmp_path):
    import asyncio

    from app.models import Outcome
    from app.pipeline import delete_row

    settings = _delete_setup(monkeypatch, tmp_path, {"juicebox": Outcome.DELETED, "noon": Outcome.FAILED})
    url = "https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW/sequences/2eWj7CrfhpTnY5QoqLXN"
    row = NotionRow(page_id="p9", title="Axl", document_url=None, status="Delete",
                    platforms=["juicebox", "noon"],
                    raw_properties={"Post URL": {"type": "url", "url": url}})
    asyncio.run(delete_row(row, _DeleteClient(), settings, dry_run=False))

    assert ("juicebox", [{"post_url": url}]) in _FakeDeleteAdapter.calls
    assert ("noon", []) in _FakeDeleteAdapter.calls  # nothing known: the adapter says so


def test_a_dry_run_delete_writes_nothing_back(monkeypatch, tmp_path):
    import asyncio

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import delete_row

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DRY_RUN})
    Ledger(settings.ledger_file).record_post("p1", "Row", {"noon": {"role": "r1"}})
    client = _DeleteClient()
    row = NotionRow(page_id="p1", title="Row", document_url=None, status="Delete", platforms=["noon"])
    asyncio.run(delete_row(row, client, settings, dry_run=True))

    assert not (client.deleting or client.deleted or client.failed)
    assert Ledger(settings.ledger_file).get("p1").is_open


def test_a_trashed_row_waits_out_the_grace_period_then_is_deleted(monkeypatch, tmp_path):
    """Trashing a row by mistake and restoring it inside the wait keeps
    everything; one left in the trash has its posts deleted."""
    import asyncio
    import json
    from datetime import datetime, timedelta, timezone

    from app.ledger import Ledger
    from app.models import Outcome
    from app.pipeline import sweep_trashed_rows

    settings = _delete_setup(monkeypatch, tmp_path, {"noon": Outcome.DELETED})
    settings.delete_trashed_after_hours = 24
    ledger = Ledger(settings.ledger_file)
    for page in ("trashed", "restored", "noaccess", "live"):
        ledger.record_post(page, page, {"noon": {"role": page}})
    client = _DeleteClient(trash={"trashed": True, "restored": True, "noaccess": None, "live": False})

    assert asyncio.run(sweep_trashed_rows(client, settings)) == []  # first sighting only
    assert _FakeDeleteAdapter.calls == []

    client.trash["restored"] = False
    asyncio.run(sweep_trashed_rows(client, settings))
    assert ledger.get("restored").trashed_seen_at is None

    # A day later: the first sighting was 25 hours ago.
    data = json.loads(settings.ledger_file.read_text(encoding="utf-8"))
    data["rows"]["trashed"]["trashed_seen_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat()
    settings.ledger_file.write_text(json.dumps(data), encoding="utf-8")

    reports = asyncio.run(sweep_trashed_rows(client, settings))
    assert [r.page_id for r in reports] == ["trashed"]
    assert _FakeDeleteAdapter.calls == [("noon", [{"role": "trashed"}])]
    assert not ledger.get("trashed").is_open and ledger.get("live").is_open
    assert ledger.get("noaccess").trashed_seen_at is None  # Notion would not say
