"""Deleting a row's Wellfound draft - the policy, the call and its variable
retry, and the read-back, against stand-ins for the job page and Wellfound's
GraphQL. The call itself is mapped from the app's bundle, not yet a screen
(docs/platforms/wellfound.md, "Deleting a row")."""

from __future__ import annotations

import asyncio

import pytest

from app.models import PlatformError
from app.platforms import wellfound_delete as wd
from app.platforms.wellfound_delete import (
    WellfoundDeleteReport,
    delete_job,
    destroy_listing,
    job_id_from,
    refusal,
    shape_variables,
    variable_complaint,
)

URL = "https://wellfound.com/recruit/jobs/4656911"


class _Session:
    """Answers DestroyJobListing: a variable error for every shape until the
    accepted one, Wellfound's wording, then `{data: ...}`."""

    def __init__(self, accept: str = "jobListingId", refuse: str | None = None):
        self.accept = accept
        self.refuse = refuse
        self.calls: list[tuple[str, dict]] = []

    async def graphql(self, name: str, variables: dict) -> dict:
        self.calls.append((name, variables))
        if self.refuse:
            return {"errors": [{"message": self.refuse}]}
        shape = ".".join(_keys(variables))
        if shape == self.accept:
            return {"data": {"destroyJobListing": {"id": "4656911"}}}
        top = self.accept.split(".")[0]
        return {"errors": [{"message": f"Variable ${top} of type DestroyJobListingInput! was provided invalid value"}]}


def _keys(variables: dict) -> list[str]:
    keys: list[str] = []
    value = variables
    while isinstance(value, dict):
        key = next(iter(value))
        keys.append(key)
        value = value[key]
    return keys


def _job(exists: bool = True, title: str = "ZZ TEST Founding Platform Engineer", published: bool = False) -> dict:
    return {"exists": exists, "status": 200 if exists else 404, "url": URL, "title": title,
            "published": published, "draft": not published}


def _run(record: dict, monkeypatch, *, pages: list[dict], session: _Session | None = None, dry_run: bool = False):
    reads = list(pages)

    async def fake_read(page, job_id):
        return reads.pop(0) if len(reads) > 1 else reads[0]

    async def fake_capture(page):
        return session or _Session()

    monkeypatch.setattr(wd, "read_job", fake_read)
    monkeypatch.setattr(wd, "capture_session", fake_capture)
    return asyncio.run(delete_job(object(), record, dry_run=dry_run))


def test_the_job_id_comes_out_of_the_recruiter_url():
    assert job_id_from(URL) == "4656911"
    assert job_id_from(URL + "/edit") == "4656911"
    assert job_id_from("4656911") == "4656911"
    assert job_id_from("https://wellfound.com/recruit/jobs-beta") is None
    assert job_id_from("") is None


def test_variables_are_shaped_as_wellfound_might_want_them():
    assert shape_variables(("jobListingId",), "1") == {"jobListingId": "1"}
    assert shape_variables(("input", "jobListingId"), "1") == {"input": {"jobListingId": "1"}}
    assert variable_complaint([{"message": "Variable $input of type X! was provided invalid value"}]) == "input"
    assert variable_complaint([{"message": "Variable $id is declared by DestroyJobListing but not used"}]) == "id"
    assert variable_complaint([{"message": "Job listing not found"}]) is None
    assert variable_complaint([{"message": "You are not authorized to perform this action"}]) is None


def test_destroy_retries_only_the_variable_name_and_keeps_the_id():
    session = _Session(accept="input.jobListingId")
    shape = asyncio.run(destroy_listing(session, "4656911"))
    assert shape == "input.jobListingId"
    assert [n for n, _ in session.calls] == ["DestroyJobListing"] * len(session.calls)
    # Every attempt carried the same id, only under a different key.
    for _, variables in session.calls:
        value = variables
        while isinstance(value, dict):
            value = next(iter(value.values()))
        assert value == "4656911"
    assert session.calls[-1][1] == {"input": {"jobListingId": "4656911"}}


def test_a_real_refusal_is_not_retried():
    session = _Session(refuse="You are not authorized to perform this action")
    with pytest.raises(PlatformError, match="refused DestroyJobListing"):
        asyncio.run(destroy_listing(session, "4656911"))
    assert len(session.calls) == 1


def test_a_published_listing_is_refused_and_a_renamed_one_too():
    record = {"job": URL, "job_title": "ZZ TEST Founding Platform Engineer"}
    assert refusal(record, _job()) is None
    assert "applicants" in refusal(record, _job(published=True))
    assert "titled" in refusal(record, _job(title="Senior Platform Engineer - Rowspace"))
    # A record with no title recorded deletes on the id alone.
    assert refusal({"job": URL}, _job(title="whatever")) is None


def test_delete_sends_destroy_and_reads_the_page_back(monkeypatch):
    session = _Session()
    report = _run({"job": URL, "job_title": "ZZ TEST Founding Platform Engineer"}, monkeypatch,
                  pages=[_job(), _job(exists=False)], session=session)
    assert session.calls and report.deleted and report.complete
    assert report.summary == "Wellfound job 'ZZ TEST Founding Platform Engineer' deleted"
    assert report.accepted_shape == "jobListingId"


def test_a_page_that_still_opens_is_not_reported_deleted(monkeypatch):
    report = _run({"job": URL}, monkeypatch, pages=[_job(), _job()], session=_Session())
    assert not report.deleted and not report.complete
    assert "still opens" in report.warnings[0]


def test_already_gone_published_and_dry_run_each_say_so(monkeypatch):
    gone = _run({"job": URL}, monkeypatch, pages=[_job(exists=False)])
    assert gone.deleted and gone.summary == "Wellfound job 4656911 already gone"

    session = _Session()
    published = _run({"job": URL}, monkeypatch, pages=[_job(published=True)], session=session)
    assert published.refused and not published.complete and not session.calls
    assert "left alone" in published.summary

    dry = _run({"job": URL}, monkeypatch, pages=[_job()], session=session, dry_run=True)
    assert not session.calls
    assert dry.summary == "dry run: Wellfound job 'ZZ TEST Founding Platform Engineer' found (draft); nothing changed"


def test_a_record_without_an_id_is_a_clear_error(monkeypatch):
    with pytest.raises(PlatformError, match="no Wellfound job id"):
        _run({"post_url": "https://wellfound.com/recruit/jobs-beta"}, monkeypatch, pages=[_job()])


def test_the_shipped_recipe_resolves_to_the_driver():
    from app.config import Settings
    from app.platforms import get_adapter
    from app.platforms.wellfound import WellfoundAdapter

    adapter = get_adapter("wellfound", settings=Settings(), dry_run=True)
    assert isinstance(adapter, WellfoundAdapter) and adapter.supports_delete
    # The recipe keeps its steps: the driver runs them for the post.
    assert adapter.recipe.all_steps and any(s.submit for s in adapter.recipe.all_steps)


def test_the_report_reads_as_the_row_will():
    assert WellfoundDeleteReport(job_id="1", refused="x").summary == "Wellfound job 1 left alone"
    assert WellfoundDeleteReport(job_id="1", found=True).summary == "Wellfound job 1 NOT deleted"
