"""Delete what a row created on Loxo: its Outreach campaign.

Both calls are the app's own, read out of its bundle on 2026-10-09
(docs/platforms/loxo.md, "Deleting a row"):

- **The list** is the GraphQL query `campaigns(agencyId, query, page, perPage,
  sortByField, sortDesc, filters)` the Outreach page itself sends - one
  record per campaign with `id`, `name`, `paused`, `stageCount`, `shared`,
  `createdAt` and the prospect count under `recipientAggs.idCount`. It is the
  only read-back: Loxo's documented Open API lists campaigns too, but it is a
  paid add-on with no key configured.
- **Delete** is what the row menu's *Delete* -> "Delete campaign" / "Are you
  sure you want to delete this campaign?" -> red *Delete* sends:
  `mutation { destroyCampaign(id: <id>) { id: _id } }`. The app then toasts
  *"This campaign will be deleted shortly"* - the delete is a background job,
  so the campaign can stay in the list for a moment after the call. The
  read-back polls until it has gone.

Both go to `POST /graphql` from inside the tab, with the cookie session
(`credentials: include`, as the app's own `rawFetch` sends) and the CSRF token
from the page's `meta[name=csrf-token]`.

A campaign is deleted only when the run that posted the row **created** it
(`campaign_created: yes` in the record). The poster also fills a campaign it
finds by name - a recruiter's own, or one left by an earlier, unrecorded run -
and that one is left alone and named on the row rather than guessed about. A
campaign the record names is also read back and its name compared before the
call: a delete cannot be taken back.
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

APP = "https://app.loxo.co"
# "Deleted shortly": how long the read-back waits for the campaign to leave
# the list, and how often it looks. The delete is accepted at once; the row
# must still not read Deleted while the campaign is listed.
SETTLE_SECONDS = 120.0
POLL_SECONDS = 5.0
PER_PAGE = 100

_GRAPHQL_JS = """
async ([query, token]) => {
  const r = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-CSRF-Token': token || '',
    },
    body: JSON.stringify({query}),
  });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 400); }
  return {status: r.status, data};
}
"""

_CSRF_JS = "() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''"


def campaign_id_from(value: str | None) -> str | None:
    """The campaign id in `/agencies/<agency>/campaigns/<id>[/stages]`, or a bare id.

    The agency number comes first in the URL, so the match is on the
    `/campaigns/` segment, never on the first number found.
    """
    text = (value or "").strip()
    if not text:
        return None
    in_path = re.search(r"/campaigns/(\d+)", text)
    if in_path:
        return in_path.group(1)
    if "/" in text or "http" in text:
        return None
    bare = re.fullmatch(r"\D*(\d{3,})\D*", text)
    return bare.group(1) if bare else None


def list_query(agency_id: str, *, page: int = 1, per_page: int = PER_PAGE, search: str = "") -> str:
    """The Outreach list's own query, with only the fields the delete reads."""
    return f"""{{
  campaigns(
    agencyId: {int(agency_id)},
    query: {json.dumps(search)},
    page: {int(page)},
    perPage: {int(per_page)},
    sortByField: "created_at",
    sortDesc: true,
    filters: {{onlyShared:false,onlyPaused:false,onlyActive:false,userIds:[],workflowStageIds:[],jobIds:[]}},
  ) {{
    totalResults
    campaigns {{
      id: _id
      name
      paused
      stageCount
      shared
      createdAt
      recipientAggs {{ idCount }}
    }}
  }}
}}"""


def destroy_mutation(campaign_id: str) -> str:
    """Exactly what the confirm button sends, wrapped as the app wraps a bare
    selection (`e.startsWith("mutation") ? e : "mutation { " + e + " }"`)."""
    return f"mutation {{ destroyCampaign(id: {int(campaign_id)}) {{ id: _id }} }}"


