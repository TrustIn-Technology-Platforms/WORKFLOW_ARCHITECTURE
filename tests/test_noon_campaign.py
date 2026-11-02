"""noon campaign, offline. The template below is the shape noon generates for a
fresh role, read live on 2026-11-03 (six emails, no InMail, sent as the
signed-in user)."""

from __future__ import annotations

import asyncio
import copy

import pytest

from app.models import EmailStep, ParsedDocument, PlatformError
from app.platforms import noon_campaign
from app.platforms.engine import build_context
from app.platforms.noon_campaign import (
    campaign_id,
    differences,
    house_campaign,
    pick_template,
    write_campaign,
)

MARCUS = "marcus@trust-in.co.uk"
RECIPIENT = "Recipient's Timezone"


def noon_generated(role: str = "role-1") -> dict:
    later = {"time": "random time", "timezone": RECIPIENT}
    emails = {
        f"outbound{i}": {"type": "primary", "offset": off, "text": f"<p>noon {i}</p>",
                         "subject": "noon subject", "schedule": dict(later), "signature": False}
        for i, off in [(1, 0), (2, 2), (3, 4), (4, 5), (5, 5)]
    }
    return {
        "name": "ZZ TEST - Role (1)",
        "role": role,
        "obsolete": None,
        "ai_written": True,
        "creator": None,
        "tags": [],
        "edit_permission": "anyone",
        "subject": "{first_name}, let's connect!",
        "senders": {"from": "sohaib@trust-in.co.uk", "cc": [], "bcc": []},
        "secondary_senders": {"from": "", "cc": [], "bcc": []},
        "inmailSender": {"name": "Sohaib  Ali", "email": "sohaib@trust-in.co.uk"},
        "messages": {
            "outbound0": {"type": "connection", "offset": 0, "text": "", "schedule": {"time": "random time"}},
            **emails,
        },
        "triggers": {"connectionAccepted": {
            "enabled": True, "waitMinutes": 30, "message": "<p>noon</p>", "useFirstMessage": True,
            "followups": [{"id": "x", "waitMinutes": 2880, "message": "<p>bump</p>"}]}},
    }


def document(emails: int = 3) -> ParsedDocument:
    steps = [
        EmailStep(order=1, subject="", body_text="Hi {{name}}, open to a chat?",
                  body_html="<p>Hi {{name}}, open to a chat?</p>", channel="linkedin"),
        *[
            EmailStep(order=i + 2, subject="Founding Engineer / SF" if i == 0 else "",
                      body_text=f"Email {i + 1}", body_html=f"<p>Hi {{{{name}}}}, email {i + 1}</p>")
            for i in range(emails)
        ],
        EmailStep(order=9, subject="InMail subject", body_text="InMail",
                  body_html="<p>InMail at {{job_company}}</p>", channel="inmail"),
    ]
    return ParsedDocument(source_name="ZZ TEST - Role", advert=None, emails=steps)


def apply(template: dict | None = None, doc: ParsedDocument | None = None) -> dict:
    return house_campaign(
        template or noon_generated(), build_context(doc or document()),
        sender_email=MARCUS, sender_name="Marcus Gardiner-hill",
    )


def test_five_steps_in_the_team_order_replace_noons():
    m = apply()["messages"]
    assert sorted(m) == [f"outbound{i}" for i in range(5)]
    assert [m[f"outbound{i}"]["type"] for i in range(5)] == [
        "connection", "primary", "primary", "inmail", "primary"]


def test_the_documents_words_with_noon_tokens():
    m = apply()["messages"]
    assert m["outbound0"]["text"] == "<p>Hi {first_name}, open to a chat?</p>"
    assert m["outbound1"]["text"] == "<p>Hi {first_name}, email 1</p>"
    assert m["outbound2"]["text"] == "<p>Hi {first_name}, email 2</p>"
    assert m["outbound3"]["text"] == "<p>InMail at {company}</p>"
    assert m["outbound4"]["text"] == "<p>Hi {first_name}, email 3</p>"
    assert m["outbound3"]["subject"] == "InMail subject"
    # the follow-ups reply in the first email's thread
    assert {m[k]["subject"] for k in ("outbound1", "outbound2", "outbound4")} == {"Founding Engineer / SF"}
    assert m["outbound2"]["newThread"] is False and "newThread" not in m["outbound1"]


def test_the_connection_note_is_capped_at_noons_limit():
    doc = document()
    doc.emails[0].body_text = "x" * 400
    text = apply(doc=doc)["messages"]["outbound0"]["text"]
    assert text == "<p>" + "x" * 300 + "</p>"


def test_the_team_cadence():
    m = apply()["messages"]
    assert (m["outbound0"]["offset"], m["outbound0"]["schedule"]["day"]) == (0, "now")
    assert m["outbound1"]["offset"] == 0
    assert m["outbound1"]["schedule"] == {"day": "now", "time": "the same time", "timezone": RECIPIENT}
    for key in ("outbound2", "outbound3", "outbound4"):
        assert m[key]["offset"] == 2
        assert m[key]["schedule"] == {"time": "random time", "timezone": RECIPIENT}


