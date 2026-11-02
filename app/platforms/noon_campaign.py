"""noon campaign: the document's words, the team's sender, cadence and trigger.

Written through `template_update`, the call noon's own editor saves with, and
read back. The editor is no longer driven at all (2026-11-03): its 2026-11
redesign renamed `Use shared template` to `Use a template` and every posting
run died there, the third redesign to break it, and noon now writes a
campaign of its own ("Written by Noon") for every role once sourcing is set
up. So there is always a campaign to overwrite, and importing `Nicholas
Template` only to repaint it through a positional Draft.js editor bought
nothing but fragility.

What the team sends, as Sohaib set it out on 2026-11-03 and as Marcus's
hand-built campaigns store it:

    1  LinkedIn connection request   now
    2  email                         the same time as step 1
    3  email, same thread            2 days later, random time 9 AM-5 PM, recipient's time zone
    4  LinkedIn InMail               the same
    5  email, same thread            the same
       if accepted                   on, 30 minutes, not after 60 days

All of it from Marcus. noon would send as whoever is signed in - sohaib@,
whose noon mailbox has no signature, which is why no email ever carried one.
A step's `signature` is only a flag that appends the sender's saved signature,
and Marcus's mailbox holds his. `inmailSender` is the one LinkedIn sender
field noon has; it is the "From" on steps 1 and 4.
"""

from __future__ import annotations

import asyncio
import copy
import html
import re
from typing import Any

from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.noon_sourcing import NoonSession, fetch_role
from app.utils.templating import noon_tokens as _noon_tokens

log = get_logger(__name__)

RECIPIENT_TZ = "Recipient's Timezone"
SAME_TIME = {"day": "now", "time": "the same time", "timezone": RECIPIENT_TZ}
LATER = {"time": "random time", "timezone": RECIPIENT_TZ}
NOW = {"day": "now", "time": "random time", "timezone": RECIPIENT_TZ}
FOLLOW_UP_DAYS = 2
ACCEPTED_WAIT_MINUTES = 30
ACCEPTED_WINDOW_DAYS = 60
# noon's editor refuses a longer connection note.
CONNECTION_NOTE_LIMIT = 300
# noon writes its campaign after the wizard's last call; on 2026-11-03 it was
# there by the time the page reloaded, but a slow night should not fail a post.
CAMPAIGN_WAIT_SECONDS = 120.0
CAMPAIGN_POLL_SECONDS = 5.0


def _note_html(text: str) -> str:
    """The connection note as noon stores it: plain text, one <p> per paragraph."""
    note = _noon_tokens(text).strip()[:CONNECTION_NOTE_LIMIT]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", note) if p.strip()]
    return "".join(f"<p>{html.escape(p, quote=False)}</p>" for p in paragraphs)


