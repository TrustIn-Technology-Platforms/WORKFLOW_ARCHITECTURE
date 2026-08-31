"""Which record a row's criteria are written to.

The failure this guards against is not a crash: it is writing one client's
requirements onto another client's job or search, which nothing downstream would
flag. So the rule is that an uncertain target is skipped and reported, never
guessed, and these tests hold that line.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.models import NotionRow, ParsedDocument
from app.platforms.engine import RunReport
from app.platforms.juicebox import JuiceboxAdapter
from app.platforms.loxo import LoxoAdapter
from app.platforms.recipe import Recipe


def _row(**columns) -> NotionRow:
    props = {
        name: {"type": "rich_text", "rich_text": [{"type": "text", "plain_text": value}]}
        for name, value in columns.items()
    }
    return NotionRow(
        page_id="p", title="", document_url=None, status=None,
        platforms=[], raw_properties=props,
    )


def _loxo(**settings_kwargs) -> LoxoAdapter:
    recipe = Recipe(
        key="loxo", label="Loxo", kind="email_sequence", path=__import__("pathlib").Path("loxo.yaml"),
        defaults={"agency_id": "28356"},
    )
    return LoxoAdapter(recipe, settings=Settings(**settings_kwargs))


def _juicebox(**settings_kwargs) -> JuiceboxAdapter:
    recipe = Recipe(
        key="juicebox", label="Juicebox", kind="email_sequence",
        path=__import__("pathlib").Path("juicebox.yaml"),
    )
    return JuiceboxAdapter(recipe, settings=Settings(**settings_kwargs))


# ----------------------------------------------------------------------
# Loxo
# ----------------------------------------------------------------------


def test_the_row_column_pins_the_loxo_job():
    """A URL in the column settles it - no searching, no ambiguity."""
    adapter = _loxo()
    document = ParsedDocument(source_name="Abundant - Staff Platform Engineer - SF")
    report = RunReport()

    target = asyncio.run(
        adapter._criteria_target(
            None,  # never touched: the column short-circuits the search
            document,
            _row(**{"Loxo Job": "https://app.loxo.co/agencies/28356/jobs/3640874/overview"}),
            report,
        )
    )

    assert target == "3640874"
    assert report.warnings == []


def test_a_bare_job_id_in_the_column_works_too():
    adapter = _loxo()
    target = asyncio.run(
        adapter._criteria_target(
            None, ParsedDocument(source_name="X - Y - Z"), _row(**{"Loxo Job": "3640874"}), RunReport()
        )
    )
    assert target == "3640874"


def test_a_document_with_no_company_is_skipped_not_guessed():
    adapter = _loxo()
    report = RunReport()

    target = asyncio.run(
        adapter._criteria_target(None, ParsedDocument(source_name=""), None, report)
    )

    assert target is None
    assert any("Loxo Job" in w for w in report.warnings)


def test_an_ambiguous_company_match_is_refused(monkeypatch):
    """Two jobs for one client is normal; picking one at random is not."""
    adapter = _loxo()

    async def two_matches(page, company):
        return [{"id": "1", "title": "Platform Engineer"}, {"id": "2", "title": "SRE"}]

    monkeypatch.setattr(adapter, "_find_jobs", two_matches)
    report = RunReport()

    target = asyncio.run(
        adapter._criteria_target(None, ParsedDocument(source_name="Decagon - X - Y"), None, report)
    )

    assert target is None
    assert any("2 Loxo jobs match" in w for w in report.warnings)


def test_exactly_one_company_match_is_used(monkeypatch):
    adapter = _loxo()

    async def one_match(page, company):
        assert company == "Abundant"
        return [{"id": "3658508", "title": "Member of Technical Staff"}]

    monkeypatch.setattr(adapter, "_find_jobs", one_match)
    report = RunReport()

    target = asyncio.run(
        adapter._criteria_target(
            None, ParsedDocument(source_name="Abundant - Staff Platform Engineer - SF"), None, report
        )
    )

    assert target == "3658508"
    assert report.warnings == []


# ----------------------------------------------------------------------
# Juicebox
# ----------------------------------------------------------------------


def test_juicebox_criteria_are_skipped_without_a_search_url():
    """Nothing in a document says which Juicebox search a role belongs to."""
    adapter = _juicebox()
    report = RunReport()

    asyncio.run(adapter._set_criteria(None, ParsedDocument(source_name="A - B - C"), None, report))

    assert any("Juicebox Search" in w for w in report.warnings)


def test_juicebox_rejects_a_column_that_is_not_a_url():
    adapter = _juicebox()
    report = RunReport()

    asyncio.run(
        adapter._set_criteria(
            None,
            ParsedDocument(source_name="A - B - C"),
            _row(**{"Juicebox Search": "Cloud Infra Engineer"}),
            report,
        )
    )

    assert any("full URL" in w for w in report.warnings)
