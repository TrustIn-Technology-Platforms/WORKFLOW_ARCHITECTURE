"""noon.ai — the YAML campaign flow, then the sourcing criteria.

`platforms/noon.yaml` still owns everything it always did: create the role,
import the team's shared template, fill the five steps from the document. This
driver runs that recipe unchanged and then, when sourcing is switched on, sets
the role's search criteria from the same document's advert — the wizard behind
`Start sourcing`, replayed through noon's own API in
[noon_sourcing](noon_sourcing.py).

The two halves are deliberately independent. The campaign is what the recruiter
reviews and sends; the criteria are what noon uses to find people to send it to.
A document with no advert section still posts its campaign, and a sourcing
failure is reported as a warning on a run whose campaign was saved — losing the
campaign because the criteria did not take would be the wrong trade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger
from app.models import AuthenticationRequired, NotionRow, ParsedDocument, PlatformError
from app.platforms.adapter import RecipeAdapter
from app.platforms.engine import RecipeEngine, RunReport, _role_name

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


class NoonAdapter(RecipeAdapter):
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
        from app.platforms.noon_sourcing import set_up_sourcing

        advert = document.advert
        if advert is None or not advert.body_text.strip():
            report.warnings.append(
                "no advert section in the document, so noon's sourcing criteria "
                "were left as they are"
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
        role_name = _role_name(document.source_name, row, advert, emails)
        jd = f"{advert.title}\n\n{advert.body_text}".strip()

        try:
            sourcing = await set_up_sourcing(
                page,
                role_id,
                role_name,
                jd,
                source=self.settings.noon_sourcing_source,
                start_sourcing=self.settings.noon_start_sourcing,
                dry_run=self.dry_run,
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
