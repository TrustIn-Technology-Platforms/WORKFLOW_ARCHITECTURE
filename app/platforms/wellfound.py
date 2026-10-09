"""Wellfound — the YAML advert recipe, plus the delete it cannot express.

`platforms/wellfound.yaml` still owns the posting: the New Job Posting form,
filled and saved as a draft. This driver runs that recipe unchanged, keeps
what the run created for the ledger (the job's URL and the title it was
posted under), and adds the delete - Wellfound's own `DestroyJobListing`,
sent from inside the tab ([wellfound_delete](wellfound_delete.py)).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import NotionRow, ParsedDocument
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RecipeEngine, RunReport

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


class WellfoundAdapter(RecipeAdapter):
    supports_delete = True

    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        report = await engine.run(document, row)
        # Every run makes a new listing, so the URL alone says what to delete;
        # the title lets the delete read the page back and refuse an id that
        # no longer points at what was posted.
        if report.post_url:
            report.records.setdefault("job", report.post_url)
            advert = document.advert_for(self.recipe.key)
            if advert is not None and advert.title.strip():
                report.records.setdefault("job_title", advert.title.strip()[:150])
        return report

    async def _delete(
        self, page: "Page", records: list[dict[str, str]], *, row_title: str
    ) -> tuple[bool, list[str]]:
        """Delete each post's draft listing (wellfound_delete), after reading
        its page back; a listing published since is left alone."""
        from app.platforms.wellfound_delete import delete_job, job_id_from

        done, notes = True, []
        for record in records:
            if not job_id_from(record.get("job") or record.get("post_url")):
                done = False
                notes.append(
                    "a post left no job id to delete by - find the draft in Wellfound's "
                    f"Jobs list and delete it by hand ({record.get('post_url') or 'no link'})"
                )
                continue
            report = await delete_job(page, record, dry_run=self.dry_run)
            notes.append(report.summary)
            notes.extend(report.warnings)
            if report.accepted_shape and report.accepted_shape != "jobListingId":
                notes.append(f"Wellfound took the id as {report.accepted_shape!r}")
            if not self.dry_run and not report.complete:
                done = False
            elif self.dry_run and report.refused:
                done = False
        return done, notes
