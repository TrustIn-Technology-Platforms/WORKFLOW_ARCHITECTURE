"""Updating a row's Wellfound listing (D-031, 2026-11-05): which listing,
which steps, and what is refused - against the shipped recipe and stand-ins
for the job page. The edit form itself has not been seen
(docs/platforms/wellfound.md, "Updating a row")."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.ledger import Ledger
from app.models import Advert, NotionRow, ParsedDocument, PlatformError
from app.platforms import wellfound as wf
from app.platforms.recipe import load_recipe

WELLFOUND_YAML = Path(__file__).resolve().parents[1] / "platforms" / "wellfound.yaml"


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, ledger_path=str(tmp_path / "posted-rows.json"))


def _row(**props) -> NotionRow:
    raw = {name: {"type": "url", "url": value} for name, value in props.items()}
    return NotionRow(page_id="page-1", title="ZZ TEST", document_url="x", status="Posted",
                     platforms=["wellfound"], raw_properties=raw)


# -- which listing ---------------------------------------------------------


def test_the_ledger_names_the_listing_first(tmp_path):
    settings = _settings(tmp_path)
    Ledger(settings.ledger_file).record_post("page-1", "ZZ TEST", {
        "wellfound": {"job": "https://wellfound.com/recruit/jobs/111", "job_title": "old"},
    })
    Ledger(settings.ledger_file).record_post("page-1", "ZZ TEST", {
        "wellfound": {"job": "https://wellfound.com/recruit/jobs/222", "job_title": "new"},
    })
    row = _row(**{"Post URL": "https://wellfound.com/recruit/jobs/333"})
    assert wf.find_listing(row, settings) == ("222", "the listing this row's post made")


def test_the_post_url_column_is_the_fallback_and_nothing_is_guessed(tmp_path):
    settings = _settings(tmp_path)
    row = _row(**{"Post URL": "https://jb/x | https://wellfound.com/recruit/jobs/333"})
    assert wf.find_listing(row, settings) == ("333", "the Post URL column")
    assert wf.find_listing(_row(**{"Post URL": "https://juicebox/only"}), settings) is None
    assert wf.find_listing(None, settings) is None


def test_a_listing_a_delete_cleared_is_not_updated(tmp_path):
    settings = _settings(tmp_path)
    ledger = Ledger(settings.ledger_file)
    ledger.record_post("page-1", "ZZ TEST", {"wellfound": {"job": "https://wellfound.com/recruit/jobs/111"}})
    ledger.record_delete("page-1", {"wellfound": (True, "deleted")})
    assert wf.find_listing(_row(), settings) is None


# -- which steps -----------------------------------------------------------


def test_the_update_runs_the_posting_steps_against_the_edit_form():
    recipe = load_recipe(WELLFOUND_YAML)
    steps = wf.update_steps(recipe.steps, "4656911")

    first = steps[0]
    assert first.action == "goto_until"
    assert first.params["url"] == "{{ base_url }}/recruit/jobs/4656911/edit"

    actions = [s.action for s in steps]
    assert "tags" not in actions, "skills would be added to, not replaced"
    selectors = " ".join(str(s.params.get("selector")) for s in steps)
    assert "San Francisco" not in selectors, "a second location tag would be added"
    assert "allowInternationalApplicants" not in selectors
    assert "remoteConfigKind" not in selectors
    for wanted in ("#form-input--title", ".CodeMirror", "#form-input--salaryMin", "yearsExperienceMin"):
        assert wanted in selectors

    submit = next(s for s in steps if s.submit)
    assert "button:has-text('Save draft')" in submit.params["selector"]
    assert "update_selector" not in submit.params
    assert not any("update_selector" in s.params for s in steps)


def test_the_posting_steps_are_not_changed_by_building_the_update():
    recipe = load_recipe(WELLFOUND_YAML)
    before = [(s.action, dict(s.params)) for s in recipe.steps]
    wf.update_steps(recipe.steps, "1")
    assert [(s.action, dict(s.params)) for s in recipe.steps] == before


# -- the driver ------------------------------------------------------------


def _document() -> ParsedDocument:
    document = ParsedDocument(advert=None, emails=[])
    document.platform_adverts["wellfound"] = Advert(
        title="Platform Engineer / Seed / NY / up to $250K + equity",
        body_text="Opening line.", body_html="<p>Opening line.</p>",
    )
    return document


class _Engine:
    """Stands in for RecipeEngine: records the steps it was given."""

    ran: list = []

    def __init__(self, recipe, page, settings, dry_run=None):
        from app.platforms.engine import RunReport
        self.report = RunReport()

    async def run_steps(self, steps, context):
        _Engine.ran = list(steps)
        self.report.executed = len(steps)
        return self.report


def _adapter(monkeypatch, tmp_path, job, dry_run=False):
    settings = _settings(tmp_path)
    Ledger(settings.ledger_file).record_post("page-1", "ZZ TEST", {
        "wellfound": {"job": "https://wellfound.com/recruit/jobs/4656911"},
    })

    async def _read_job(page, job_id):
        return {"exists": True, "status": 200, "url": "", "title": "old title",
                "published": False, "draft": True, **job}

    monkeypatch.setattr("app.platforms.wellfound_delete.read_job", _read_job)
    monkeypatch.setattr(wf, "RecipeEngine", _Engine)
    _Engine.ran = []
    return wf.WellfoundAdapter(load_recipe(WELLFOUND_YAML), settings=settings, dry_run=dry_run)


def test_a_draft_is_rewritten_and_the_result_names_the_listing_without_a_new_record(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch, tmp_path, {})
    report = asyncio.run(adapter._update(object(), _document(), _row()))
    assert _Engine.ran and _Engine.ran[0].params["url"].endswith("/recruit/jobs/4656911/edit")
    assert report.submitted
    assert report.sourcing_url == "https://wellfound.com/recruit/jobs/4656911"
    assert report.post_url is None, "a post URL would be recorded as a second listing to delete"
    assert adapter._records(report) == {}
    assert any("the listing this row's post made" in w for w in report.warnings)


def test_a_published_listing_is_left_alone(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch, tmp_path, {"published": True, "draft": False})
    with pytest.raises(PlatformError, match="published since"):
        asyncio.run(adapter._update(object(), _document(), _row()))
    assert _Engine.ran == []


def test_a_listing_that_is_gone_says_to_post_again(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch, tmp_path, {"exists": False})
    with pytest.raises(PlatformError, match="Post the row again"):
        asyncio.run(adapter._update(object(), _document(), _row()))


def test_a_row_with_no_listing_says_where_to_put_the_link(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    adapter = wf.WellfoundAdapter(load_recipe(WELLFOUND_YAML), settings=settings, dry_run=False)
    with pytest.raises(PlatformError, match="Post URL"):
        asyncio.run(adapter._update(object(), _document(), _row()))
