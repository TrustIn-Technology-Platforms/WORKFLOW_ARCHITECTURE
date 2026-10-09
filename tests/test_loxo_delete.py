"""Deleting a row's Loxo campaign - the policy, the calls and the read-back,
against a stand-in for Loxo's GraphQL. The calls themselves are the app's own
(docs/platforms/loxo.md, "Deleting a row")."""

from __future__ import annotations

import asyncio

import pytest

from app.models import PlatformError
from app.platforms.loxo_delete import (
    LoxoDeleteReport,
    campaign_id_from,
    delete_campaign,
    destroy_mutation,
    list_query,
    refusal,
)

URL = "https://app.loxo.co/agencies/28356/campaigns/706135"


def _campaign(cid: int = 706135, name: str = "ZZ TEST - delete me", prospects: int = 0, **extra) -> dict:
    return {
        "id": cid, "name": name, "paused": True, "stageCount": 3, "shared": True,
        "createdAt": "2026-10-09T14:45:54Z", "recipientAggs": {"idCount": prospects}, **extra,
    }


class _Session:
    """Answers the list query from `campaigns`; a destroy removes the campaign
    after `lag` more list reads, the way Loxo's "deleted shortly" job does."""

    agency_id = "28356"

    def __init__(self, campaigns: list[dict], lag: int = 0):
        self.campaigns = campaigns
        self.lag = lag
        self.queries: list[str] = []
        self.destroyed: list[int] = []
        self._pending: list[tuple[int, int]] = []  # (campaign id, reads left)

    async def graphql(self, query: str) -> dict:
        self.queries.append(query)
        if query.startswith("mutation"):
            cid = int(query.split("id: ")[1].split(")")[0])
            self.destroyed.append(cid)
            self._pending.append((cid, self.lag))
            return {"destroyCampaign": {"id": cid}}
        # A list read: settle any pending delete first.
        still = []
        for cid, left in self._pending:
            if left <= 0:
                self.campaigns = [c for c in self.campaigns if c["id"] != cid]
            else:
                still.append((cid, left - 1))
        self._pending = still
        return {"campaigns": {"totalResults": len(self.campaigns), "campaigns": list(self.campaigns)}}


def _run(record: dict, session: _Session, *, dry_run: bool = False) -> LoxoDeleteReport:
    return asyncio.run(delete_campaign(
        object(), record, agency_id="28356", dry_run=dry_run, session=session,
        settle_seconds=0.2, poll_seconds=0.05,
    ))


def test_the_campaign_id_comes_out_of_the_url_not_the_agency_number():
    assert campaign_id_from(URL) == "706135"
    assert campaign_id_from(URL + "/stages") == "706135"
    assert campaign_id_from("706135") == "706135"
    # The agency id is the first number in the path; it must never be taken.
    assert campaign_id_from("https://app.loxo.co/agencies/28356/people") is None
    assert campaign_id_from("") is None and campaign_id_from(None) is None


def test_the_calls_are_the_apps_own():
    assert destroy_mutation("706135") == "mutation { destroyCampaign(id: 706135) { id: _id } }"
    query = list_query("28356", page=2, search='ZZ "quoted"')
    assert "agencyId: 28356" in query and "page: 2" in query
    assert 'query: "ZZ \\"quoted\\""' in query  # a name with quotes stays valid GraphQL
    assert "recipientAggs { idCount }" in query
    with pytest.raises(ValueError):
        destroy_mutation("not-an-id")  # an id that is not a number never reaches Loxo


def test_only_a_campaign_the_run_created_is_deleted():
    created = {"campaign": URL, "campaign_created": "yes", "campaign_name": "ZZ TEST - delete me"}
    assert refusal(created, _campaign()) is None
    # Found by name and filled: may be a recruiter's own.
    assert "recruiter's own" in refusal({**created, "campaign_created": "no"}, _campaign())
    # A record from before the flag existed.
    assert "does not say" in refusal({"campaign": URL}, _campaign())
    # Renamed since, or an id that points elsewhere now.
    why = refusal(created, _campaign(name="Rowspace - Founding Infra Eng - NY-SF"))
    assert "Rowspace" in why and "ZZ TEST" in why
    # A record with no name recorded (2026-09-23 to 2026-10-09) still deletes
    # on the flag alone: the id came off the URL of the campaign the run made.
    assert refusal({"campaign": URL, "campaign_created": "yes"}, _campaign(name="whatever")) is None


