"""Delete what a row created on Juicebox: its sequence, and its sourcing project.

Every call is the app's own, read out of its bundle on 2026-09-23
(docs/platforms/juicebox.md, "Deleting a row"):

- **Sequence** is `DELETE /api/sequence?sequenceId=<id>` - the app's
  `archiveSequence`. The sequence stays in `/api/sequence/list` with
  `archived: true`, which is what the app filters its lists on.
- **Project** is two calls, as the app's "Close and delete" makes them: close
  (`PATCH /api/projects?project_id=<id>` with `closed: true` and a reason -
  the delete refuses an open project with `project-not-closed`), then
  `DELETE /api/projects?&project_id=<id>`. It takes the project's searches
  with it.

Every `/api/` call carries the Firebase ID token in an `fbauthorization`
header, which the app mints in the page and never stores in a cookie - so a
raw fetch without it is a 401. The token is read off the app's own traffic,
as noon's is, and the calls are made from inside the tab.

A project is deleted only when the run that posted the row created it, and
only when the project still looks like that one: named as the run named it
(or "New Project", when the rename did not stick) and created while that run
was going. The id alone is not trusted. On 2026-09-23 `create_project`
returned the project the browser was already in - a real client project -
because the click on the new one never navigated; a delete trusting that id
would have taken the client's searches with it. A project named in the row's
`Juicebox Project` column is a recruiter's and is never recorded at all.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://app.juicebox.ai"

# Juicebox's own reason list (PROJECT_CLOSE_REASONS in its bundle); `other`
# with a note is the honest one for a role withdrawn from Notion.
CLOSE_REASON = "other"
CLOSE_NOTE = "Deleted from the TrustIn posting table by the posting service."
# A run creates its project minutes before the post finishes; sourcing takes
# about ten. A project created outside this window is not that run's.
CREATED_WINDOW = timedelta(minutes=45)
UNRENAMED = "New Project"

_FETCH_JS = """
async ([url, method, token, body]) => {
  const r = await fetch(url, {
    method,
    headers: {"Content-Type": "application/json", "fbauthorization": token},
    body: body === null ? undefined : JSON.stringify(body),
  });
  let data = null;
  const text = await r.text();
  try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 400); }
  return {status: r.status, data};
}
"""


def sequence_id(url: str) -> str | None:
    """The sequence id from `/project/<p>/sequences/<id>` or `?createdSequenceId=`."""
    match = re.search(r"/sequences/([A-Za-z0-9]{12,})", url or "") or re.search(
        r"[?&](?:createdSequenceId|sequenceId)=([A-Za-z0-9]{12,})", url or ""
    )
    return match.group(1) if match else None


def project_id(url: str) -> str | None:
    """The project id from any `/project/<id>/...` URL."""
    match = re.search(r"/project/([A-Za-z0-9]{12,})", url or "")
    return match.group(1) if match else None


@dataclass(slots=True)
class JuiceboxSession:
    page: "Page"
    token: str
    timeout_seconds: float = 60.0

    async def call(self, method: str, path: str, body: Any = None) -> Any:
        url = f"{APP}{path}"
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_FETCH_JS, [url, method, self.token, body]),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(
                f"Juicebox did not answer {method} {path} within {self.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"Juicebox call {method} {path} could not be made: {exc}") from exc
        status = int(result.get("status") or 0)
        data = result.get("data")
        if status in (401, 403):
            raise AuthenticationRequired(
                "Juicebox rejected the session while deleting. Run: "
                "python -m app.cli login juicebox"
            )
        if status >= 400:
            raise PlatformError(f"Juicebox returned {status} from {method} {path}: {str(data)[:200]}")
        return data


async def capture_session(page: "Page", *, timeout_ms: int = 60_000) -> JuiceboxSession:
    """Load the app and take the token off the first call that carries one."""
    found: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if "token" in found or "/api/" not in request.url:
            return
        try:
            token = request.headers.get("fbauthorization")
        except Exception:
            return
        if token:
            found["token"] = token

    page.on("request", on_request)
    try:
        # `commit`: the app holds the document open and domcontentloaded can
        # outlast any timeout (docs/platforms/juicebox.md).
        await page.goto(f"{APP}/", wait_until="commit", timeout=60_000)
        waited = 0
        while waited < timeout_ms and "token" not in found:
            await page.wait_for_timeout(500)
            waited += 500
    finally:
        page.remove_listener("request", on_request)
    if "token" not in found:
        raise AuthenticationRequired(
            "Juicebox never made a signed-in call, so the session is not live. "
            "Run: python -m app.cli login juicebox"
        )
    return JuiceboxSession(page=page, token=found["token"])


def _items(data: Any) -> list[dict[str, Any]]:
    """Every record under `result`. The sequence list is a flat list there;
    the project list is a dict of groups (`your_agents`, `your_projects`,
    `team_projects`, ...), and a project can sit in any of them."""
    inner = data.get("result") if isinstance(data, dict) else data
    if isinstance(inner, list):
        return [d for d in inner if isinstance(d, dict)]
    if isinstance(inner, dict):
        return [
            d for group in inner.values() if isinstance(group, list)
            for d in group if isinstance(d, dict)
        ]
    return []


async def list_sequences(session: JuiceboxSession) -> list[dict[str, Any]]:
    return _items(await session.call("GET", "/api/sequence/list"))


async def list_projects(session: JuiceboxSession) -> list[dict[str, Any]]:
    """Every project the account can see, closed ones included."""
    return _items(await session.call("GET", "/api/projects"))


async def get_project(session: JuiceboxSession, project: str) -> dict[str, Any] | None:
    """The project from the full list, or None once it is deleted.

    The list, not `GET /api/projects?project_id=`: that answers a merely
    closed project with the same 404 as a deleted one, and a project closed by
    hand but never deleted would read as gone.
    """
    return _find(await list_projects(session), project)


def _find(items: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("id") or "") == wanted:
            return item
    return None


def _created_at(project: dict[str, Any]) -> datetime | None:
    stamp = project.get("dateAdded") or project.get("createdAt")
    if isinstance(stamp, dict) and isinstance(stamp.get("_seconds"), (int, float)):
        return datetime.fromtimestamp(stamp["_seconds"], tz=timezone.utc)
    return None


def project_matches(
    project: dict[str, Any], *, names: tuple[str, ...], posted_at: datetime | None
) -> str | None:
    """Why this project is NOT the one the run created, or None when it is.

    Every check has to pass: a wrong id reaching here means another client's
    project, and a delete cannot be taken back.
    """
    title = str(project.get("title") or "").strip()
    allowed = {n.strip() for n in names if n and n.strip()} | {UNRENAMED}
    if title not in allowed:
        named = " or ".join(repr(n) for n in sorted(allowed))
        return f"it is called {title!r}, not {named}"
    if project.get("isAgenticProject") or project.get("agentStatus"):
        return "it runs a Juicebox agent, which no posting run creates"
    created = _created_at(project)
    if posted_at is None or created is None:
        return "its creation time could not be checked against the run's"
    if abs(created - posted_at) > CREATED_WINDOW:
        return (
            f"it was created {created:%Y-%m-%d %H:%M} UTC, not during the run "
            f"that posted the row ({posted_at:%Y-%m-%d %H:%M} UTC)"
        )
    return None


@dataclass(slots=True)
class JuiceboxDeleteReport:
    sequence_id: str | None = None
    sequence_name: str = ""
    sequence_deleted: bool = False
    project_id: str | None = None
    project_name: str = ""
    project_deleted: bool = False
    # Why the project was left alone, when the guard refused it.
    project_refused: str = ""
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Everything asked for is gone. A refused project is not gone, and
        the row has to say so rather than read as deleted."""
        return (not self.sequence_id or self.sequence_deleted) and (
            not self.project_id or self.project_deleted
        )

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.sequence_id:
            label = f"sequence {self.sequence_name!r}" if self.sequence_name else f"sequence {self.sequence_id}"
            # A dry run marks something missing as deleted (there is nothing
            # left to do), so it must not then report it as found.
            state = (
                "already gone" if self.dry_run and self.sequence_deleted
                else "found" if self.dry_run
                else "deleted" if self.sequence_deleted
                else "NOT deleted"
            )
            parts.append(f"{label} {state}")
        if self.project_id:
            label = f"project {self.project_name!r}" if self.project_name else f"project {self.project_id}"
            state = (
                "left alone" if self.project_refused
                else "already gone" if self.dry_run and self.project_deleted
                else "found" if self.dry_run
                else "deleted" if self.project_deleted
                else "NOT deleted"
            )
            parts.append(f"{label} {state}")
        prefix = "dry run: " if self.dry_run else ""
        return prefix + ("; ".join(parts) or "nothing to delete")


