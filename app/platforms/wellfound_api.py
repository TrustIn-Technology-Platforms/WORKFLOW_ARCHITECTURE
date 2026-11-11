"""Wellfound's own GraphQL calls, sent from inside the recruiter tab.

The recruiter app is a client over `POST /graphql` with *persisted*
operations: no query text is sent, only `{operationName, variables,
extensions: {operationId: "tfe/<sha256>"}}`. The pitch-template and
saved-search halves read through it (the templates list, a search's stored
filters) and write the two things the page itself only writes that way (a
search's years filter, the template a search pitches with).

`WellfoundTap` listens to the page. Every call the app makes teaches it
three things nothing else gives reliably: the request headers the server
expects, the company's `startupId`, and each operation's current id - so an
operation the page has fired itself is always sent under today's hash, and
`OPERATION_IDS` (read out of the app's registry chunk on 2026-11-12,
[scripts/probe_wellfound_operations.py](../../scripts/probe_wellfound_operations.py))
is only the fallback for one it has not.

The older delete ([wellfound_delete](wellfound_delete.py)) reads the registry
through a webpack global. That global was gone by 2026-11-12 (the app now
ships Next.js chunks), which is why this module learns from traffic instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page, Request

log = get_logger(__name__)

APP = "https://wellfound.com"

OPERATION_IDS: dict[str, str] = {
    "PitchRecipes": "81e69a1cd58602c87afb06050d1e075589e09002fac48aa26c3ee2e0803be5fa",
    "PitchRecipeDelete": "ce4042f9abb91277bf92979f45fb33edd36787fddd399fbb09d08f6472da4a93",
    "SearchFilters": "8eb330b7525540f3d12d074b60b2eb2dd979efdbd84959981707274db46a0ec2",
    "RecruitSearchResult": "73b780d7de198d2cd5006dc79ed2851a528660249e15d8b57efc220b2fe69b5d",
    "SavedSearchPreferredPitchRecipeUpdate": "12760447c99aad95c61d7c26847574faa18f569f531b45c641940c8493539add",
    "SavedSearchDelete": "ddefa1ac7c76388273d52e175ebfe4c4700167ee3471f51fca07ee4e1d837485",
}

# Sent as the page sends them; the browser adds cookies and the rest.
_FORWARDED = ("x-requested-with", "x-apollo-signature", "x-csrf-token", "x-angellist-dd-client-referrer-resource")

_FETCH_JS = """
async ([body, headers]) => {
  const r = await fetch('/graphql', {
    method: 'POST', credentials: 'same-origin', headers, body: JSON.stringify(body),
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


def unwrap(body: Any) -> dict[str, Any]:
    """One response body as a dict. The app batches, so a body may be a list
    of one."""
    if isinstance(body, list):
        body = body[0] if body else {}
    return body if isinstance(body, dict) else {}


def errors_of(body: dict[str, Any]) -> str:
    """The server's own complaint, short enough for a row."""
    messages = [str(e.get("message") or "") for e in (body.get("errors") or []) if isinstance(e, dict)]
    return "; ".join(m for m in messages if m)[:300]


class WellfoundTap:
    """Listens to one page's GraphQL traffic and sends calls the same way."""

    def __init__(self, page: "Page", *, timeout_seconds: float = 60.0) -> None:
        self.page = page
        self.timeout_seconds = timeout_seconds
        self.startup_id: str = ""
        self.operation_ids: dict[str, str] = {}
        # The variables each operation was last sent with by the page itself.
        self.last_variables: dict[str, Any] = {}
        self._headers: dict[str, str] = {}
        page.on("request", self._on_request)

    def detach(self) -> None:
        try:
            self.page.remove_listener("request", self._on_request)
        except Exception:
            pass

    def _on_request(self, request: "Request") -> None:
        if "/graphql" not in request.url or request.method != "POST":
            return
        try:
            body = json.loads(request.post_data or "null")
        except Exception:
            return
        headers = {k.lower(): v for k, v in (request.headers or {}).items()}
        kept = {k: headers[k] for k in _FORWARDED if headers.get(k)}
        if kept.get("x-apollo-signature"):
            self._headers = kept
        for item in body if isinstance(body, list) else [body]:
            if not isinstance(item, dict):
                continue
            name = item.get("operationName")
            variables = item.get("variables") or {}
            op_id = (item.get("extensions") or {}).get("operationId")
            if name and isinstance(op_id, str):
                self.operation_ids[name] = op_id
            if name:
                self.last_variables[name] = variables
            startup = variables.get("startupId") if isinstance(variables, dict) else None
            if startup and not self.startup_id:
                self.startup_id = str(startup)

    async def ready(self, *, seconds: float = 30.0) -> None:
        """Wait until the page has made a call of its own, so the headers and
        the company id are known. Every recruiter page makes several on load."""
        waited = 0.0
        while waited < seconds and not (self._headers and self.startup_id):
            await self.page.wait_for_timeout(500)
            waited += 0.5
        if self._headers and self.startup_id:
            return
        if "/login" in (self.page.url or ""):
            raise AuthenticationRequired(
                "Wellfound is not signed in. Run: python -m app.cli login wellfound"
            )
        # A page that made no call still carries the signature inline (it did
        # on 2026-10-09); the company id has no such fallback.
        try:
            config = await self.page.evaluate(_PAGE_CONFIG_JS)
        except Exception:
            config = {}
        if config.get("signature") and not self._headers:
            self._headers = {
                "x-requested-with": "XMLHttpRequest",
                "x-apollo-signature": config["signature"],
                "x-csrf-token": config.get("csrf", ""),
            }
        if not self._headers or not self.startup_id:
            raise PlatformError(
                "Wellfound's page made none of its usual calls, so its company id "
                "and request signature could not be read. Open wellfound.com/recruit "
                "by hand to see what it shows, then run this again."
            )

    def operation_id(self, name: str) -> str:
        live = self.operation_ids.get(name)
        if live:
            return live
        if name not in OPERATION_IDS:
            raise PlatformError(f"no persisted operation id is known for Wellfound's {name}")
        return f"tfe/{OPERATION_IDS[name]}"

    async def call(self, name: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One operation. Returns the body (`data`, `errors`); the caller
        decides what an error means."""
        await self.ready()
        body = {
            "operationName": name,
            "variables": variables,
            "extensions": {"operationId": self.operation_id(name)},
        }
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-apollo-operation-name": name,
            **self._headers,
        }
        try:
            result = await asyncio.wait_for(
                self.page.evaluate(_FETCH_JS, [body, headers]), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise PlatformError(
                f"Wellfound did not answer {name} within {self.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:  # a closed page, a navigation mid-call
            raise PlatformError(f"the Wellfound call {name} could not be made: {exc}") from exc
        status = int(result.get("status") or 0)
        if status in (401, 403):
            raise AuthenticationRequired(
                "Wellfound rejected the session. Run: python -m app.cli login wellfound"
            )
        data = unwrap(result.get("data"))
        if status >= 400 or not data:
            raise PlatformError(
                f"Wellfound returned {status} from {name}: {str(result.get('data'))[:200]}"
            )
        log.debug("wellfound call", extra={"operation": name, "status": status})
        return data
