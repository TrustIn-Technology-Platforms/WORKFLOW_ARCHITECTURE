"""Row in, posts out, status written back.

The only place that touches Notion state. Adapters return results; this decides
what the row's final status is and writes it once, so a row posting to three
platforms cannot race three updates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.documents import parser
from app.documents.docx_reader import read_blocks
from app.documents.fetcher import build_fetcher
from app.logging_conf import get_logger
from app.ledger import Ledger, LedgerEntry
from app.models import (
    DeleteResult,
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
    if row is None:
        return []
    settings = settings or get_settings()
    # Every advert, not just the general one. A board advert inherits from the
    # general advert at *parse* time - before the row is anywhere near the
    # document - so a gap filled here on the general advert alone never reached
    # it, and Wellfound failed for want of a location the column plainly held
    # (2026-09-01, the first row posted with a `Wellfound` section).
    adverts = [
        a for a in (document.advert, *document.platform_adverts.values())
        if a is not None
    ]
    if not adverts:
        return []

    filled: list[str] = []
    for attr, column in (
        ("location", settings.prop_location),
        ("salary", settings.prop_salary),
        ("employment_type", settings.prop_employment_type),
    ):
        value: str | None = None
        for advert in adverts:
            if getattr(advert, attr):
                continue
            value = value if value is not None else (_row_text(row, column) or "")
            if value:
                setattr(advert, attr, value)
        if value:
            filled.append(f"{attr} <- {column}")

    # Skills are a list, so they take the same "column fills a gap" rule but a
    # different reader. A recruiter naming the stack beats anything inferred
    # from the prose, which is why the column is consulted before Claude is.
    tags: list[str] | None = None
    for advert in adverts:
        if advert.tags:
            continue
        if tags is None:
            tags = split_skills(_row_text(row, settings.prop_skills) or "")
        if tags:
            advert.tags = list(tags)
    if tags:
        filled.append(f"tags <- {settings.prop_skills}")

    if filled:
        log.info("advert enriched from row", extra={"filled": filled})
    return filled


def _row_multi(row: NotionRow, column: str) -> list[str]:
    """A multi-select column's option names, matched loosely like `_row_text`."""
    from app.notion.schema import multi_select_names

    wanted = _loose(column)
    for name, value in (row.raw_properties or {}).items():
        if _loose(name) == wanted:
            return multi_select_names(value)
    return []