async def delete_records(
    page: "Page",
    *,
    sequence: str | None,
    project: str | None,
    dry_run: bool,
    project_names: tuple[str, ...] = (),
    posted_at: datetime | None = None,
) -> JuiceboxDeleteReport:
    """Delete the sequence and (when given) the project, each read back after.

    Something already gone is not a failure: a recruiter may have deleted it by
    hand, and a retry after a half-finished run must not fail on the half that
    worked. A project that fails `project_matches` is left alone and said so.
    """
    report = JuiceboxDeleteReport(sequence_id=sequence, project_id=project, dry_run=dry_run)
    session = await capture_session(page)

    if sequence:
        found = _find(await list_sequences(session), sequence)
        report.sequence_name = str((found or {}).get("title") or "")
        if found is None or found.get("archived"):
            report.sequence_deleted = True
            report.warnings.append(f"sequence {sequence} was already gone")
        elif not dry_run:
            await session.call("DELETE", f"/api/sequence?sequenceId={sequence}")
            after = _find(await list_sequences(session), sequence)
            report.sequence_deleted = after is None or bool(after.get("archived"))
            log.info("juicebox sequence deleted", extra={"sequence": sequence, "gone": report.sequence_deleted})
            if not report.sequence_deleted:
                report.warnings.append(
                    f"Juicebox still lists sequence {sequence} after the delete - remove it by hand"
                )

    if project:
        found = await get_project(session, project)
        report.project_name = str((found or {}).get("title") or "")
        if found is None:
            report.project_deleted = True
            report.warnings.append(f"project {project} was already gone")
            return report
        refused = project_matches(found, names=project_names, posted_at=posted_at)
        if refused:
            report.project_refused = refused
            report.warnings.append(
                f"project {project} was left alone because {refused} - check it "
                "in Juicebox and delete it by hand if it is this role's"
            )
            log.warning("juicebox project not deleted", extra={"project": project, "why": refused})
        elif not dry_run:
            if not found.get("closed"):
                await session.call(
                    "PATCH",
                    f"/api/projects?project_id={project}",
                    {
                        "closed": True,
                        "closedReason": CLOSE_REASON,
                        "closedReasonDetails": CLOSE_NOTE,
                        "shouldCancelSequences": False,
                    },
                )
            await session.call("DELETE", f"/api/projects?&project_id={project}")
            report.project_deleted = await get_project(session, project) is None
            log.info("juicebox project deleted", extra={"project": project, "gone": report.project_deleted})
            if not report.project_deleted:
                report.warnings.append(
                    f"Juicebox still has project {project} after the delete - remove it by hand"
                )
    return report
