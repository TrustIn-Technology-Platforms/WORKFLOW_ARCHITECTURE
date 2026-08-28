"""Run a recipe against a live page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import (
    Advert,
    EmailStep,
    NotionRow,
    ParsedDocument,
    PlatformError,
)
from app.platforms.actions import ACTIONS, StepRun
from app.platforms.recipe import Recipe, Step
from app.utils import templating

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


class _StopRun(Exception):
    """Internal: a dry run reached the submit step and stops there."""


@dataclass(slots=True)
class RunReport:
    captures: dict[str, str] = field(default_factory=dict)
    executed: int = 0
    skipped: int = 0
    submitted: bool = False
    emails_written: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def post_url(self) -> str | None:
        return self.captures.get("post_url")


def _role_name(
    source_name: str, row: NotionRow | None, advert: Advert, email_steps: list[EmailStep]
) -> str:
    """The name every platform gives the role/sequence, in one place.

    The recruiters' .docx filename is the convention "Company - Role - Location"
    and is the source of truth, so it wins. Then the Notion row title, then a
    real advert title, and last the shared subject - an emails-only document has
    no advert title, and the fallback advert can be an email greeting the parser
    latched onto, so the subject beats 'Hi {first_name},'.
    """
    if source_name.strip():
        return source_name.strip()
    if row is not None and (row.title or "").strip():
        return row.title.strip()
    title = (advert.title or "").strip()
    looks_like_greeting = (
        title.lower().startswith(("hi ", "hello", "hey", "dear")) or "{" in title
    )
    if title and not looks_like_greeting:
        return title
    if email_steps:
        return (email_steps[0].subject or "").strip()
    return ""


def _channel_step(
    document: ParsedDocument, channel: str, email_steps: list[EmailStep]
) -> dict[str, Any]:
    """The first step of `channel`, else the first email as a stand-in.

    The stand-in keeps a fixed-slot recipe (noon's InMail slot, its
    connection-accepted message) working on an emails-only document. An empty
    dict-shaped context would fill the slot with nothing, which the platform
    saves - worse than repeating the opener.
    """
    for step in document.emails:
        if step.channel == channel:
            return step.as_context()
    if email_steps:
        return email_steps[0].as_context()
    return EmailStep(order=1, subject="", body_text="", body_html="").as_context()


def build_context(
    document: ParsedDocument,
    row: NotionRow | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The values a recipe expression can reach.

    `row.property` exposes every Notion column as plain text, so advert metadata
    kept as a real column rather than as prose in the document needs no code
    change to reach a platform field.
    """
    advert = document.advert or Advert(title="", body_text="", body_html="")

    # A recipe with fixed slots (noon) maps the email steps onto them by index,
    # so `emails` must be the email-channel steps only: a LinkedIn note or an
    # InMail is a step too, but it is not what goes in an email slot, and
    # leaving it in shifts every index down. Juicebox filters the same way in
    # its driver; the full, all-channel list stays available as `steps`.
    email_steps = [e for e in document.emails if e.is_email]

    row_context: dict[str, Any] = {"title": "", "url": "", "property": {}}
    if row is not None:
        from app.notion.schema import plain_text_of

        row_context = {
            "title": row.title,
            "url": row.url or "",
            "page_id": row.page_id,
            "property": {
                name: (plain_text_of(value) or "")
                for name, value in (row.raw_properties or {}).items()
            },
        }

    now = datetime.now(timezone.utc)
    return {
        **(defaults or {}),
        "advert": advert.as_context(),
        # Email steps only, for platforms whose campaign has fixed slots that the
        # emails are mapped onto (noon) rather than one step per email.
        "emails": [e.as_context() for e in email_steps],
        "email_count": len(email_steps),
        # Every step, all channels, in document order - for a recipe that needs
        # the LinkedIn or InMail copy rather than an email slot.
        "steps": [e.as_context() for e in document.emails],
        # The document's own InMail and connection-note copy, for platforms
        # whose campaign has slots for those channels (noon). Documents used to
        # carry emails only, so recipes recycled email 1 into these slots; fall
        # back to that when a document has no such section, so email-only
        # documents keep posting exactly as before.
        "inmail": _channel_step(document, "inmail", email_steps),
        "connection_note": _channel_step(document, "linkedin", email_steps),
        # The name a recipe should give the role/sequence: row title, else a
        # real advert title, else the first email's subject. See _role_name.
        "role_name": _role_name(document.source_name, row, advert, email_steps),
        "row": row_context,
        "now": {
            "date": now.date().isoformat(),
            "iso": now.isoformat(),
            "year": str(now.year),
        },
    }


