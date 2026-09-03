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
        assert status == "Posting"
        return self.rows

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


def test_recover_dry_run_lists_but_changes_nothing():
    import asyncio

    from app.config import Settings
    from app.pipeline import recover_stuck_rows

    client = _StuckClient([_posting_row("old", 50)])
    stuck = asyncio.run(recover_stuck_rows(client, Settings(), older_than_minutes=45, dry_run=True))
    assert [r.page_id for r in stuck] == ["old"]
    assert client.failed == {}