async def progress_reset(client: NotionClient, page_id: str, posted: list[str]) -> None:
    """Clear `Failed On` as a run starts; what is already up stays listed."""
    try:
        await client.set_platform_columns(page_id, posted=posted, failed=[])
    except Exception as exc:  # noqa: BLE001 - optional columns, see `progress`
        log.warning("could not reset the platform columns",
                    extra={"page_id": page_id, "error": str(exc)[:200]})


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
    on_result: Callable[[PostResult], Awaitable[None]] | None = None,
) -> list[PostResult]:
    """Run each platform in turn against one parsed document.

    `on_result` is told about each platform as it finishes - the Notion door
    uses it to fill `Posted On` / `Failed On` while the run is still going.

    Sequential on purpose: a single row should produce one coherent outcome, and
    two browser contexts driving the same account concurrently is a good way to
    have a platform log both of them out.
    """
    settings = settings or get_settings()
    recipes = load_recipes(settings)
    results: list[PostResult] = []
    enrich_advert(document, row, settings)

    async def report(result: PostResult) -> None:
        results.append(result)
        if on_result is not None:
            await on_result(result)
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
                await report(
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
            await report(result)

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

    # Platforms already up from an earlier run of this row. A row set back to
    # Ready to Post after one platform failed must post only that one: until
    # 2026-10-09 it posted all four again, which is how duplicate noon roles
    # and Loxo campaigns came about, and why nobody dared re-run a row.
    already = {p.lower() for p in _row_multi(row, settings.prop_posted_on)}
    posted_on: list[str] = sorted(already)
    failed_on: list[str] = []

    async def progress(result: PostResult) -> None:
        if result.outcome is Outcome.POSTED:
            posted_on.append(result.platform.lower())
        elif result.outcome is Outcome.FAILED:
            failed_on.append(result.platform.lower())
        else:
            return
        try:
            await client.set_platform_columns(
                row.page_id, posted=posted_on, failed=failed_on
            )
        except Exception as exc:  # noqa: BLE001 - a progress note must never
            # fail a row whose posts are happening; the final write-back
            # carries the same facts.
            log.warning("could not update the platform columns",
                        extra={"page_id": row.page_id, "error": str(exc)[:200]})

    if not dry_run:
        await client.mark_posting(row.page_id)
        await progress_reset(client, row.page_id, posted_on)

    try:
        if not row.document_url:
            raise PipelineError("No document URL was provided on the row.")
        if not row.platforms:
            raise PipelineError(
                "No platforms are set on the row, so there is nowhere to post."
            )
        to_run = [p for p in row.platforms if p.lower() not in already]
        if not to_run:
            raise PipelineError(
                "Every platform on this row is already listed under "
                f"{settings.prop_posted_on}, so there was nothing to post. To post "
                "one again, remove it from that column first (or set the row to "
                f"{settings.status_delete} to take everything down)."
            )
        if len(to_run) < len(row.platforms):
            skipped = sorted(p for p in row.platforms if p.lower() in already)
            log.info("platforms already posted - skipped",
                     extra={"page_id": row.page_id, "platforms": skipped})

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
            document, to_run, row=row, settings=settings, dry_run=dry_run,
            on_result=None if dry_run else progress,
        )
        if not dry_run:
            _record_posts(row, report.results, settings)

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

    try:
        await _write_back(report, client, dry_run)
    except Exception as exc:  # noqa: BLE001 - the posts happened; only the
        # final update did not. Left as it was, this row sits on `Posting`
        # until the sweep blames a restart and tells a recruiter to re-run
        # it - posting everything a second time (found in review,
        # 2026-09-03). Say what really happened instead.
        log.error(
            "write-back failed",
            extra={"page_id": row.page_id, "error": str(exc)[:200],
                   "post_url": report.post_urls_text},
        )
        if not dry_run and not report.error:
            try:
                await client.mark_failed(
                    row.page_id,
                    "Every platform posted, but Notion rejected the final "
                    f"update ({exc}). Do NOT re-run - the posts already "
                    f"exist: {report.post_urls_text or 'see the log'}. Set "
                    "this row to Posted by hand.",
                )
            except Exception:  # noqa: BLE001 - Notion is unreachable; the
                # sweep is the right owner from here.
                log.exception("could not record the write-back failure",
                              extra={"page_id": row.page_id})
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

    # The platforms' own notes go on the row too: which search was built, what
    # a taxonomy refused, a stage Claude had to infer. Until 2026-09-03 only
    # parse warnings were written, so a Loxo run that refused nineteen chips
    # showed a recruiter nothing but "Posted".
    # A platform the row tags but has no recipe for (`TrustIn`) is skipped
    # by design; saying so on every such row is noise that reads as a
    # problem. Only what a real destination reported is worth writing.
    notes = [
        f"{r.platform}: {r.detail}"
        for r in report.results
        if r.detail and r.outcome is not Outcome.SKIPPED
    ]
    if report.document and report.document.warnings:
        notes.append("parse: " + "; ".join(report.document.warnings[:3]))
    detail = " | ".join(notes)[:1800]
    await client.mark_posted(report.row.page_id, report.post_urls_text, detail or None)
    log.info(
        "row posted",
        extra={"page_id": report.row.page_id, "post_url": report.post_urls_text},
    )