class RecipeEngine:
    """Executes `steps`, then `per_email` per email, then `finalise`."""

    def __init__(
        self,
        recipe: Recipe,
        page: "Page",
        settings: Settings | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.recipe = recipe
        self.page = page
        self.settings = settings or get_settings()
        self.dry_run = self.settings.dry_run if dry_run is None else dry_run
        self.report = RunReport()

    async def run(
        self, document: ParsedDocument, row: NotionRow | None = None
    ) -> RunReport:
        context = build_context(document, row, self.recipe.defaults)

        try:
            await self._run_phase(self.recipe.steps, context)

            if self.recipe.kind == "email_sequence":
                emails = sorted(document.emails, key=lambda e: e.order)
                if not emails:
                    raise PlatformError(
                        f"{self.recipe.label} posts an email sequence, but the "
                        "document produced no emails."
                    )
                for email in emails:
                    await self._run_phase(
                        self.recipe.per_email, {**context, "email": email.as_context()}
                    )
                    self.report.emails_written += 1
                    log.info(
                        "email step written",
                        extra={
                            "platform": self.recipe.key,
                            "order": email.order,
                            "subject": email.subject[:80],
                        },
                    )

            await self._run_phase(self.recipe.finalise, context)
        except _StopRun:
            # A dry run stopped at the submit. Everything after it exists to
            # confirm or read back a post that was never made, so running those
            # steps would only produce a misleading failure.
            pass

        return self.report

    async def _run_phase(self, steps: list[Step], context: dict[str, Any]) -> None:
        for step in steps:
            await self._run_step(step, context)

    async def _run_step(self, step: Step, context: dict[str, Any]) -> None:
        spec = ACTIONS[step.action]

        if step.submit and self.dry_run:
            # Everything before this point has already run against the real
            # page, which is what makes a dry-run worth anything: selectors,
            # field mapping and the session are all exercised.
            self.report.skipped += 1
            self.report.warnings.append(
                f"dry run: stopped before {step.description}"
            )
            log.info(
                "dry run - stopped at submit",
                extra={"platform": self.recipe.key, "step": step.description},
            )
            raise _StopRun

        params = dict(step.params)
        for name in spec.templated:
            if name in params:
                params[name] = templating.render(params[name], context)
        if "selector" in params:
            params["selector"] = templating.render_deep(params["selector"], context)

        if self._should_skip(step, spec, params):
            self.report.skipped += 1
            log.debug(
                "step skipped, nothing to write",
                extra={"platform": self.recipe.key, "step": step.description},
            )
            return

        run = StepRun(
            page=self.page,
            params=params,
            captures=self.report.captures,
            timeout_ms=int(
                params.get("timeout_ms")
                or self.recipe.timeout_ms
                or self.settings.action_timeout_ms
            ),
        )

        log.debug(
            "step",
            extra={
                "platform": self.recipe.key,
                "phase": step.phase,
                "index": step.index,
                "action": step.action,
            },
        )

        try:
            await spec.handler(run)
        except PlatformError as exc:
            raise self._decorate(step, str(exc)) from exc
        except Exception as exc:
            raise self._decorate(step, _short(exc)) from exc

        self.report.executed += 1
        if step.submit:
            self.report.submitted = True

    def _should_skip(self, step: Step, spec: Any, params: dict[str, Any]) -> bool:
        """An optional step whose value rendered empty has nothing to do."""
        if not params.get("optional"):
            return False
        for name in ("value", "value_html", "path", "text"):
            if name in step.params:
                if str(params.get(name) or "").strip():
                    return False
                return True
        return False

    def _decorate(self, step: Step, reason: str) -> PlatformError:
        return PlatformError(
            f"{self.recipe.label} failed at {step.phase} step {step.index} "
            f"({step.description}): {reason}"
        )


def _short(exc: Exception) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0][:200] if lines else exc.__class__.__name__


def describe_emails(emails: list[EmailStep]) -> str:
    """One-line summary for logs and CLI output."""
    if not emails:
        return "no emails"
    parts = [f"#{e.order} {e.subject[:40]!r}" for e in emails]
    return f"{len(emails)} emails: " + ", ".join(parts)