def test_delete_sends_destroy_and_reads_the_campaign_back_until_gone():
    session = _Session([_campaign(), _campaign(1, "Decart - Senior Software Engineer, Inference - SF")], lag=2)
    report = _run({"campaign": URL, "campaign_created": "yes", "campaign_name": "ZZ TEST - delete me"}, session)
    assert session.destroyed == [706135]
    assert report.deleted and report.complete
    assert report.summary == "Loxo campaign 'ZZ TEST - delete me' deleted"
    # The other campaign is untouched.
    assert [c["id"] for c in session.campaigns] == [1]
    # Several list reads: the first found it, then it was polled until gone.
    assert sum(1 for q in session.queries if not q.startswith("mutation")) >= 3


def test_a_campaign_still_listed_after_the_wait_is_not_reported_deleted():
    """Loxo says "deleted shortly"; until the list agrees the row must not
    read Deleted. A later retry finds it gone and reports that."""
    session = _Session([_campaign()], lag=1000)
    report = _run({"campaign": URL, "campaign_created": "yes"}, session)
    assert session.destroyed == [706135]
    assert not report.deleted and not report.complete
    assert "still listed" in report.warnings[0]
    assert report.summary == "Loxo campaign 'ZZ TEST - delete me' NOT deleted"


def test_already_gone_is_done_and_said():
    session = _Session([])
    report = _run({"campaign": URL, "campaign_created": "yes"}, session)
    assert report.deleted and report.complete and not session.destroyed
    assert report.summary == "Loxo campaign 706135 already gone"
    assert "already gone" in report.warnings[0]


def test_a_refused_campaign_is_never_destroyed_and_keeps_the_row_from_reading_deleted():
    session = _Session([_campaign()])
    report = _run({"campaign": URL, "campaign_created": "no"}, session)
    assert not session.destroyed
    assert report.refused and not report.complete
    assert "left alone" in report.summary and "by hand" in report.warnings[0]


def test_a_dry_run_reads_the_name_back_and_sends_nothing():
    session = _Session([_campaign(prospects=4, paused=False)])
    report = _run({"campaign": URL, "campaign_created": "yes"}, session, dry_run=True)
    assert not session.destroyed
    assert report.summary == "dry run: Loxo campaign 'ZZ TEST - delete me' found (running, 4 prospect(s)); nothing changed"
    gone = _run({"campaign": URL, "campaign_created": "yes"}, _Session([]), dry_run=True)
    assert gone.summary == "dry run: Loxo campaign 706135 already gone"


def test_a_record_without_an_id_is_a_clear_error():
    with pytest.raises(PlatformError, match="no Loxo campaign id"):
        _run({"post_url": "https://app.loxo.co/agencies/28356/people"}, _Session([]))


def test_prospects_on_a_deleted_campaign_are_named_on_the_row():
    session = _Session([_campaign(prospects=12)])
    report = _run({"campaign": URL, "campaign_created": "yes"}, session)
    assert report.deleted
    assert report.summary == "Loxo campaign 'ZZ TEST - delete me' deleted (12 prospect(s) were on it)"


def test_the_adapter_records_what_its_delete_needs():
    from pathlib import Path

    from app.config import Settings
    from app.platforms.engine import RunReport
    from app.platforms.loxo import LoxoAdapter
    from app.platforms.recipe import Recipe

    recipe = Recipe(key="loxo", label="Loxo", kind="email_sequence", path=Path("loxo.yaml"))
    adapter = LoxoAdapter(recipe, settings=Settings(), dry_run=False)
    assert adapter.supports_delete
    report = RunReport(
        captures={"post_url": URL},
        records={"campaign": URL, "campaign_created": "yes", "campaign_name": "ZZ TEST - delete me"},
    )
    assert adapter._records(report) == {
        "post_url": URL, "campaign": URL, "campaign_created": "yes",
        "campaign_name": "ZZ TEST - delete me",
    }