def _record_posts(row: NotionRow, results: list[PostResult], settings: Settings) -> None:
    """Keep what each platform created, so the row can be deleted later.

    Failed rows too: a row that failed on one platform still made something on
    the others. A ledger that cannot be written is logged loudly and does not
    fail a row whose posts all happened.
    """
    records = {r.platform: r.records for r in results if r.records}
    try:
        Ledger(settings.ledger_file).record_post(row.page_id, row.title, records)
    except Exception:  # noqa: BLE001 - see above
        log.exception(
            "could not record the row's posts - deleting it later will need a "
            "person", extra={"page_id": row.page_id, "platforms": sorted(records)},
        )


@dataclass(slots=True)
class DeleteReport:
    page_id: str
    title: str
    results: list[DeleteResult] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(r.ok for r in self.results)

    @property
    def detail(self) -> str:
        return " | ".join(f"{r.platform}: {r.detail or r.outcome.value}" for r in self.results)


def _records_from_post_url(
    url: str | None, platforms: list[str]
) -> dict[str, list[dict[str, str]]]:
    """The one link Notion kept, for a row posted before the ledger existed.

    It names one platform by its host, and only as a link - each platform's
    delete decides whether a link is enough to act on.
    """
    from urllib.parse import urlparse

    host = (urlparse(url or "").hostname or "").lower()
    for platform in platforms:
        if host and platform.lower() in host:
            return {platform.lower(): [{"post_url": url or ""}]}
    return {}


async def delete_records(
    page_id: str,
    title: str,
    platforms: list[str],
    settings: Settings,
    *,
    post_url: str | None = None,
    dry_run: bool = False,
    only_named: bool = False,
    on_result: Callable[[DeleteResult], Awaitable[None]] | None = None,
) -> DeleteReport:
    """Delete what the row created on each platform, one platform at a time.

    What to delete comes from the ledger; the row's `Post URL` fills in for a
    platform the ledger has nothing on. A platform already deleted by an
    earlier attempt is not visited again, so setting a half-deleted row back
    to Delete finishes the job rather than repeating it.

    `only_named` limits the delete to the `platforms` given. Without it, every
    ledger record joins in - the Notion door's behaviour, where Delete means
    the whole row even if the multi-select changed since posting. The direct
    door asks per platform, and "take it off noon" must not also take it off
    Juicebox.
    """
    ledger = Ledger(settings.ledger_file)
    entry: LedgerEntry | None = ledger.get(page_id)
    report = DeleteReport(page_id=page_id, title=title)

    records: dict[str, list[dict[str, str]]] = {}
    if entry is not None:
        records.update({p: r for p, r in entry.records.items() if p not in entry.deleted})
    for platform, recs in _records_from_post_url(post_url, platforms).items():
        if entry is None or platform not in entry.records:
            records.setdefault(platform, recs)

    named = list(dict.fromkeys(p.lower() for p in platforms))
    if only_named:
        records = {p: r for p, r in records.items() if p in named}

    recipes = load_recipes(settings)
    already = set(entry.deleted) if entry else set()
    order = named if only_named else list(dict.fromkeys([*named, *records]))
    async with BrowserRunner(settings) as runner:
        for name in order:
            if resolve(name, recipes) is None:
                continue  # a tag such as `TrustIn`, not a destination
            if name in already and name not in records:
                continue
            if entry is not None and name not in entry.records and name not in records:
                # A tracked row whose post here recorded nothing - it failed
                # before creating anything the adapter could name.
                report.results.append(DeleteResult(
                    platform=name, outcome=Outcome.SKIPPED,
                    detail=f"no post of this row recorded anything on {name}; "
                    "if a failed run left something there, delete it by hand",
                ))
                continue
            adapter = get_adapter(
                name, recipes=recipes, runner=runner, settings=settings, dry_run=dry_run
            )
            try:
                result = await adapter.delete(records.get(name, []), row_title=title)
            except PipelineError as exc:
                result = DeleteResult(platform=name, outcome=Outcome.FAILED, detail=str(exc))
            report.results.append(result)
            if on_result is not None:
                await on_result(result)
            log.info(
                "platform delete",
                extra={"page_id": page_id, "platform": name, "outcome": result.outcome.value},
            )

    if not dry_run and entry is not None:
        ledger.record_delete(
            page_id,
            {
                r.platform: (r.outcome is Outcome.DELETED, r.detail or r.outcome.value)
                for r in report.results
                if r.platform in entry.records
            },
        )
    failures = [r for r in report.results if not r.ok]
    if failures:
        report.error = "; ".join(f"{r.platform}: {r.detail or 'failed'}" for r in failures)
    return report


