"""Delete what a row created on Wellfound: its draft job posting.

Mapped on 2026-10-09 from Wellfound's own help article ("How do I delete and
unpublish a job posting?": open the job, click the trash can top-right;
deleting removes the whole record, applicants included) and from the app's
public JavaScript, because the local session had ended before the screen
could be opened (docs/platforms/wellfound.md, "Deleting a row"):

- **Transport.** Apollo over `POST /graphql`, `credentials: same-origin`,
  headers `X-Requested-With: XMLHttpRequest`, `X-Apollo-Signature`
  (`window._alConfig.__APOLLO_SIGNATURE__`, on every page) and
  `X-Apollo-Operation-Name`. The query text is never sent: the body is
  `{operationName, variables, extensions: {operationId: "tfe/<sha256>"}}`,
  the hash coming from a registry the app ships (chunk `6994`, module
  `getPersistedQueryAlias`).
- **Delete** is the operation `DestroyJobListing`. Its variables are not in
  the client code (the job page is server-rendered), so the call is tried
  with the likely variable name first and, when Wellfound answers that a
  *variable* was wrong, once more with the name it asks for. The job id
  never changes between attempts; only the key it is sent under.
- **Read-back** is the job's own recruiter page: a deleted listing is gone
  from `/recruit/jobs/<id>`.

Only a draft is deleted. A listing a recruiter has published since (an
*Unpublish* button on its page) is left alone and named on the row, because
deleting it would take its applicants with it.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://wellfound.com"
# The persisted-operation registry as shipped on 2026-10-09
# (application-c503218edf32f5ebc0bc.js, chunk 6994). The hash is the sha256 of
# the operation document, so it changes only when Wellfound edits the
# operation; the live registry on the page is read first and this is the
# fallback.
OPERATION_IDS = {
    "DestroyJobListing": "bc24373bc28b7799b2ce3b4bff257753b0b149cc88fcbf90fd34fe9695ba439b",
    "RecruitJobListing": "c4075efe6454e0e9bdf743427c4139157111f4db4f1d71031441189e3eb310f7",
}
# The variable names tried for DestroyJobListing, in order. `jobListingId`
# is the name the recruit code uses for a listing's id elsewhere.
VARIABLE_SHAPES: tuple[tuple[str, ...], ...] = (
    ("jobListingId",),
    ("id",),
    ("input", "jobListingId"),
    ("input", "id"),
)
# What the page says when the listing is gone.
GONE_MARKERS = ("page not found", "doesn't exist", "does not exist", "no longer available", "404")

_GRAPHQL_JS = """
async ([name, opId, variables, signature, csrf]) => {
  const r = await fetch('/graphql', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
      'X-Apollo-Signature': signature || '',
      'X-Apollo-Operation-Name': name,
      'X-CSRF-Token': csrf || '',
    },
    body: JSON.stringify({operationName: name, variables, extensions: {operationId: opId}}),
  });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 400); }
  return {status: r.status, data};
}
"""

_PAGE_CONFIG_JS = """
() => ({
  signature: (window._alConfig || {}).__APOLLO_SIGNATURE__ || '',
  csrf: (document.querySelector('meta[name=csrf-token]') || {}).content || '',
})
"""

# The registry the running app holds, through webpack's own module cache, so
# a redeploy that re-hashes an operation is followed without a code change.
_REGISTRY_JS = """
(name) => {
  try {
    const chunks = window.webpackChunkangellist;
    if (!chunks || !chunks.push) return null;
    let req = null;
    chunks.push([[Symbol('probe')], {}, (r) => { req = r; }]);
    if (!req || !req.c) return null;
    for (const key of Object.keys(req.c)) {
      const exp = req.c[key] && req.c[key].exports;
      if (exp && typeof exp.getPersistedQueryAlias === 'function') {
        try { return exp.getPersistedQueryAlias(name); } catch (e) { return null; }
      }
    }
  } catch (e) {}
  return null;
}
"""

_PAGE_STATE_JS = """
() => {
  const text = (document.body && document.body.innerText) || '';
  const h1 = document.querySelector('h1');
  const buttons = [...document.querySelectorAll('button, a')].map(b => (b.innerText || '').trim()).filter(Boolean);
  return {
    url: location.href,
    title: document.title || '',
    h1: h1 ? (h1.innerText || '').trim() : '',
    text: text.slice(0, 6000),
    has_unpublish: buttons.some(t => /^unpublish$/i.test(t)),
    has_publish: buttons.some(t => /^publish$/i.test(t)),
  };
}
"""


def job_id_from(value: str | None) -> str | None:
    """The listing id in `/recruit/jobs/<id>[/edit]`, or a bare id."""
    text = (value or "").strip()
    if not text:
        return None
    in_path = re.search(r"/recruit/jobs/(\d+)", text)
    if in_path:
        return in_path.group(1)
    if "/" in text or "http" in text:
        return None
    bare = re.fullmatch(r"\D*(\d{4,})\D*", text)
    return bare.group(1) if bare else None


def shape_variables(shape: tuple[str, ...], job_id: str) -> dict[str, Any]:
    """`("input", "jobListingId")` -> `{"input": {"jobListingId": "<id>"}}`."""
    value: Any = job_id
    for key in reversed(shape):
        value = {key: value}
    return value


def variable_complaint(errors: list[dict[str, Any]]) -> str | None:
    """The name Wellfound asked for when it rejected our variables, else None.

    graphql-ruby words these "Variable $input of type ... was provided
    invalid value", "... is required", "Variable $foo is declared by
    DestroyJobListing but not used". Anything else is a real refusal.
    """
    for error in errors:
        message = str(error.get("message") or "")
        match = re.search(r"\$(\w+)", message)
        if match and re.search(r"variable|required|invalid value|declared|not used|unused", message, re.I):
            return match.group(1)
    return None


@dataclass(slots=True)
class WellfoundSession:
    page: "Page"
    signature: str
    csrf_token: str
    timeout_seconds: float = 60.0

    async def operation_id(self, name: str) -> str:
        """The live registry's hash for `name`, else the one shipped here."""
        live = None
        try:
            live = await self.page.evaluate(_REGISTRY_JS, name)
        except Exception:
            live = None
        if isinstance(live, str) and re.fullmatch(r"[0-9a-f]{64}", live):
            return f"tfe/{live}"
        if name not in OPERATION_IDS:
            raise PlatformError(f"no persisted operation id is known for Wellfound's {name}")
        return f"tfe/{OPERATION_IDS[name]}"

    async def graphql(self, name: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One call. Returns the whole body (`data`, `errors`); the caller
        decides whether an error is a wrong variable name or a refusal."""
        op_id = await self.operation_id(name)
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_GRAPHQL_JS, [name, op_id, variables, self.signature, self.csrf_token]),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(f"Wellfound did not answer {name} within {self.timeout_seconds:.0f}s.") from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"the Wellfound call {name} could not be made: {exc}") from exc
        status = int(result.get("status") or 0)
        data = result.get("data")
        if status in (401, 403):
            raise AuthenticationRequired(
                "Wellfound rejected the session while deleting. Run: python -m app.cli login wellfound"
            )
        if status >= 400 or not isinstance(data, dict):
            raise PlatformError(f"Wellfound returned {status} from {name}: {str(data)[:200]}")
        return data


async def capture_session(page: "Page") -> WellfoundSession:
    """The page must be inside the recruiter app (the adapter's login check
    puts it there); the Apollo signature and CSRF token are on every page."""
    if "wellfound.com" not in (page.url or ""):
        await page.goto(f"{APP}/recruit/jobs-beta", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(4_000)
    config: dict[str, str] = {}
    for _ in range(10):
        try:
            config = await page.evaluate(_PAGE_CONFIG_JS)
        except Exception:
            config = {}
        if config.get("signature"):
            break
        await page.wait_for_timeout(2_000)
    if not config.get("signature"):
        raise AuthenticationRequired(
            "Wellfound's page carries no Apollo signature, so it is not the app. "
            "Run: python -m app.cli login wellfound"
        )
    return WellfoundSession(page=page, signature=config["signature"], csrf_token=config.get("csrf", ""))


async def read_job(page: "Page", job_id: str) -> dict[str, Any]:
    """The listing's recruiter page, as a person would see it.

    `exists` is False on a 404, a redirect away from the listing, or a page
    that says so. A listing that exists reports its title and whether it is
    published (an Unpublish button) - the one state the delete refuses.
    """
    url = f"{APP}/recruit/jobs/{job_id}"
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:
        raise PlatformError(f"could not open Wellfound job {job_id}: {exc}") from exc
    await page.wait_for_timeout(5_000)
    status = response.status if response is not None else 0
    state = await page.evaluate(_PAGE_STATE_JS)
    final = str(state.get("url") or page.url)
    if "/login" in final:
        raise AuthenticationRequired(
            "Wellfound sent the delete to its login page - the session has ended. "
            "Run: python -m app.cli login wellfound"
        )
    text_low = (state.get("text") or "").lower()
    moved_away = f"/recruit/jobs/{job_id}" not in final
    exists = status < 400 and not moved_away and not any(m in text_low for m in GONE_MARKERS)
    return {
        "exists": exists,
        "status": status,
        "url": final,
        "title": (state.get("h1") or "").strip() or re.sub(r"\s*[|\-–]\s*Wellfound.*$", "", state.get("title") or "").strip(),
        "published": bool(state.get("has_unpublish")),
        "draft": bool(state.get("has_publish")) and not bool(state.get("has_unpublish")),
    }


def refusal(record: dict[str, str], job: dict[str, Any]) -> str | None:
    """Why this listing is NOT deleted, or None when it is."""
    if job.get("published"):
        return (
            "it has been published since it was posted (its page offers Unpublish), "
            "and deleting a published listing removes its applicants"
        )
    wanted = (record.get("job_title") or "").strip().lower()
    actual = (job.get("title") or "").strip().lower()
    if wanted and actual and wanted[:60] not in actual and actual[:60] not in wanted:
        return f"its page is titled {job.get('title')!r} now, not {record.get('job_title')!r}"
    return None


@dataclass(slots=True)
class WellfoundDeleteReport:
    job_id: str
    title: str = ""
    found: bool = False
    published: bool | None = None
    deleted: bool = False
    refused: str = ""
    dry_run: bool = False
    # The variable name Wellfound accepted, for the platform notes.
    accepted_shape: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.deleted and not self.refused

    @property
    def summary(self) -> str:
        label = f"Wellfound job {self.title!r}" if self.title else f"Wellfound job {self.job_id}"
        if self.refused:
            return f"{label} left alone"
        if self.dry_run:
            if not self.found:
                return f"dry run: {label} already gone"
            state = "published" if self.published else "draft"
            return f"dry run: {label} found ({state}); nothing changed"
        if self.deleted and not self.found:
            return f"{label} already gone"
        if self.deleted:
            return f"{label} deleted"
        return f"{label} NOT deleted"


async def destroy_listing(session: WellfoundSession, job_id: str) -> str:
    """Send DestroyJobListing, re-shaping the variables once when Wellfound
    names a different variable. Returns the shape that was accepted."""
    tried: list[str] = []
    shapes = list(VARIABLE_SHAPES)
    while shapes:
        shape = shapes.pop(0)
        tried.append(".".join(shape))
        body = await session.graphql("DestroyJobListing", shape_variables(shape, job_id))
        errors = [e for e in (body.get("errors") or []) if isinstance(e, dict)]
        if not errors:
            return tried[-1]
        wanted = variable_complaint(errors)
        if wanted is None:
            message = "; ".join(str(e.get("message") or e) for e in errors[:3])
            raise PlatformError(f"Wellfound refused DestroyJobListing for job {job_id}: {message[:300]}")
        # Put the name Wellfound asked for at the front, once, if it is new.
        for candidate in ((wanted,), (wanted, "jobListingId"), (wanted, "id")):
            if ".".join(candidate) not in tried and candidate not in shapes:
                shapes.insert(0, candidate)
        log.info("wellfound destroy variables rejected", extra={"job": job_id, "tried": tried[-1], "wanted": wanted})
        if len(tried) >= 6:
            break
    raise PlatformError(
        f"Wellfound rejected every variable shape tried for DestroyJobListing on job {job_id} "
        f"({', '.join(tried)}) - map the call with scripts/probe_wellfound_job_delete.py"
    )


async def delete_job(
    page: "Page",
    record: dict[str, str],
    *,
    dry_run: bool,
    session: WellfoundSession | None = None,
) -> WellfoundDeleteReport:
    """Delete the listing the record names, read back on its own page."""
    job_id = job_id_from(record.get("job") or record.get("post_url"))
    if not job_id:
        raise PlatformError(
            "the record names no Wellfound job id to delete by "
            f"({record.get('job') or record.get('post_url') or 'no link'})"
        )
    report = WellfoundDeleteReport(job_id=job_id, dry_run=dry_run)
    job = await read_job(page, job_id)
    if not job["exists"]:
        report.deleted = True
        report.warnings.append(f"Wellfound job {job_id} was already gone")
        return report
    report.found = True
    report.title = str(job.get("title") or "")
    report.published = bool(job.get("published"))

    refused = refusal(record, job)
    if refused:
        report.refused = refused
        report.warnings.append(
            f"Wellfound job {report.title!r} ({job_id}) was left alone because {refused} - "
            "open it in Wellfound and unpublish or delete it by hand"
        )
        log.warning("wellfound job not deleted", extra={"job": job_id, "why": refused})
        return report
    if dry_run:
        return report

    session = session or await capture_session(page)
    report.accepted_shape = await destroy_listing(session, job_id)
    log.info("wellfound job delete sent", extra={"job": job_id, "shape": report.accepted_shape})
    after = await read_job(page, job_id)
    report.deleted = not after["exists"]
    log.info("wellfound job deleted", extra={"job": job_id, "gone": report.deleted})
    if not report.deleted:
        report.warnings.append(
            f"Wellfound accepted DestroyJobListing for job {report.title!r} ({job_id}) but its page "
            "still opens - check the Jobs list and ask the delete again"
        )
    return report
