"""Wellfound — the YAML advert recipe, plus the delete and update it cannot express.

`platforms/wellfound.yaml` still owns the posting: the New Job Posting form,
filled and saved as a draft. This driver runs that recipe unchanged, keeps
what the run created for the ledger (the job's URL and the title it was
posted under), adds the delete - Wellfound's own `DestroyJobListing`,
sent from inside the tab ([wellfound_delete](wellfound_delete.py)) - and the
update: the listing the row already made, opened on its edit form and the
document's fields written over it (`Ready to Update`, D-031, 2026-11-05).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.config import Settings
from app.logging_conf import get_logger
from app.models import NotionRow, ParsedDocument, PlatformError
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RecipeEngine, RunReport, _StopRun, build_context
from app.platforms.recipe import Step

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

APP = "https://wellfound.com"
_JOB_URL = re.compile(r"wellfound\.com/recruit/jobs/(\d+)")


def find_listing(row: NotionRow | None, settings: Settings) -> tuple[str, str] | None:
    """The listing an update rewrites: `(job id, how it was found)`.

    The ledger first - the newest record of a post this row made, unless a
    delete has since cleared the platform - then a Wellfound job link in the
    row's `Post URL` column. Nothing else: the Jobs list has never been read
    by the automation, and guessing a listing by title on an account with
    fifty drafts would edit the wrong one.
    """
    if row is None:
        return None
    from app.ledger import Ledger
    from app.pipeline import _row_text

    entry = Ledger(settings.ledger_file).get(row.page_id)
    if entry and "wellfound" not in entry.deleted:
        for record in reversed(entry.records.get("wellfound", [])):
            match = _JOB_URL.search(record.get("job") or record.get("post_url") or "")
            if match:
                return match.group(1), "the listing this row's post made"
    text = _row_text(row, settings.prop_post_url) or ""
    match = _JOB_URL.search(text)
    if match:
        return match.group(1), f"the {settings.prop_post_url} column"
    return None


def update_steps(steps: list[Step], job_id: str) -> list[Step]:
    """The posting steps, re-aimed at an existing listing's edit form.

    The first `goto_until` opens `/recruit/jobs/<id>/edit` instead of the
    `/new` form; a step marked `on_update: skip` in the recipe is left out;
    the submit step clicks its `update_selector` when it names one. Text
    fields and single-value selects replace what is there, which is what
    makes the rest of the recipe reusable as written.
    """
    rewritten: list[Step] = []
    aimed = False
    for step in steps:
        if str(step.params.get("on_update") or "").lower() == "skip":
            continue
        params = dict(step.params)
        if not aimed and step.action in ("goto", "goto_until"):
            params["url"] = f"{{{{ base_url }}}}/recruit/jobs/{job_id}/edit"
            params["description"] = "the listing's edit form"
            aimed = True
        if step.submit and params.get("update_selector"):
            params["selector"] = params["update_selector"]
        params.pop("update_selector", None)
        rewritten.append(Step(action=step.action, params=params, index=step.index, phase=step.phase))
    return rewritten


class WellfoundAdapter(RecipeAdapter):
    supports_delete = True
    supports_update = True

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

    async def _update(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        """Write the document's advert over the listing this row already made.

        Title, description, position type, primary role, years and salary -
        the fields the document carries. Skills and location stay as posted
        (their widgets add rather than replace), and so do the visa and remote
        radios (the document never set them). A listing a recruiter has
        published since is left alone: an unattended run must not change what
        candidates can see, the same line the delete holds.
        """
        from app.platforms.wellfound_delete import read_job

        found = find_listing(row, self.settings)
        if found is None:
            raise PlatformError(
                "Wellfound: this row has no record of a listing to update - it was "
                "posted before the ledger existed, or its post failed. Put the "
                f"listing's link (wellfound.com/recruit/jobs/<id>) in the "
                f"{self.settings.prop_post_url} column, or post the row again."
            )
        job_id, how = found
        job = await read_job(page, job_id)
        if not job["exists"]:
            raise PlatformError(
                f"Wellfound: the listing this row made ({job_id}) is gone - it was "
                "deleted on Wellfound. Post the row again instead."
            )
        if job["published"]:
            raise PlatformError(
                f"Wellfound: the listing {job.get('title')!r} ({job_id}) has been "
                "published since it was posted, and an update would change the "
                "live advert. Unpublish it on Wellfound (its page offers Unpublish) "
                "and set the status again, or edit it there by hand."
            )

        report = RunReport()
        report.warnings.append(f"listing: {how} ({job_id})")
        log.info("wellfound update", extra={"job": job_id, "how": how, "title": job.get("title")})

        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        engine.report = report
        context = build_context(document, row, self.recipe.defaults, self.recipe.key)
        try:
            await engine.run_steps(update_steps(self.recipe.steps, job_id), context)
        except _StopRun:
            # The dry run stopped before Save; everything after it reads back
            # a save that never happened.
            pass
        report.submitted = not self.dry_run
        # The link for the row's Notes line goes in `sourcing_url`, not
        # `post_url`: a post URL on the result is recorded in the ledger as
        # something new to delete, and this listing is already there.
        report.sourcing_url = f"{APP}/recruit/jobs/{job_id}"
        report.captures.pop("post_url", None)
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
