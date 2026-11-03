"""Retire a noon role: stop its sourcing, then delete it.

Both halves go through the calls the portal itself makes, read out of its
bundle on 2026-09-08 (docs/platforms/noon.md, "Retiring a role"):

- **Stop sourcing** is `role_autopilot` with the role's own autopilot block
  and `enabled: false`. The portal shows a role as paused when it is `active`
  and `autopilot.enabled === false`; there is no separate pause endpoint and
  no `active` flag a client can set - `active` only ever goes false on delete.
- **Delete** is `delete_role {token, id}`, behind the portal's "Confirm
  deletion?" prompt. The role's row in `refetch_roles` becomes an `obsolete`
  tombstone - unless the list had not caught up with the role yet, which it
  takes minutes to do, and then it simply never appears (2026-11-04). The
  direct read, `poll_role_params`, keeps answering for a deleted role and so
  cannot read a deletion back.

Nothing here touches the campaign (the template): a role's outreach only
starts when a recruiter presses `Contact N candidates`, and deleting the role
takes its project and template with it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.platforms.noon_sourcing import (
    ROLE_POLL_SECONDS,
    NoonSession,
    capture_session,
    fetch_role,
    list_role_row,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

# How long a row still live in the list after `delete_role` gets to turn into
# a tombstone before the delete is called a failure.
LIST_SETTLE_SECONDS = 30.0


@dataclass(slots=True)
class RetireReport:
    """What was found and what was done, for the row's detail line."""

    role_id: str
    name: str = ""
    was_active: bool | None = None
    sourcing_was_enabled: bool | None = None
    sourcing_stopped: bool = False
    deleted: bool = False
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        label = f"noon role {self.name!r}" if self.name else f"noon role {self.role_id}"
        if self.dry_run:
            state = (
                "sourcing on" if self.sourcing_was_enabled
                else "sourcing already off" if self.sourcing_was_enabled is False
                else "no sourcing block"
            )
            return f"dry run: {label} found ({state}); nothing changed"
        done = []
        if self.sourcing_stopped:
            done.append("sourcing stopped")
        elif self.sourcing_was_enabled is False:
            done.append("sourcing was already off")
        if self.deleted:
            done.append("deleted")
        return f"{label}: {', '.join(done) or 'nothing changed'}"


def stopped_autopilot(autopilot: dict[str, Any]) -> dict[str, Any]:
    """The autopilot block as the portal would save it to stop the search:
    everything as it was, `enabled` off. A copy - the caller's block is what
    the report compares against."""
    block = dict(autopilot)
    block["enabled"] = False
    return block


async def stop_sourcing(session: NoonSession, role: dict[str, Any]) -> bool:
    """Turn the role's autopilot off and read it back. True when noon holds it off."""
    role_id = str(role.get("id"))
    autopilot = role.get("autopilot")
    if not isinstance(autopilot, dict) or not autopilot:
        log.info("noon role has no autopilot block", extra={"role": role_id})
        return False
    # No token, as the portal's own save-autopilot call carries none.
    await session.post(
        "role_autopilot", {"id": role_id, "autopilot": stopped_autopilot(autopilot)}
    )
    after = await fetch_role(session, role_id)
    enabled = (after.get("autopilot") or {}).get("enabled")
    log.info("noon sourcing stopped", extra={"role": role_id, "enabled_after": enabled})
    return enabled is False


async def delete_role(
    session: NoonSession,
    role_id: str,
    *,
    settle_seconds: float | None = None,
    poll_seconds: float | None = None,
) -> tuple[bool, str | None]:
    """Delete the role and read the deletion back off the role list.

    Only the list can say a role was deleted - the direct read keeps answering
    for one - and it says so in one of two ways: the row turns into an
    `obsolete` tombstone, or, for a role the list had not caught up with yet
    (every role deleted within minutes of being made, 2026-11-04), it never
    appears at all. noon accepting the call is the same evidence the portal
    acts on when it drops the role from its screen, so an unlisted role
    counts as deleted, with a note for the row. Only a row still live after
    `settle_seconds` is a failure. Returns (deleted, note).
    """
    settle = LIST_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    poll = ROLE_POLL_SECONDS if poll_seconds is None else poll_seconds
    listed_before = await list_role_row(session, role_id)
    await session.post("delete_role", {"token": session.token, "id": role_id})
    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        row = await list_role_row(session, role_id)
        if row is None and listed_before is None:
            log.info("noon role deleted before its list knew it", extra={"role": role_id})
            return True, (
                "noon's role list had not caught up with this role, so the deletion "
                "could not be read back; noon accepted the delete"
            )
        if row is None or row.get("obsolete"):
            log.info("noon role deleted", extra={"role": role_id})
            return True, None
        if loop.time() - started >= settle:
            break
        await asyncio.sleep(poll)
    log.warning("noon role still listed after delete", extra={"role": role_id})
    return False, None


async def retire_role(
    page: "Page",
    role_id: str,
    *,
    delete: bool,
    dry_run: bool,
    session: NoonSession | None = None,
) -> RetireReport:
    """Stop the role's sourcing and, when asked, delete it. A dry run only reads.

    `session` lets a caller deleting several roles capture noon's token once
    rather than reloading the portal and sniffing the token per role - the
    token rides every call and does not change between them.
    """
    report = RetireReport(role_id=role_id, dry_run=dry_run)
    session = session or await capture_session(page)
    role = await fetch_role(session, role_id)
    report.name = str(role.get("name") or "")
    report.was_active = role.get("active") if isinstance(role.get("active"), bool) else None
    autopilot = role.get("autopilot")
    report.sourcing_was_enabled = (
        autopilot.get("enabled") if isinstance(autopilot, dict) and "enabled" in autopilot else None
    )
    if dry_run:
        return report

    if report.sourcing_was_enabled:
        report.sourcing_stopped = await stop_sourcing(session, role)
        if not report.sourcing_stopped:
            report.warnings.append(
                "noon did not report the sourcing as stopped after the call - "
                "open the role and check its Sourcing card"
            )
    if delete:
        report.deleted, note = await delete_role(session, role_id)
        if note:
            report.warnings.append(note)
        if not report.deleted:
            report.warnings.append(
                f"the role is still live in noon's list {LIST_SETTLE_SECONDS:.0f}s "
                "after delete_role - delete it by hand"
            )
    return report