async def delete_row(
    row: NotionRow,
    client: NotionClient,
    settings: Settings | None = None,
    dry_run: bool | None = None,
) -> DeleteReport:
    """A row set to Delete: claim it, delete everywhere, write the outcome back."""
    settings = settings or get_settings()
    dry_run = settings.dry_run if dry_run is None else dry_run
    log.info(
        "row delete started",
        extra={"page_id": row.page_id, "title": row.title, "dry_run": dry_run},
    )
    # `Posted On` empties platform by platform as the deletes land, so a
    # delete that stops halfway shows exactly what is still up.
    posted_on = sorted({p.lower() for p in _row_multi(row, settings.prop_posted_on)})
    failed_on: list[str] = []

    async def progress(result: DeleteResult) -> None:
        name = result.platform.lower()
        if result.outcome is Outcome.DELETED:
            posted_on[:] = [p for p in posted_on if p != name]
        elif not result.ok:
            failed_on.append(name)
        else:
            return
        try:
            await client.set_platform_columns(row.page_id, posted=posted_on, failed=failed_on)
        except Exception as exc:  # noqa: BLE001 - optional columns
            log.warning("could not update the platform columns",
                        extra={"page_id": row.page_id, "error": str(exc)[:200]})

    if not dry_run:
        await client.mark_deleting(row.page_id)
        await progress_reset(client, row.page_id, posted_on)
    try:
        report = await delete_records(
            row.page_id,
            row.title,
            row.platforms,
            settings,
            post_url=_row_text(row, settings.prop_post_url),
            dry_run=dry_run,
            on_result=None if dry_run else progress,
        )
    except Exception as exc:  # noqa: BLE001 - the row must not be left on Deleting
        log.exception("row delete crashed", extra={"page_id": row.page_id})
        report = DeleteReport(page_id=row.page_id, title=row.title)
        report.error = f"Unexpected error: {exc.__class__.__name__}. Check the logs."
    if dry_run:
        log.info(
            "dry run - Notion not updated",
            extra={"page_id": row.page_id, "detail": report.detail},
        )
        return report
    if report.ok:
        await client.mark_deleted(row.page_id, report.detail or None)
        log.info("row deleted", extra={"page_id": row.page_id})
    else:
        await client.mark_failed(
            row.page_id,
            f"Delete incomplete - {report.error}. What was deleted stays deleted; "
            f"set the status back to {settings.status_delete} to try the rest again.",
        )
        log.warning("row delete incomplete", extra={"page_id": row.page_id, "error": report.error})
    return report