@dataclass(slots=True)
class LoxoSession:
    page: "Page"
    csrf_token: str
    agency_id: str
    timeout_seconds: float = 60.0

    async def graphql(self, query: str) -> dict[str, Any]:
        """One call; the `data` object, or an error that says what Loxo said."""
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_GRAPHQL_JS, [query, self.csrf_token]),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(
                f"Loxo did not answer a GraphQL call within {self.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"the Loxo GraphQL call could not be made: {exc}") from exc
        status = int(result.get("status") or 0)
        data = result.get("data")
        if status in (401, 403):
            raise AuthenticationRequired(
                "Loxo rejected the session while deleting. Run: python -m app.cli login loxo"
            )
        if status >= 400 or not isinstance(data, dict):
            raise PlatformError(f"Loxo returned {status} from /graphql: {str(data)[:200]}")
        if data.get("errors"):
            message = "; ".join(str(e.get("message") or e) for e in data["errors"][:3])
            raise PlatformError(f"Loxo refused the call: {message[:300]}")
        return data.get("data") or {}


async def capture_session(page: "Page", *, agency_id: str) -> LoxoSession:
    """The page must already be inside the app (the adapter's login check puts
    it there); the CSRF token is on every app page's head."""
    if "loxo.co" not in (page.url or ""):
        await page.goto(f"{APP}/", wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(5_000)
    token = ""
    for _ in range(10):
        try:
            token = await page.evaluate(_CSRF_JS)
        except Exception:
            token = ""
        if token:
            break
        await page.wait_for_timeout(2_000)
    if not token:
        raise AuthenticationRequired(
            "Loxo's page carries no CSRF token, so it is not the signed-in app. "
            "Run: python -m app.cli login loxo"
        )
    return LoxoSession(page=page, csrf_token=token, agency_id=str(agency_id))


async def list_campaigns(session: LoxoSession) -> list[dict[str, Any]]:
    """Every campaign the account can see, page by page."""
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        data = await session.graphql(list_query(session.agency_id, page=page))
        block = data.get("campaigns") or {}
        batch = [c for c in (block.get("campaigns") or []) if isinstance(c, dict)]
        found.extend(batch)
        total = int(block.get("totalResults") or 0)
        if not batch or len(found) >= total or page >= 50:
            return found
        page += 1


async def find_campaign(session: LoxoSession, campaign_id: str) -> dict[str, Any] | None:
    for campaign in await list_campaigns(session):
        if str(campaign.get("id") or "") == str(campaign_id):
            return campaign
    return None


def prospects_of(campaign: dict[str, Any]) -> int:
    aggs = campaign.get("recipientAggs") or {}
    try:
        return int(aggs.get("idCount") or 0)
    except (TypeError, ValueError):
        return 0


def refusal(record: dict[str, str], campaign: dict[str, Any]) -> str | None:
    """Why this campaign is NOT deleted, or None when it is.

    Every check has to pass. The id is the one the poster read off the URL of
    the campaign it had just created, which is sound; the flag and the name
    are what catch a record pointing at a recruiter's own campaign.
    """
    created = (record.get("campaign_created") or "").strip().lower()
    if created == "no":
        return (
            "it was already there when the row was posted (the run filled it "
            "rather than creating it), so it may be a recruiter's own"
        )
    if created != "yes":
        return "the record does not say whether the run that posted the row created it"
    wanted = (record.get("campaign_name") or "").strip()
    actual = str(campaign.get("name") or "").strip()
    if wanted and actual != wanted:
        return f"it is called {actual!r} now, not {wanted!r}"
    return None


@dataclass(slots=True)
class LoxoDeleteReport:
    campaign_id: str
    name: str = ""
    found: bool = False
    prospects: int = 0
    paused: bool | None = None
    deleted: bool = False
    # Why the campaign was left alone, when the guard refused it.
    refused: str = ""
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """The campaign is gone. A refused one is not, and the row must say so."""
        return self.deleted and not self.refused

    @property
    def summary(self) -> str:
        label = f"Loxo campaign {self.name!r}" if self.name else f"Loxo campaign {self.campaign_id}"
        if self.refused:
            return f"{label} left alone"
        if self.dry_run:
            if not self.found:
                return f"dry run: {label} already gone"
            extra = f", {self.prospects} prospect(s)" if self.prospects else ""
            state = "paused" if self.paused else "running" if self.paused is False else "found"
            return f"dry run: {label} found ({state}{extra}); nothing changed"
        if self.deleted and not self.found:
            return f"{label} already gone"
        if self.deleted:
            extra = f" ({self.prospects} prospect(s) were on it)" if self.prospects else ""
            return f"{label} deleted{extra}"
        return f"{label} NOT deleted"


async def delete_campaign(
    page: "Page",
    record: dict[str, str],
    *,
    agency_id: str,
    dry_run: bool,
    session: LoxoSession | None = None,
    settle_seconds: float = SETTLE_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> LoxoDeleteReport:
    """Delete the campaign the record names, and read it back until it is gone.

    Already gone is not a failure: a recruiter may have deleted it by hand,
    and a retry after a half-finished delete must not fail on the half that
    worked. A refused campaign is left alone and said so.
    """
    campaign_id = campaign_id_from(record.get("campaign") or record.get("post_url"))
    if not campaign_id:
        raise PlatformError(
            "the record names no Loxo campaign id to delete by "
            f"({record.get('campaign') or record.get('post_url') or 'no link'})"
        )
    report = LoxoDeleteReport(campaign_id=campaign_id, dry_run=dry_run)
    session = session or await capture_session(page, agency_id=agency_id)

    campaign = await find_campaign(session, campaign_id)
    if campaign is None:
        report.deleted = True
        report.warnings.append(f"Loxo campaign {campaign_id} was already gone")
        return report
    report.found = True
    report.name = str(campaign.get("name") or "")
    report.prospects = prospects_of(campaign)
    report.paused = campaign.get("paused") if isinstance(campaign.get("paused"), bool) else None

    refused = refusal(record, campaign)
    if refused:
        report.refused = refused
        report.warnings.append(
            f"Loxo campaign {report.name!r} ({campaign_id}) was left alone because "
            f"{refused} - check it in Loxo and delete it by hand if it is this role's"
        )
        log.warning("loxo campaign not deleted", extra={"campaign": campaign_id, "why": refused})
        return report
    if dry_run:
        return report

    await session.graphql(destroy_mutation(campaign_id))
    log.info("loxo campaign delete sent", extra={"campaign": campaign_id, "prospects": report.prospects})
    waited = 0.0
    while True:
        if await find_campaign(session, campaign_id) is None:
            report.deleted = True
            break
        if waited >= settle_seconds:
            break
        await asyncio.sleep(poll_seconds)
        waited += poll_seconds
    log.info("loxo campaign deleted", extra={"campaign": campaign_id, "gone": report.deleted})
    if not report.deleted:
        report.warnings.append(
            f"Loxo accepted the delete of campaign {report.name!r} ({campaign_id}) but still "
            f"listed it {settle_seconds:.0f}s later - it says campaigns are deleted shortly, "
            "so check the Outreach list in a few minutes and ask the delete again if it is still there"
        )
    return report