def test_marcus_sends_with_his_signature_on_the_emails():
    out = apply()
    assert out["senders"]["from"] == MARCUS
    assert out["secondary_senders"]["from"] == ""
    assert out["inmailSender"] == {"name": "Marcus Gardiner-hill", "email": MARCUS}
    assert [out["messages"][f"outbound{i}"]["signature"] for i in range(5)] == [
        False, True, True, False, True]
    assert out["ai_written"] is False


def test_accepted_trigger_sends_the_note_after_30_minutes():
    accepted = apply()["triggers"]["connectionAccepted"]
    assert accepted == {
        "enabled": True, "waitMinutes": 30, "acceptanceWindowDays": 60,
        "message": "<p>Hi {first_name}, open to a chat?</p>",
        "useFirstMessage": False, "followups": [],
    }


def test_the_input_is_untouched():
    template = noon_generated()
    before = copy.deepcopy(template)
    apply(template)
    assert template == before


def test_fewer_than_three_emails_is_refused():
    with pytest.raises(PlatformError, match="has 2 emails"):
        apply(doc=document(emails=2))


def test_differences_names_what_did_not_save():
    wanted = apply()
    assert differences(wanted, wanted) == []
    saved = copy.deepcopy(wanted)
    saved["senders"]["from"] = "sohaib@trust-in.co.uk"
    saved["messages"]["outbound5"] = {"type": "primary"}
    assert differences(saved, wanted) == [
        "sender is 'sohaib@trust-in.co.uk'",
        "steps are ['outbound0', 'outbound1', 'outbound2', 'outbound3', 'outbound4', 'outbound5']",
    ]


def test_the_campaign_is_found_the_way_the_portal_finds_it():
    assert campaign_id({"autopilot": {"emailCampaign": {"id": "c1"}}, "template": "t1"}) == "c1"
    assert campaign_id({"autopilot": {}, "template": "t1"}) == "t1"
    assert campaign_id({}) is None
    templates = {"c1": noon_generated("other"), "t2": noon_generated()}
    assert pick_template(templates, {"autopilot": {"emailCampaign": {"id": "c1"}}}, "role-1")[0] == "c1"
    assert pick_template(templates, {}, "role-1")[0] == "t2"
    assert pick_template(templates, {}, "role-9") is None


class _Session:
    token = "tok"
    company = "co"
    email = "me@x"

    def __init__(self, *, keeps: bool = True, signature: str | None = "<p>Marcus</p>", late: int = 0):
        self.templates = {"tid": noon_generated()}
        self.keeps = keeps
        self.signature = signature
        self.late = late  # how many reads before noon has written its campaign
        self.calls: list[tuple[str, dict]] = []

    async def post(self, path, payload, *, forbidden_ok=False):
        self.calls.append((path, payload))
        if path == "templates":
            if self.late:
                self.late -= 1
                return {}
            return copy.deepcopy(self.templates)
        if path == "template_update":
            if self.keeps:
                self.templates[payload["tid"]] = payload["templates"]
            return True
        if path == "get_email_signature":
            return {"email_signature": self.signature}
        raise AssertionError(path)


@pytest.fixture(autouse=True)
def _role(monkeypatch):
    async def fake_fetch_role(session, role_id, **kwargs):
        return {"id": role_id, "autopilot": {"emailCampaign": {"id": "tid"}}}

    monkeypatch.setattr(noon_campaign, "fetch_role", fake_fetch_role)
    monkeypatch.setattr(noon_campaign, "CAMPAIGN_POLL_SECONDS", 0)


def run(session: _Session, wait_seconds: float = 0.0) -> list[str]:
    return asyncio.run(write_campaign(
        session, "role-1", build_context(document()),
        sender_email=MARCUS, sender_name="Marcus Gardiner-hill", wait_seconds=wait_seconds,
    ))


def test_writes_once_as_the_editor_does_and_reads_back():
    session = _Session()
    assert run(session) == []
    update = next(p for path, p in session.calls if path == "template_update")
    assert (update["tid"], update["id"], update["company"], update["save_role"]) == ("tid", "co", "co", True)
    assert session.templates["tid"]["senders"]["from"] == MARCUS


def test_waits_for_noon_to_write_its_campaign():
    session = _Session(late=2)
    assert run(session, wait_seconds=60) == []


def test_no_campaign_at_all_fails_with_a_reason():
    with pytest.raises(PlatformError, match="had not written a campaign"):
        run(_Session(late=99), wait_seconds=0)


def test_a_write_noon_did_not_keep_fails_the_post():
    with pytest.raises(PlatformError, match="did not keep"):
        run(_Session(keeps=False))


def test_missing_signature_is_a_warning():
    warnings = run(_Session(signature=None))
    assert len(warnings) == 1 and "no email signature" in warnings[0]
