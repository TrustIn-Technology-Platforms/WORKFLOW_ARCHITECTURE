"""Retire a noon role: stop its sourcing, then delete it.

Both halves go through the calls the portal itself makes, read out of its
bundle on 2026-09-08 (docs/platforms/noon.md, "Retiring a role"):

- **Stop sourcing** is `role_autopilot` with the role's own autopilot block
  and `enabled: false`. The portal shows a role as paused when it is `active`
  and `autopilot.enabled === false`; there is no separate pause endpoint and
  no `active` flag a client can set - `active` only ever goes false on delete.
- **Delete** is `delete_role {token, id}`, behind the portal's "Confirm
  deletion?" prompt. The role leaves `refetch_roles` at once.

Nothing here touches the campaign (the template): a role's outreach only
starts when a recruiter presses `Contact N candidates`, and deleting the role
takes its project and template with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import PlatformError
from app.platforms.noon_sourcing import NoonSession, capture_session, fetch_role

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


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


async def delete_role(session: NoonSession, role_id: str) -> bool:
    """Delete the role and confirm it is gone from the role list."""
    await session.post("delete_role", {"token": session.token, "id": role_id})
    try:
        await fetch_role(session, role_id)
    except PlatformError:
        log.info("noon role deleted", extra={"role": role_id})
        return True
    log.warning("noon role still listed after delete", extra={"role": role_id})
    return False


async def retire_role(
    page: "Page", role_id: str, *, delete: bool, dry_run: bool
) -> RetireReport:
    """Stop the role's sourcing and, when asked, delete it. A dry run only reads."""
    report = RetireReport(role_id=role_id, dry_run=dry_run)
    session = await capture_session(page)
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
        report.deleted = await delete_role(session, role_id)
        if not report.deleted:
            report.warnings.append(
                "the role is still in noon's list after delete_role - delete it by hand"
            )
    return report