async def sweep_trashed_rows(
    client: NotionClient,
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
) -> list[DeleteReport]:
    """Delete the records of posted rows that have sat in Notion's trash for
    `delete_trashed_after_hours`.

    Notion sends nothing when a row is deleted, so each of the ledger's open
    rows is asked. A row first seen in the trash is only noted; it is deleted
    on a later sweep once the wait has passed, and a row restored in between
    is forgotten as trashed. A row Notion will not answer for is left alone.
    """
    settings = settings or get_settings()
    if not settings.delete_trashed_rows:
        return []
    ledger = Ledger(settings.ledger_file)
    wait = timedelta(hours=max(0.0, settings.delete_trashed_after_hours))
    reports: list[DeleteReport] = []
    for entry in ledger.open_entries():
        trashed = await client.page_in_trash(entry.page_id)
        if trashed is None:
            continue
        if not trashed:
            if entry.trashed_seen_at:
                ledger.set_trashed_seen(entry.page_id, False)
                log.info("row restored from trash", extra={"page_id": entry.page_id})
            continue
        seen = ledger.set_trashed_seen(entry.page_id, True)
        since = datetime.fromisoformat(seen) if seen else datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - since < wait:
            log.info(
                "row in trash - waiting before deleting",
                extra={"page_id": entry.page_id, "title": entry.title, "since": seen},
            )
            continue
        log.warning(
            "row deleted in Notion - deleting its posts",
            extra={"page_id": entry.page_id, "title": entry.title,
                   "platforms": entry.open_platforms},
        )
        report = await delete_records(
            entry.page_id, entry.title, list(entry.records), settings, dry_run=dry_run
        )
        reports.append(report)
        if report.ok:
            log.info("trashed row deleted", extra={"page_id": entry.page_id})
        else:
            log.error(
                "trashed row delete incomplete",
                extra={"page_id": entry.page_id, "error": (report.error or "")[:500]},
            )
    return reports


STUCK_MESSAGE = (
    "The run that claimed this row never wrote back - the posting service "
    "restarted mid-run (a deploy or a crash), or Notion rejected its final "
    "update - so the row was released. Parts may already be posted: check the "
    "platforms for a saved sequence or campaign before re-running. To reuse "
    "what exists, fill Juicebox Project / Loxo Job; then set the status back "
    "to Ready to Post."
)


STUCK_DELETE_MESSAGE = (
    "The run deleting this row never wrote back - the posting service "
    "restarted mid-run - so the row was released. Whatever was deleted stays "
    "deleted, and a second attempt skips it: set the status back to {status} "
    "to finish."
)


async def recover_stuck_rows(
    client: NotionClient,
    settings: Settings | None = None,
    *,
    older_than_minutes: int | None = None,
    dry_run: bool = False,
) -> list[NotionRow]:
    """Release rows a dead process left on `Posting`.

    A row is claimed as `Posting` and released by the write-back at the end of
    its run. When the process dies in between - a redeploy stopped the
    container eight minutes into the Axle row on 2026-09-03 - nothing releases
    it. A row untouched on `Posting` for longer than any live run takes is
    marked Failed with a note that says what happened and what to check. Rows
    without a last-edited stamp are left alone: not knowing is not evidence.
    """
    settings = settings or get_settings()
    minutes = settings.stuck_posting_minutes if older_than_minutes is None else older_than_minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = await client.query_rows_by_status(settings.status_posting)
    deleting = await client.query_rows_by_status_if_present(settings.status_deleting)
    stuck = [
        r for r in [*rows, *deleting]
        if r.last_edited is not None and r.last_edited <= cutoff
    ]
    for row in stuck:
        log.warning(
            "row stuck in posting",
            extra={
                "page_id": row.page_id,
                "title": row.title,
                "since": row.last_edited.isoformat() if row.last_edited else None,
                "dry_run": dry_run,
            },
        )
        if not dry_run:
            message = (
                STUCK_DELETE_MESSAGE.format(status=settings.status_delete)
                if (row.status or "").strip() == settings.status_deleting
                else STUCK_MESSAGE
            )
            await client.mark_failed(
                row.page_id,
                f"{message} (claimed {row.last_edited:%Y-%m-%d %H:%M} UTC, "
                f"untouched for over {minutes} minutes.)",
            )
    return stuck


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
        to_delete = await client.query_rows_by_status_if_present(settings.status_delete, limit)
        if not rows and not to_delete:
            log.info("nothing to do")
            return reports
        for row in rows:
            reports.append(await process_row(row, client, settings, dry_run))
        for row in to_delete:
            await delete_row(row, client, settings, dry_run)

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