def campaign_steps(context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The five steps and the accepted-message, from the recipe context.

    `context` is `engine.build_context`'s: `emails` holds the email-channel
    steps only, `inmail` and `connection_note` fall back to email 1 when the
    document has no such section.
    """
    emails = context.get("emails") or []
    if len(emails) < 3:
        raise PlatformError(
            f"noon's campaign has three email steps and the document has "
            f"{len(emails)} email{'' if len(emails) == 1 else 's'}. Add the missing "
            "emails to the document and re-run."
        )
    inmail = context["inmail"]
    note = context["connection_note"]
    subject = _noon_tokens(emails[0]["subject"])

    def email(step: dict[str, Any], offset: int, schedule: dict[str, str], *, first: bool) -> dict[str, Any]:
        message = {
            "type": "primary",
            "offset": offset,
            "schedule": dict(schedule),
            # Steps 3 and 5 reply in step 2's thread, so they carry its subject.
            "subject": subject,
            "text": _noon_tokens(step["body_html"]),
            "signature": True,
            "unsubscribeLink": False,
            "preview": "",
        }
        if not first:
            message["newThread"] = False
        return message

    steps = {
        "outbound0": {
            "type": "connection",
            "offset": 0,
            "schedule": dict(NOW),
            "text": _note_html(note["body_text"]),
            "signature": False,
            "unsubscribeLink": False,
        },
        "outbound1": email(emails[0], 0, SAME_TIME, first=True),
        "outbound2": email(emails[1], FOLLOW_UP_DAYS, LATER, first=False),
        "outbound3": {
            "type": "inmail",
            "offset": FOLLOW_UP_DAYS,
            "schedule": dict(LATER),
            "subject": _noon_tokens(inmail["subject"] or emails[0]["subject"]),
            "text": _noon_tokens(inmail["body_html"]),
            # An InMail cannot carry an email signature.
            "signature": False,
            "unsubscribeLink": False,
            "preview": "",
        },
        "outbound4": email(emails[2], FOLLOW_UP_DAYS, LATER, first=False),
    }
    return steps, _noon_tokens(note["body_html"])


def house_campaign(
    template: dict[str, Any],
    context: dict[str, Any],
    *,
    sender_email: str,
    sender_name: str,
) -> dict[str, Any]:
    """The campaign as the team sends it; the input is not changed.

    Every step is replaced, not edited: noon's own campaign has six emails and
    no InMail, and a step left over from it would go out after ours.
    """
    steps, accepted_message = campaign_steps(context)
    updated = copy.deepcopy(template)
    updated["messages"] = steps
    updated["senders"] = {**(updated.get("senders") or {}), "from": sender_email}
    # A second sender rotates the emails between the two inboxes.
    updated["secondary_senders"] = {**(updated.get("secondary_senders") or {}), "from": ""}
    updated["inmailSender"] = {"name": sender_name, "email": sender_email}
    # noon marks its generated copy as AI-written; the words are now ours.
    updated["ai_written"] = False
    triggers = dict(updated.get("triggers") or {})
    triggers["connectionAccepted"] = {
        "enabled": True,
        "waitMinutes": ACCEPTED_WAIT_MINUTES,
        "acceptanceWindowDays": ACCEPTED_WINDOW_DAYS,
        "message": accepted_message,
        # True sends step 2's email on acceptance instead of this message.
        "useFirstMessage": False,
        # noon's generated campaign adds two "just bumping this up" nudges;
        # the team's template has none.
        "followups": [],
    }
    updated["triggers"] = triggers
    return updated


def differences(saved: dict[str, Any], wanted: dict[str, Any]) -> list[str]:
    """What the read-back lacks of what was written."""
    problems: list[str] = []
    if (saved.get("senders") or {}).get("from") != wanted["senders"]["from"]:
        problems.append(f"sender is {(saved.get('senders') or {}).get('from')!r}")
    if (saved.get("inmailSender") or {}).get("email") != wanted["inmailSender"]["email"]:
        problems.append(f"LinkedIn sender is {(saved.get('inmailSender') or {}).get('email')!r}")
    got_steps = saved.get("messages") or {}
    if sorted(got_steps) != sorted(wanted["messages"]):
        problems.append(f"steps are {sorted(got_steps)}")
    for key, want in wanted["messages"].items():
        got = got_steps.get(key) or {}
        for field in ("type", "offset", "schedule", "signature", "subject"):
            if got.get(field) != want.get(field):
                problems.append(f"{key} {field} is {got.get(field)!r}")
        if bool(got.get("text")) != bool(want.get("text")):
            problems.append(f"{key} text is {'empty' if not got.get('text') else 'not empty'}")
    accepted = (saved.get("triggers") or {}).get("connectionAccepted") or {}
    for field in ("enabled", "waitMinutes", "acceptanceWindowDays"):
        if accepted.get(field) != wanted["triggers"]["connectionAccepted"][field]:
            problems.append(f"accepted trigger {field} is {accepted.get(field)!r}")
    return problems


def campaign_id(role: dict[str, Any]) -> str | None:
    """The campaign a role sends, read the way the portal reads it
    (`autopilot.emailCampaign.id`, else the role's `template`)."""
    autopilot = role.get("autopilot") or {}
    email_campaign = autopilot.get("emailCampaign") if isinstance(autopilot, dict) else None
    cid = (email_campaign or {}).get("id") if isinstance(email_campaign, dict) else None
    cid = cid or role.get("template")
    return cid if isinstance(cid, str) and cid else None


def pick_template(templates: dict[str, Any], role: dict[str, Any], role_id: str) -> tuple[str, dict[str, Any]] | None:
    """The role's campaign out of the company's list, or None while noon has
    not written it yet."""
    cid = campaign_id(role)
    if cid and isinstance(templates.get(cid), dict):
        return cid, templates[cid]
    live = [
        (tid, t) for tid, t in templates.items()
        if isinstance(t, dict) and t.get("role") == role_id and not t.get("obsolete")
    ]
    return live[0] if len(live) == 1 else None


async def role_campaign(
    session: NoonSession, role_id: str, *, wait_seconds: float = 0.0
) -> tuple[str, dict[str, Any]]:
    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        role = await fetch_role(session, role_id, fresh=True)
        templates = await session.post("templates", {"token": session.token, "company": session.company})
        found = pick_template(templates if isinstance(templates, dict) else {}, role, role_id)
        if found is not None:
            return found
        if loop.time() - started >= wait_seconds:
            raise PlatformError(
                f"noon had not written a campaign for this role after "
                f"{wait_seconds:.0f}s, so the outreach was not saved. Open the role "
                "in noon: if 'Review & Contact' shows a campaign, re-run; if not, "
                "finish sourcing there first."
            )
        await asyncio.sleep(CAMPAIGN_POLL_SECONDS)


async def write_campaign(
    session: NoonSession,
    role_id: str,
    context: dict[str, Any],
    *,
    sender_email: str,
    sender_name: str,
    wait_seconds: float = CAMPAIGN_WAIT_SECONDS,
) -> list[str]:
    """Write the document's campaign onto the role and prove it saved.

    Returns warnings for the row; raises when the campaign could not be set.
    """
    tid, template = await role_campaign(session, role_id, wait_seconds=wait_seconds)
    wanted = house_campaign(template, context, sender_email=sender_email, sender_name=sender_name)
    # The payload the editor's own save sends for a role's campaign.
    await session.post(
        "template_update",
        {
            "token": session.token,
            "company": session.company,
            "id": session.company,
            "templates": wanted,
            "tid": tid,
            "save_role": True,
            "creator": wanted.get("creator"),
            "tags": wanted.get("tags") or [],
            "edit_permission": wanted.get("edit_permission") or "anyone",
        },
    )
    saved_tid, saved = await role_campaign(session, role_id)
    problems = differences(saved, wanted) if saved_tid == tid else [f"the role now sends campaign {saved_tid}"]
    if problems:
        raise PlatformError(
            "noon did not keep the campaign as written (" + "; ".join(problems) + "). "
            "Check the role's outreach in noon before contacting anyone from it."
        )
    log.info("noon campaign written", extra={"role": role_id, "template": tid})

    warnings: list[str] = []
    signature = await session.post(
        "get_email_signature",
        {"token": session.token, "email": sender_email, "company": session.company},
    )
    if not (signature or {}).get("email_signature"):
        warnings.append(
            f"noon has no email signature saved for {sender_email}, so the emails "
            "go without one - add it under that mailbox's settings in noon"
        )
    return warnings
