"""noon.ai — the YAML campaign flow, then the sourcing criteria.

`platforms/noon.yaml` still owns everything it always did: create the role,
import the team's shared template, fill the five steps from the document. This
driver runs that recipe unchanged and then, when sourcing is switched on, sets
the role's search criteria from the same document — the wizard behind
`Start sourcing`, replayed through noon's own API in
[noon_sourcing](noon_sourcing.py).

What it reads is the document's `Client JD` section; when there is none, the
shared sourcing profile's composed spec stands in, and the raw advert only
when nothing was drafted at all — the advert is written to attract applicants
and softens the years, the stack and the location, which are the things a
search filters on (`ParsedDocument.search_jd`, D-024). The facts that live on
the Notion row rather than in any prose - location above all - are stated in a
preamble above it, along with the profile's similar titles, skill tiers and
target companies, because `generate_params` is the call that writes the role's
`preferences` and it writes what it can read.

The two halves are deliberately independent. The campaign is what the recruiter
reviews and sends; the criteria are what noon uses to find people to send it to.
A document with nothing to source on still posts its campaign, and a sourcing
failure is reported as a warning on a run whose campaign was saved — losing the
campaign because the criteria did not take would be the wrong trade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import (
    Advert,
    AuthenticationRequired,
    NotionRow,
    ParsedDocument,
    PlatformError,
)
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RecipeEngine, RunReport, _role_name

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


class NoonAdapter(RecipeAdapter):
    supports_delete = True

    def _records(self, report: RunReport) -> dict[str, str]:
        # The post URL is the project page, which names no role; the role's
        # uuid is what the delete keys on, and the recipe captures it while the
        # role's own page is still on screen.
        records = super()._records(report)
        if report.captures.get("role_id"):
            records["role"] = report.captures["role_id"]
        return records

    async def _delete(
        self, page: "Page", records: list[dict[str, str]], *, row_title: str
    ) -> tuple[bool, list[str]]:
        """Stop each role's sourcing and delete it (noon_retire, proven 2026-09-08)."""
        from app.platforms.noon_retire import retire_role
        from app.platforms.noon_sourcing import RoleMissing, capture_session

        done, notes = True, []
        # Once for the whole delete: the token rides every call and does not
        # change between roles, where capturing per record reloads the portal
        # and waits out the token sniff again (~30s each).
        session = await capture_session(page)
        for record in records:
            role_id = record.get("role")
            if not role_id:
                done = False
                notes.append(
                    "a post left no role id to delete by - find the role in noon "
                    f"and delete it by hand ({record.get('post_url') or 'no link'})"
                )
                continue
            try:
                report = await retire_role(
                    page, role_id, delete=True, dry_run=self.dry_run, session=session
                )
            except RoleMissing:
                notes.append(f"noon role {role_id} was already gone")
                continue
            notes.append(report.summary)
            notes.extend(report.warnings)
            if not self.dry_run and not report.deleted:
                done = False
        return done, notes

    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> RunReport:
        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        report = await engine.run(document, row)

        if not self.settings.criteria_enabled:
            return report

        await self._set_criteria(page, document, row, report)
        return report

    async def _set_criteria(
        self,
        page: "Page",
        document: ParsedDocument,
        row: NotionRow | None,
        report: RunReport,
    ) -> None:
        """Tighten the role's sourcing criteria. Never fails the campaign."""
        from app.platforms.noon_sourcing import set_up_sourcing, targeting_preamble

        advert = document.advert
        jd = document.job_description
        if not jd:
            report.warnings.append(
                "the document has neither a Client JD section nor an advert, so "
                "noon's sourcing criteria were left as they are"
            )
            return

        role_id = report.captures.get("role_id")
        if not role_id:
            # A dry run stops before the role exists, so there is nothing to
            # configure - which is the honest outcome, not a failure.
            report.warnings.append(
                "dry run: no role was created, so the sourcing criteria were "
                "not set"
                if self.dry_run
                else "could not read the new role's id from the URL, so the "
                "sourcing criteria were not set"
            )
            return

        emails = [e for e in document.emails if e.is_email]
        # A document can now carry a Client JD and no advert at all, so the
        # empty stand-in is what the rest of this reads - the same pattern the
        # Loxo and Juicebox adapters use.
        advert = advert or Advert(title="", body_text="", body_html="")
        role_name = _role_name(document.source_name, row, advert, emails)
        # The facts a search filters on, above the description noon reads. They
        # live on the Notion row rather than in the prose, which is why the
        # location came back empty on every role until this existed.
        #
        # The general advert deliberately, not `advert_for("noon")`: a section
        # written for a board is cut down for that board, and these are facts
        # about the role rather than copy. `enrich_advert` and `ensure_skills`
        # have already filled it from the row's columns by this point.
        # The advert's title only, never `role_name`: that is the document's
        # filename, "Company - Role - Location", whose leading segment is the
        # company. `role_title` would hand noon a company name to search for.
        # The preamble's location is where the CANDIDATE must be. The advert's
        # (filled from the row) is the posting's location, which is not the
        # same thing when the company sits elsewhere (Axle, 2026-09-07), so the
        # Client JD is asked first and the advert fills the gap.
        from app.platforms.sourcing_profile import ensure_sourcing
        from app.platforms.targeting_ai import sourcing_location

        profile = await ensure_sourcing(
            document,
            self.settings,
            role_title=advert.title,
            location=advert.location or "",
        )
        fallback_must_haves: list[str] | None = None
        if profile is None:
            report.warnings.append(
                "no sourcing profile could be drafted (is ANTHROPIC_API_KEY "
                "set here?), so noon was set up from the document alone"
            )
            targeting = targeting_preamble(
                title=advert.title,
                location=advert.location or "",
                employment_type=advert.employment_type or "",
                skills=advert.tags,
            )
        else:
            # The whole brief, not the title alone: the similar titles, both
            # skill tiers and a shortlist of target companies, so noon's own
            # extractor has the same facts every other platform gets (D-024).
            # Each list is a shortlist: every line noon extracts here can
            # become a starred non-negotiable, and thirty of them would
            # narrow the search to nobody while looking like diligence.
            targeting = targeting_preamble(
                title=advert.title,
                similar_titles=profile.similar_titles,
                location=sourcing_location(
                    profile.candidate_location, advert.location
                ),
                employment_type=advert.employment_type or "",
                skills=profile.must_have_skills[:12] or advert.tags,
                nice_to_have=profile.nice_to_have_skills[:12],
                companies=profile.companies[:12],
            )
            fallback_must_haves = profile.as_must_haves()
            # What noon reads: the Client JD verbatim, else the profile's
            # composed spec - never the raw advert when a spec exists, because
            # generate_params builds the criteria from whatever this text is.
            jd = document.search_jd or jd
            report.warnings.append(f"sourcing profile: {profile.summary}")
            if profile.boolean_search:
                report.warnings.append(
                    f"boolean search: {profile.boolean_search}"
                )

        try:
            sourcing = await set_up_sourcing(
                page,
                role_id,
                role_name,
                jd,
                source=self.settings.noon_sourcing_source,
                start_sourcing=self.settings.noon_start_sourcing,
                dry_run=self.dry_run,
                targeting=targeting,
                fallback_must_haves=fallback_must_haves,
            )
        except (PlatformError, AuthenticationRequired) as exc:
            log.warning(
                "noon sourcing criteria not set",
                extra={"role": role_id, "error": str(exc)[:200]},
            )
            report.warnings.append(f"sourcing criteria not set: {exc}")
            return

        report.warnings.extend(sourcing.warnings)
        report.warnings.append(f"sourcing: {sourcing.summary}")
