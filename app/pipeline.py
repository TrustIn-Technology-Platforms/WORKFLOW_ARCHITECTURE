"""Row in, posts out, status written back.

The only place that touches Notion state. Adapters return results; this decides
what the row's final status is and writes it once, so a row posting to three
platforms cannot race three updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.documents import parser
from app.documents.docx_reader import read_blocks
from app.documents.fetcher import build_fetcher
from app.logging_conf import get_logger
from app.models import (
    NotionRow,
    Outcome,
    ParsedDocument,
    PipelineError,
    PostResult,
)
from app.notion.client import NotionClient
from app.platforms import BrowserRunner, get_adapter, load_recipes, resolve
from app.platforms.skills import ensure_skills, split_skills

log = get_logger(__name__)


@dataclass(slots=True)
class RowReport:
    row: NotionRow
    results: list[PostResult] = field(default_factory=list)
    error: str | None = None
    document: ParsedDocument | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(r.ok for r in self.results)

    @property
    def post_url(self) -> str | None:
        for result in self.results:
            if result.post_url:
                return result.post_url
        return None

    @property
    def post_urls_text(self) -> str | None:
        """Every platform's link, one per line, since a row can post to more
        than one. A single-URL column shows only `post_url`; a rich-text column
        holds them all and Notion linkifies each. `platform: url` per line."""
        lines = [f"{r.platform}: {r.post_url}" for r in self.results if r.post_url]
        if not lines:
            return None
        return lines[0].split(": ", 1)[1] if len(lines) == 1 else "\n".join(lines)


async def load_document(
    url_or_path: str, settings: Settings | None = None
) -> ParsedDocument:
    """Fetch (or read) a document and parse it. No Notion, no browser."""
    settings = settings or get_settings()

    local = Path(url_or_path)
    if local.exists():
        content = local.read_bytes()
        source_name = local.stem
        log.info("document read from disk", extra={"path": str(local)})
    else:
        fetched = await build_fetcher(settings).fetch(url_or_path)
        if fetched.kind != "docx":
            raise PipelineError(
                f"The link returned a {fetched.kind} file, and only .docx can be "
                "read today. Export the document as .docx and re-share it."
            )
        content = fetched.content
        # The share link carries the real filename ("Company - Role - Location.docx");
        # its stem is the sequence name every platform uses.
        source_name = Path(fetched.filename).stem if fetched.filename else ""

    document = parser.parse_document(read_blocks(content))
    document.source_name = source_name.strip()
    return document


def enrich_advert(
    document: ParsedDocument,
    row: NotionRow | None,
    settings: Settings | None = None,
) -> list[str]:
    """Fill empty advert fields from the row's columns. Returns what was filled.

    The document wins when it carries the value; the row only fills gaps. This
    is orchestrator work by design - the parser knows nothing about Notion and
    the recipes should not each re-implement "column, else document".
    """
    if row is None or document.advert is None:
        return []
    settings = settings or get_settings()
    advert = document.advert
    filled: list[str] = []
    for attr, column in (
        ("location", settings.prop_location),
        ("salary", settings.prop_salary),
        ("employment_type", settings.prop_employment_type),
    ):
        if getattr(advert, attr):
            continue
        value = _row_text(row, column)
        if value:
            setattr(advert, attr, value)
            filled.append(f"{attr} <- {column}")

    # Skills are a list, so they take the same "column fills a gap" rule but a
    # different reader. A recruiter naming the stack beats anything inferred
    # from the prose, which is why the column is consulted before Claude is.
    if not advert.tags:
        value = _row_text(row, settings.prop_skills)
        if value:
            advert.tags = split_skills(value)
            filled.append(f"tags <- {settings.prop_skills}")

    if filled:
        log.info("advert enriched from row", extra={"filled": filled})
    return filled


def _row_text(row: NotionRow, column: str) -> str | None:
    """`property_text` with the same loose name match the Notion client uses."""
    exact = row.property_text(column)
    if exact:
        return exact.strip() or None
    wanted = _loose(column)
    for name in row.raw_properties:
        if _loose(name) == wanted:
            text = row.property_text(name)
            return (text or "").strip() or None
    return None


def _loose(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


async def post_document(
    document: ParsedDocument,
    platforms: list[str],
    row: NotionRow | None = None,
    settings: Settings | None = None,
    dry_run: bool | None = None,
) -> list[PostResult]:
    """Run each platform in turn against one parsed document.

    Sequential on purpose: a single row should produce one coherent outcome, and
    two browser contexts driving the same account concurrently is a good way to
    have a platform log both of them out.
    """
    settings = settings or get_settings()
    recipes = load_recipes(settings)
    results: list[PostResult] = []
    enrich_advert(document, row, settings)
    # Once per row, not once per platform: the draft costs an API call and every
    # advert-kind recipe wants the same answer.
    await ensure_skills(document, settings)

    async with BrowserRunner(settings) as runner:
        for name in platforms:
            # A Platforms option with no recipe is a tag, not a destination:
            # the database carries `TrustIn` alongside the four real ones. That
            # must not fail a row whose actual platforms all posted, so it is
            # recorded as skipped and named.
            if resolve(name, recipes) is None:
                known = ", ".join(sorted(recipes)) or "none"
                results.append(
                    PostResult(
                        platform=name,
                        outcome=Outcome.SKIPPED,
                        detail=f"no recipe for {name!r} - nothing to post to (known: {known})",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                log.info("platform skipped - no recipe", extra={"platform": name})
                continue

            adapter = get_adapter(
                name, recipes=recipes, runner=runner, settings=settings, dry_run=dry_run
            )
            try:
                result = await adapter.post(document, row)
            except PipelineError as exc:
                result = PostResult(
                    platform=name,
                    outcome=Outcome.FAILED,
                    detail=str(exc),
                    artifacts=list(getattr(exc, "artifacts", []) or []),
                )
                log.error(
                    "platform failed",
                    extra={"platform": name, "error": str(exc)},
                )
            results.append(result)

    return results


async def process_row(
    row: NotionRow,
    client: NotionClient,
    settings: Settings | None = None,
    dry_run: bool | None = None,
) -> RowReport:
    """Claim a row, do the work, write the outcome back."""
    settings = settings or get_settings()
    dry_run = settings.dry_run if dry_run is None else dry_run
    report = RowReport(row=row)

    log.info(
        "row started",
        extra={
            "page_id": row.page_id,
            "title": row.title,
            "platforms": row.platforms,
            "dry_run": dry_run,
        },
    )

    if not dry_run:
        await client.mark_posting(row.page_id)

    try:
        if not row.document_url:
            raise PipelineError("No document URL was provided on the row.")
        if not row.platforms:
            raise PipelineError(
                "No platforms are set on the row, so there is nowhere to post."
            )

        document = await load_document(row.document_url, settings)
        report.document = document

        if document.is_empty:
            raise PipelineError(
                "The document produced no advert and no emails. Check that its "
                "headings mark the advert and each email step."
            )
        for warning in document.warnings:
            log.warning("parse warning", extra={"page_id": row.page_id, "warning": warning})

        report.results = await post_document(
            document, row.platforms, row=row, settings=settings, dry_run=dry_run
        )

        failures = [r for r in report.results if r.outcome is Outcome.FAILED]
        if failures:
            report.error = "; ".join(
                f"{r.platform}: {r.detail or 'failed'}" for r in failures
            )

    except PipelineError as exc:
        report.error = str(exc)
    except Exception as exc:  # anything here is a bug, not an expected failure
        log.exception("unexpected error", extra={"page_id": row.page_id})
        report.error = f"Unexpected error: {exc.__class__.__name__}. Check the logs."

    await _write_back(report, client, dry_run)
    return report


async def _write_back(report: RowReport, client: NotionClient, dry_run: bool) -> None:
    if dry_run:
        log.info(
            "dry run - Notion not updated",
            extra={"page_id": report.row.page_id, "would_be": "ok" if report.ok else "failed"},
        )
        return

    if report.error:
        detail = report.error
        if report.document and report.document.warnings:
            detail += " | parse warnings: " + "; ".join(report.document.warnings[:3])
        await client.mark_failed(report.row.page_id, detail)
        log.warning("row failed", extra={"page_id": report.row.page_id, "error": detail})
        return

    warnings = "; ".join(report.document.warnings[:3]) if report.document else ""
    await client.mark_posted(report.row.page_id, report.post_urls_text, warnings or None)
    log.info(
        "row posted",
        extra={"page_id": report.row.page_id, "post_url": report.post_urls_text},
    )


async def run_once(
    settings: Settings | None = None,
    limit: int | None = None,
    dry_run: bool | None = None,
) -> list[RowReport]:
    """One poll: claim ready rows, process each, write back."""
    settings = settings or get_settings()
    settings.ensure_dirs()

    reports: list[RowReport] = []
    async with NotionClient(settings) as client:
        rows = await client.query_ready_rows(limit)
        if not rows:
            log.info("nothing to do")
            return reports
        for row in rows:
            reports.append(await process_row(row, client, settings, dry_run))

    posted = sum(1 for r in reports if r.ok)
    log.info("poll finished", extra={"rows": len(reports), "posted": posted})
    return reports


async def run_page(
    page_id: str,
    settings: Settings | None = None,
    dry_run: bool | None = None,
) -> RowReport:
    """Process exactly one row, whatever its status."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    async with NotionClient(settings) as client:
        row = await client.get_row(page_id)
        return await process_row(row, client, settings, dry_run)
