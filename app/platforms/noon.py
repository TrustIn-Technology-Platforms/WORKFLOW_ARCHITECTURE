"""noon.ai — create the role, set its sourcing up, then fill the campaign.

`platforms/noon.yaml` still owns the browser work: create the role, import the
team's shared template, fill the steps from the document. This driver runs
that recipe with one interruption. Right after the role exists — the step
that captures its id — it sets the role's search criteria from the same
document, the wizard behind `Start sourcing`, replayed through noon's own API
in [noon_sourcing](noon_sourcing.py). Then the recipe carries on into the
campaign editor.

**The order is noon's, not ours.** Since the portal's 2026-10 redesign a fresh
role shows nothing but `Start sourcing`; the `Review & Contact` card the
campaign editor lives behind only renders once the sourcing wizard has been
completed (seen live 2026-10-09). The recipe used to do the campaign first and
the criteria after, and every production run since the redesign died waiting
for a card that was never going to appear. So the sourcing half is no longer
optional for noon: a run that cannot set it up cannot save a campaign either,
and says so rather than leaving a bare role behind.

What the wizard reads is the document's `Client JD` section; when there is
none, the shared sourcing profile's composed spec stands in, and the raw
advert only when nothing was drafted at all — the advert is written to attract
applicants and softens the years, the stack and the location, which are the
things a search filters on (`ParsedDocument.search_jd`, D-024). The facts that
live on the Notion row rather than in any prose - location above all - are
stated in a preamble above it, along with the profile's similar titles, skill
tiers and target companies, and the same profile supplies the example
companies and the client description the wizard's third screen asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from app.platforms.noon_sourcing import (
    PORTAL,
    WizardBrief,
    as_list,
    client_description,
    role_title,
    targeting_preamble,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

    from app.config import Settings

log = get_logger(__name__)


@dataclass(slots=True)
class WizardInputs:
    """Everything the sourcing wizard is handed, built once from the document.

    Shared by the adapter and `python -m app.cli source`, which used to build
    this by hand each and had already diverged once (the CLI's copy lost the
    years line). `notes` are the lines worth telling the recruiter - which
    profile was used, the boolean string - and go on the row's detail.
    """

    job_description: str
    targeting: str
    brief: WizardBrief
    fallback_must_haves: list[str] | None = None
    origin: str = "advert"  # Client JD | composed spec | advert
    notes: list[str] = field(default_factory=list)


async def wizard_inputs(
    document: ParsedDocument, settings: "Settings", *, role_name: str = ""
) -> WizardInputs | None:
    """The wizard's inputs from the document and its shared sourcing profile.

    None when the document has no job description at all - neither a Client
    JD nor an advert - because then there is nothing to source on.
    """
    jd = document.job_description
    if not jd:
        return None
    # A document can carry a Client JD and no advert at all, so the empty
    # stand-in is what the rest of this reads - the same pattern the Loxo and
    # Juicebox adapters use.
    advert = document.advert or Advert(title="", body_text="", body_html="")

    # The shared profile (D-024), from the advert's title only - never
    # `role_name`: that is the document's filename, "Company - Role -
    # Location", whose leading segment is the company. `role_title` would hand
    # noon a company name to search for.
    from app.platforms.sourcing_profile import ensure_sourcing
    from app.platforms.targeting_ai import sourcing_location

    profile = await ensure_sourcing(
        document, settings, role_title=advert.title, location=advert.location or ""
    )
    notes: list[str] = []
    title = role_title(advert.title)

    if profile is None:
        notes.append(
            "no sourcing profile could be drafted (is ANTHROPIC_API_KEY set "
            "here?), so noon was set up from the document alone"
        )
        # The general advert deliberately, not `advert_for("noon")`: a section
        # written for a board is cut down for that board, and these are facts
        # about the role rather than copy. `enrich_advert` and `ensure_skills`
        # have already filled it from the row's columns by this point.
        targeting = targeting_preamble(
            title=advert.title,
            location=advert.location or "",
            employment_type=advert.employment_type or "",
            skills=advert.tags,
        )
        brief = WizardBrief(
            client_description=client_description(jd),
            titles=[title] if title else [],
            location=_places(advert.location),
        )
        return WizardInputs(
            job_description=jd,
            targeting=targeting,
            brief=brief,
            origin="Client JD" if document.client_jd else "advert",
            notes=notes,
        )

    # The preamble's location is where the CANDIDATE must be. The advert's
    # (filled from the row) is the posting's location, which is not the same
    # thing when the company sits elsewhere (Axle, 2026-09-07), so the Client
    # JD is asked first and the advert fills the gap.
    location = sourcing_location(profile.candidate_location, advert.location)
    # The whole brief, not the title alone: the similar titles, both skill
    # tiers and a shortlist of target companies, so noon's own extractor has
    # the same facts every other platform gets (D-024). Each list is a
    # shortlist: every line noon extracts here can become a starred
    # non-negotiable, and thirty of them would narrow the search to nobody
    # while looking like diligence.
    targeting = targeting_preamble(
        title=advert.title,
        similar_titles=profile.similar_titles,
        location=location,
        employment_type=advert.employment_type or "",
        skills=profile.must_have_skills[:12] or advert.tags,
        nice_to_have=profile.nice_to_have_skills[:12],
        companies=profile.companies[:12],
    )
    # What noon reads: the Client JD verbatim, else the profile's composed
    # spec - never the raw advert when a spec exists, because generate_params
    # builds the criteria from whatever this text is.
    search_jd = document.search_jd or jd
    years = None
    if profile.min_years is not None:
        high = profile.max_years if profile.max_years is not None else max(profile.min_years, 20)
        years = (profile.min_years, max(profile.min_years, high))
    brief = WizardBrief(
        example_companies=list(profile.companies[:12]),
        client_description=client_description(search_jd, stage=profile.stage),
        titles=list(dict.fromkeys(t for t in [title, *profile.similar_titles] if t)),
        years=years,
        location=_places(location),
    )
    notes.append(f"sourcing profile: {profile.summary}")
    if profile.boolean_search:
        notes.append(f"boolean search: {profile.boolean_search}")
    origin = (
        "Client JD" if document.client_jd
        else "composed spec" if profile.drafted_jd
        else "advert"
    )
    return WizardInputs(
        job_description=search_jd,
        targeting=targeting,
        brief=brief,
        fallback_must_haves=profile.as_must_haves(),
        origin=origin,
        notes=notes,
    )


def _places(location: str | None) -> list[str]:
    """"New York / Atlanta" (how `sourcing_location` joins places) -> a list,
    the shape `preferences.location` stores."""
    if not location:
        return []
    return as_list([part.strip() for part in str(location).split("/") if part.strip()])


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

        async def between(report: RunReport) -> None:
            await self._set_up_sourcing(page, document, row, report)

        # The recipe pauses right after it has captured the new role's id -
        # the role exists, nothing else does yet - and resumes on the campaign
        # once the wizard has run and the page has been reloaded to show it.
        return await engine.run(document, row, after_capture="role_id", interlude=between)

    async def _set_up_sourcing(
        self,
        page: "Page",
        document: ParsedDocument,
        row: NotionRow | None,
        report: RunReport,
    ) -> None:
        """The sourcing wizard, between creating the role and its campaign.

        Not optional any more, and not quiet when it fails: the campaign editor
        is only reachable past it, so a wizard that did not finish means no
        campaign, and the recruiter is told where the bare role is.
        """
        from app.platforms.noon_sourcing import set_up_sourcing

        role_id = report.captures.get("role_id", "")
        role_url = report.captures.get("post_url") or f"{PORTAL}?role={role_id}"
        where = (
            f"The role exists at {role_url} with no campaign: finish 'Start "
            "sourcing' in noon by hand and add the campaign there, or delete the "
            "role and re-run."
        )
        if not self.settings.criteria_enabled:
            raise PlatformError(
                "noon only opens a role's outreach editor once its sourcing is set "
                "up, so the sourcing step cannot be switched off for noon. Re-run "
                f"without --no-sourcing (CRITERIA_ENABLED=true). {where}"
            )

        inputs = await wizard_inputs(document, self.settings)
        if inputs is None:
            raise PlatformError(
                "the document has neither a Client JD section nor an advert, so "
                "noon's sourcing could not be set up - and without it noon will "
                f"not open the outreach editor. {where}"
            )
        report.warnings.extend(inputs.notes)

        emails = [e for e in document.emails if e.is_email]
        advert = document.advert or Advert(title="", body_text="", body_html="")
        role_name = _role_name(document.source_name, row, advert, emails)

        try:
            sourcing = await set_up_sourcing(
                page,
                role_id,
                role_name,
                inputs.job_description,
                source=self.settings.noon_sourcing_source,
                start_sourcing=self.settings.noon_start_sourcing,
                dry_run=self.dry_run,
                targeting=inputs.targeting,
                fallback_must_haves=inputs.fallback_must_haves,
                brief=inputs.brief,
                role_wait_seconds=self.settings.noon_role_wait_seconds,
            )
        except (PlatformError, AuthenticationRequired) as exc:
            log.warning(
                "noon sourcing not set up",
                extra={"role": role_id, "error": str(exc)[:200]},
            )
            raise PlatformError(
                f"noon's sourcing could not be set up ({exc}), and without it the "
                f"outreach campaign cannot be saved. {where}"
            ) from exc

        report.warnings.extend(sourcing.warnings)
        report.warnings.append(f"sourcing: {sourcing.summary}")

        # The wizard wrote through the API while the page sat on the fresh
        # role; a reload is what makes the stage cards - and with them the
        # `Review & Contact` card the recipe clicks next - appear.
        await page.goto(f"{PORTAL}?role={role_id}", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
