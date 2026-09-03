"""Command line entry points.

    python -m app.cli login  noon
    python -m app.cli record noon --url https://www.noon.ai/portal --doc ./advert.docx
    python -m app.cli parse  ./advert.docx
    python -m app.cli post   noon --doc ./advert.docx --dry-run --headed
    python -m app.cli run --watch
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.logging_conf import configure_logging
from app.models import PipelineError

app = typer.Typer(add_completion=False, help="Document to platform automation.")
console = Console()


def _setup(verbose: bool = False) -> Any:
    settings = get_settings()
    configure_logging(
        "DEBUG" if verbose else settings.log_level, as_json=settings.log_json
    )
    settings.ensure_dirs()
    return settings


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# recipes and sessions
# ----------------------------------------------------------------------


@app.command("platforms")
def list_platforms() -> None:
    """List every recipe, whether it is enabled, and how old its session is."""
    from app.platforms import load_recipes
    from app.sessions.store import SessionStore

    settings = _setup()
    try:
        recipes = load_recipes(settings)
    except PipelineError as exc:
        _fail(str(exc))
        return

    if not recipes:
        console.print(
            f"[yellow]No recipes in {settings.platform_config_dir}/[/yellow]"
        )
        return

    sessions = SessionStore(settings)
    table = Table(title="Platforms")
    for column in ("Key", "Label", "Kind", "Enabled", "Steps", "Profile"):
        table.add_column(column)

    for recipe in recipes.values():
        info = (
            sessions.profile_info(recipe.key)
            if settings.use_browser_profile
            else sessions.info(recipe.key, recipe.session_file)
        )
        table.add_row(
            recipe.key,
            recipe.label,
            recipe.kind,
            "[green]yes[/green]" if recipe.enabled else "[yellow]no[/yellow]",
            str(len(recipe.all_steps)),
            info.summary if info.exists else "[red]none - run login[/red]",
        )
    console.print(table)


@app.command()
def login(
    platform: str = typer.Argument(..., help="Recipe key, e.g. noon"),
    timeout: int = typer.Option(900, help="Seconds to wait for the login."),
) -> None:
    """Open a visible browser, wait for you to log in, and save the session."""
    from app.platforms import capture_login, load_recipes, resolve

    settings = _setup()
    recipe = resolve(platform, load_recipes(settings))
    if recipe is None:
        _fail(f"No recipe named {platform!r}. Run: python -m app.cli platforms")
        return

    try:
        path = asyncio.run(capture_login(recipe, settings, timeout_seconds=timeout))
    except PipelineError as exc:
        _fail(str(exc))
        return

    console.print(f"\n[green]Logged in.[/green] Profile: {path}")
    console.print("[dim]That directory holds live credentials. It is git-ignored.[/dim]")
    console.print(
        f"\nNext: [cyan]python -m app.cli record {platform} "
        f"--url {recipe.login.url} --doc <file.docx>[/cyan]"
    )


@app.command()
def check() -> None:
    """Validate the configuration, the recipes, and Notion access."""
    from app.platforms import load_recipes

    settings = _setup()
    problems: list[str] = []

    console.print("\n[bold]Recipes[/bold]")
    try:
        recipes = load_recipes(settings)
        for recipe in recipes.values():
            state = "enabled" if recipe.enabled else "disabled"
            console.print(f"  [green]ok[/green]  {recipe.key} ({state})")
        if not recipes:
            problems.append(f"No recipes found in {settings.platform_config_dir}/")
    except PipelineError as exc:
        problems.append(str(exc))
        console.print(f"  [red]{exc}[/red]")

    console.print("\n[bold]Notion[/bold]")
    if not settings.notion_configured:
        console.print("  [yellow]skipped - NOTION_TOKEN / NOTION_DATABASE_ID unset[/yellow]")
    else:
        try:
            asyncio.run(_check_notion(settings))
            console.print("  [green]ok[/green]  database reachable")
        except PipelineError as exc:
            problems.append(str(exc))
            console.print(f"  [red]{exc}[/red]")

    # Three paths quietly depend on this key: Wellfound's drafted skills, Loxo's
    # empty Skill DNA buckets, and Juicebox's criteria (which cannot run at all
    # without it). A typo'd key otherwise surfaces mid-run as three different
    # degradations, so it is proven here - where a person is already looking.
    console.print("\n[bold]Criteria drafting[/bold]")
    if not settings.anthropic_api_key:
        console.print(
            "  [yellow]ANTHROPIC_API_KEY unset - Wellfound skills stay empty, "
            "Loxo's empty buckets are not filled, Juicebox criteria cannot be "
            "drafted[/yellow]"
        )
    else:
        try:
            asyncio.run(_check_anthropic(settings))
            console.print(
                f"  [green]ok[/green]  key accepted, model {settings.criteria_model}"
            )
        except PipelineError as exc:
            problems.append(str(exc))
            console.print(f"  [red]{exc}[/red]")

    if problems:
        console.print(f"\n[red]{len(problems)} problem(s).[/red]")
        raise typer.Exit(code=1)
    console.print("\n[green]All checks passed.[/green]")


async def _check_anthropic(settings: Any) -> None:
    """Prove the key and the model id without spending output tokens.

    `count_tokens` is an authenticated, free endpoint that also 404s on an
    unknown model - so one call verifies both of the two things that can be
    misconfigured here.
    """
    from anthropic import (
        AsyncAnthropic,
        AuthenticationError,
        NotFoundError,
        PermissionDeniedError,
    )

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        await client.messages.count_tokens(
            model=settings.criteria_model,
            messages=[{"role": "user", "content": "ping"}],
        )
    except AuthenticationError as exc:
        raise PipelineError(
            "Anthropic rejected ANTHROPIC_API_KEY. Re-copy it from "
            "console.anthropic.com - the criteria drafting will not run "
            "until it is valid."
        ) from exc
    except PermissionDeniedError as exc:
        raise PipelineError(
            "The ANTHROPIC_API_KEY is valid but not permitted to use "
            f"{settings.criteria_model!r} - check the key's workspace limits."
        ) from exc
    except NotFoundError as exc:
        raise PipelineError(
            f"CRITERIA_MODEL={settings.criteria_model!r} is not a model "
            "Anthropic recognises. Fix the setting - claude-opus-5 is the "
            "default."
        ) from exc
    except Exception as exc:
        raise PipelineError(f"Could not reach the Anthropic API: {exc}") from exc
    finally:
        await client.close()


async def _check_notion(settings: Any) -> None:
    from app.notion.client import NotionClient

    async with NotionClient(settings) as client:
        properties = await client.database_schema()
        wanted = {
            "final document": settings.prop_final_document,
            "status": settings.prop_status,
            "platforms": settings.prop_platforms,
            "post url": settings.prop_post_url,
            "posted at": settings.prop_posted_at,
            "error": settings.prop_error,
        }
        for label, name in wanted.items():
            resolved = await client.resolve_property(name)
            mark = "[green]ok[/green]" if resolved else "[yellow]missing[/yellow]"
            actual = f" -> {resolved[0]} ({resolved[1]})" if resolved else ""
            console.print(f"    {mark}  {label}: {name}{actual}")
        console.print(f"    [dim]{len(properties)} columns in the database[/dim]")


# ----------------------------------------------------------------------
# documents
# ----------------------------------------------------------------------


@app.command()
def parse(
    source: str = typer.Argument(..., help="A .docx path or a share link"),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of a summary."),
    show_html: bool = typer.Option(False, "--html", help="Include the HTML bodies."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch and parse a document. Touches nothing else - the fast feedback loop."""
    from app.pipeline import load_document

    _setup(verbose)
    try:
        document = asyncio.run(load_document(source))
    except PipelineError as exc:
        _fail(str(exc))
        return

    if as_json:
        console.print_json(json.dumps(_document_json(document, show_html), indent=2))
        return

    advert = document.advert
    console.print("\n[bold]Advert[/bold]")
    if advert is None:
        console.print("  [yellow]none found[/yellow]")
    else:
        console.print(f"  title            {advert.title}")
        for name in ("location", "salary", "employment_type", "category", "reference"):
            value = getattr(advert, name)
            if value:
                console.print(f"  {name:<16} {value}")
        for label, value in advert.fields.items():
            console.print(f"  [dim]{label:<16} {value}[/dim]")
        console.print(f"  body             {len(advert.body_text)} chars")
        if show_html:
            console.print(f"\n[dim]{advert.body_html[:2000]}[/dim]")

    # Which board gets its own copy. Printed even when there is none, because
    # "Wellfound posted the general advert" is invisible unless the absence of a
    # board section is stated somewhere a person looks.
    console.print("\n[bold]Board adverts[/bold]")
    if document.platform_adverts:
        for key, board in sorted(document.platform_adverts.items()):
            console.print(
                f"  [green]{key}[/green]  {len(board.body_text)} chars"
                f"   title: {board.title[:60]}"
            )
            preview = " ".join(board.body_text.split())[:110]
            console.print(f"      [dim]{preview}{'...' if len(preview) == 110 else ''}[/dim]")
    else:
        console.print(
            "  [yellow]none - every platform gets the general advert. A section "
            "headed 'Wellfound' would be posted there instead.[/yellow]"
        )

    console.print(f"\n[bold]Emails[/bold] ({len(document.emails)})")
    for email in document.emails:
        delay = f"  +{email.delay_days}d" if email.delay_days is not None else ""
        console.print(f"  [cyan]#{email.order}[/cyan]{delay}  {email.subject}")
        preview = " ".join(email.body_text.split())[:110]
        console.print(f"      [dim]{preview}{'...' if len(preview) == 110 else ''}[/dim]")
        if show_html:
            console.print(f"      [dim]{email.body_html[:800]}[/dim]")

    # What the sourcing platforms will read, and which section it came from.
    # An advert here is the fallback, not the intent - see docs/12.
    console.print("\n[bold]Client JD[/bold]")
    if document.client_jd:
        preview = " ".join(document.client_jd.split())[:180]
        console.print(f"  {len(document.client_jd)} chars")
        console.print(f"  [dim]{preview}{'...' if len(preview) == 180 else ''}[/dim]")
    else:
        console.print(
            "  [yellow]none - the criteria will be built from the advert, which "
            "is marketing copy[/yellow]"
        )

    if document.warnings:
        console.print("\n[bold yellow]Warnings[/bold yellow]")
        for warning in document.warnings:
            console.print(f"  - {warning}")

    if document.is_empty:
        _fail("\nNothing usable was parsed out of this document.")


def _document_json(document: Any, include_html: bool) -> dict[str, Any]:
    advert = document.advert
    payload: dict[str, Any] = {
        "advert": None
        if advert is None
        else {
            k: v
            for k, v in advert.as_context().items()
            if include_html or k != "body_html"
        },
        "emails": [
            {k: v for k, v in e.as_context().items() if include_html or k != "body_html"}
            for e in document.emails
        ],
        "client_jd": document.client_jd,
        "job_description_chars": len(document.job_description),
        "warnings": document.warnings,
    }
    return payload


# ----------------------------------------------------------------------
# posting
# ----------------------------------------------------------------------


@app.command()
def post(
    platform: str = typer.Argument(..., help="Recipe key, e.g. noon"),
    doc: str = typer.Option(..., "--doc", help="A .docx path or a share link"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Stop before submitting."),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    slow: int = typer.Option(0, "--slow", help="Milliseconds between actions."),
    set_: list[str] = typer.Option(
        [], "--set", metavar="COLUMN=VALUE",
        help="Stand in for a Notion column, e.g. --set 'Location=San Francisco'. "
             "Repeatable. Reaches recipes as row.property[...] and fills empty "
             "advert fields the same way a real row would.",
    ),
    sourcing: Optional[bool] = typer.Option(
        None, "--sourcing/--no-sourcing",
        help="Also set the platform's sourcing criteria from the advert. "
             "Defaults to CRITERIA_ENABLED.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one recipe against one document. No Notion involved."""
    settings = _setup(verbose)
    if headed:
        settings.headless = False
    if slow:
        settings.slow_mo_ms = slow
    if sourcing is not None:
        settings.criteria_enabled = sourcing

    row = _stand_in_row(set_) if set_ else None

    try:
        result = asyncio.run(_post(platform, doc, settings, dry_run, row))
    except PipelineError as exc:
        _fail(str(exc))
        return

    colour = {"posted": "green", "dry_run": "cyan", "skipped": "yellow"}.get(
        result.outcome.value, "red"
    )
    console.print(f"\n[{colour}]{result.outcome.value}[/{colour}]  {result.platform}")
    if result.post_url:
        console.print(f"  url      {result.post_url}")
    if result.detail:
        console.print(f"  detail   {result.detail}")
    for artifact in result.artifacts:
        console.print(f"  artifact {artifact}")
    if dry_run:
        console.print("\n[dim]Dry run - nothing was submitted. Use --live to post.[/dim]")


@app.command()
def source(
    role: str = typer.Option(
        ..., "--role",
        help="Role uuid, or the /portal/sourcing?role=... URL of an existing role.",
    ),
    doc: str = typer.Option(
        ..., "--doc",
        help="A .docx path or share link. Its Client JD section is the job "
             "description, or its advert when there is no such section.",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live",
        help="A dry run reads the document and shows the criteria, saving nothing.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Role name to give noon. Defaults to the document's."
    ),
    start: bool = typer.Option(
        True, "--start/--no-start",
        help="Send the final call, the one that sets noon searching.",
    ),
    set_: list[str] = typer.Option(
        [], "--set", metavar="COLUMN=VALUE",
        help="Stand in for a Notion column, e.g. --set 'Location=Manchester'. "
             "Repeatable. The location, employment type and skills a search "
             "filters on live on the row rather than in the document, so "
             "without this a run started from a file alone has none of them.",
    ),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Set a noon role's sourcing criteria and search filters from a document.

    Every requirement noon reads out of the job description becomes a must-have
    — nice-to-haves included — every criterion it generates is kept as a
    non-negotiable, and each clarifying question is answered with the strictest
    option offered. This is the tight-criteria setup done by hand until now; see
    docs/platforms/noon.md#the-sourcing-wizard.

    The criteria rank the pool; `preferences` decides the pool. Those come off
    the row's columns, so pass them with `--set` when running from a file:

        --set 'Location=Manchester' --set 'Employment Type=Permanent'
    """
    settings = _setup(verbose)
    if headed:
        settings.headless = False

    row = _stand_in_row(set_) if set_ else None

    try:
        report = asyncio.run(_source(role, doc, settings, dry_run, name, start, row))
    except PipelineError as exc:
        _fail(str(exc))
        return

    console.print(f"\n[bold]role[/bold]  {report.role_id}")

    # The filters, first, because they decide the pool the criteria then rank -
    # and because an empty location is the failure this run exists to catch.
    console.print("\n[bold]Search filters[/bold]")
    if report.location:
        console.print(f"  location   {report.location}")
    else:
        console.print("  location   [yellow]none - noon will search globally[/yellow]")
    if report.titles:
        console.print(f"  titles     {', '.join(report.titles)}")
    else:
        console.print("  titles     [yellow]none[/yellow]")

    console.print(f"\n[bold]Must-haves[/bold] ({len(report.must_haves)})")
    for line in report.must_haves:
        promoted = " [dim](was a nice-to-have)[/dim]" if line in report.promoted else ""
        console.print(f"  - {line}{promoted}")
    if report.non_negotiables:
        console.print(f"\n[bold]Non-negotiables[/bold] ({len(report.non_negotiables)})")
        for position, line in enumerate(report.non_negotiables, start=1):
            console.print(f"  {position}. {line}")
    if report.answers:
        console.print("\n[bold]Clarifying questions[/bold]")
        for question, answer in report.answers.items():
            shown = "[yellow]skipped[/yellow]" if answer == "SKIP" else answer
            console.print(f"  {question}\n    -> {shown}")
    for warning in report.warnings:
        console.print(f"\n[yellow]{warning}[/yellow]")
    if dry_run:
        console.print("\n[dim]Dry run - nothing was saved. Use --live to apply.[/dim]")
    elif report.started_sourcing:
        console.print("\n[green]noon is now sourcing against these criteria.[/green]")


@app.command("search-criteria")
def search_criteria(
    search: str = typer.Option(
        ..., "--search", help="Juicebox search URL, or its search_id."
    ),
    project: Optional[str] = typer.Option(
        None, "--project", help="Project id. Only needed when --search is a bare id."
    ),
    doc: Optional[str] = typer.Option(
        None, "--doc",
        help="A .docx whose advert the criteria come from. Defaults to the job "
             "description the search already carries.",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="A dry run shows the criteria, saving nothing."
    ),
    restore: Optional[str] = typer.Option(
        None, "--restore", help="Path to a backup JSON to put back, instead of rebuilding."
    ),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Rebuild a Juicebox search's criteria from a document's advert.

    Juicebox ranks criteria rather than splitting must-have from nice-to-have,
    so the list is built dealbreakers first, then the baseline, then
    disqualifiers as negative criteria — tightest checks where it weighs them
    most. See docs/platforms/juicebox.md.
    """
    settings = _setup(verbose)
    if headed:
        settings.headless = False

    try:
        report = asyncio.run(
            _search_criteria(search, project, doc, settings, dry_run, restore)
        )
    except PipelineError as exc:
        _fail(str(exc))
        return

    console.print(f"\n[bold]search[/bold] {report.search_url}")
    if report.before:
        console.print(f"\n[bold]Before[/bold] ({len(report.before)})")
        for position, line in enumerate(report.before, start=1):
            console.print(f"  {position}. [dim]{line}[/dim]")
    console.print(f"\n[bold]After[/bold] ({len(report.after)}) - most important first")
    for position, line in enumerate(report.after, start=1):
        console.print(f"  {position}. {line}")
    for warning in report.warnings:
        console.print(f"\n[yellow]{warning}[/yellow]")
    if dry_run:
        console.print("\n[dim]Dry run - nothing was saved. Use --live to apply.[/dim]")


async def _search_criteria(
    search: str,
    project: str | None,
    doc: str | None,
    settings: Any,
    dry_run: bool,
    restore: str | None = None,
) -> Any:
    from app.pipeline import load_document
    from app.platforms import BrowserRunner, SessionStore, load_recipes, resolve
    from app.platforms.browser import save_failure
    from app.platforms.juicebox_criteria import restore_criteria, set_criteria

    advert_text, role_name = "", ""
    if doc:
        document = await load_document(doc, settings)
        advert_text = document.job_description
        if not advert_text:
            raise PipelineError(
                f"{doc} has neither a Client JD section nor an advert, so there "
                "is nothing to build search criteria from."
            )
        role_name = document.source_name or (document.advert.title if document.advert else "")
        origin = "Client JD" if document.client_jd else "advert"
        console.print(f"[dim]{origin}: {len(advert_text)} chars from {doc}[/dim]")
    else:
        console.print("[dim]advert: the search's own job description[/dim]")

    recipe = resolve("juicebox", load_recipes(settings))
    if recipe is None:
        raise PipelineError("No recipe named 'juicebox'.")
    base = str(recipe.defaults.get("base_url", "https://app.juicebox.ai"))

    if search.startswith("http"):
        url = search
    elif project:
        url = f"{base}/project/{project}/search?search_id={search}"
    else:
        raise PipelineError("--search was a bare id, so --project is needed too.")

    sessions = SessionStore(settings)
    state = None
    if settings.use_browser_profile:
        sessions.require_profile(recipe.key, recipe.label)
    else:
        state = sessions.require(recipe.key, recipe.label, recipe.session_file)

    async with BrowserRunner(settings) as runner:
        opener = (
            runner.profile_context(
                recipe.key, trace_name="jb-criteria", channel=recipe.browser_channel
            )
            if settings.use_browser_profile
            else runner.context(storage_state=state, trace_name="jb-criteria")
        )
        async with opener as (context, page):
            try:
                if restore:
                    from app.platforms.juicebox_criteria import SearchCriteriaReport

                    put_back = await restore_criteria(page, url, restore)
                    return SearchCriteriaReport(
                        search_url=url, after=put_back, saved=True,
                        warnings=[f"restored {len(put_back)} criteria from {restore}"],
                    )
                return await set_criteria(
                    page,
                    url,
                    advert_text,
                    role_name=role_name,
                    settings=settings,
                    dry_run=dry_run,
                )
            except PipelineError:
                await save_failure(context, page, "juicebox-criteria-failed", settings)
                raise


@app.command("juicebox-sourcing")
def juicebox_sourcing(
    doc: str = typer.Option(
        ..., "--doc",
        help="A .docx path or share link. Its Client JD section is the job "
             "description, or its advert when there is no such section.",
    ),
    project: Optional[str] = typer.Option(
        None, "--project",
        help="URL of an existing Juicebox project to build the search in. "
             "Default: create one named after the document.",
    ),
    search: Optional[str] = typer.Option(
        None, "--search",
        help="URL of an existing search to set the filters on. Skips the "
             "project and the JD paste; only the filters are written.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Project name when creating one. Defaults to the document's."
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live",
        help="A dry run drafts the filters and shows them; no browser opens.",
    ),
    set_: list[str] = typer.Option(
        [], "--set", metavar="COLUMN=VALUE",
        help="Stand in for a Notion column, e.g. --set 'Location=NY, ATL'. "
             "Repeatable. The location a search filters on lives on the row, "
             "not in the document, so without it the search has no place filter.",
    ),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Set up a Juicebox sourcing search from a document: project, JD search, filters.

    The flow a recruiter does by hand - create the project, press Job
    description and paste the JD so Juicebox's AI builds the search, then add
    the job titles, location, skills and years its AI leaves thin - saved with
    Save Changes and run. Nothing contacts a candidate. See
    docs/platforms/juicebox.md#sourcing.
    """
    settings = _setup(verbose)
    if headed:
        settings.headless = False

    row = _stand_in_row(set_) if set_ else None

    try:
        report = asyncio.run(
            _juicebox_sourcing(doc, project, name, settings, dry_run, row, search=search)
        )
    except PipelineError as exc:
        _fail(str(exc))
        return

    if report is None:
        console.print(
            "\n[dim]Dry run - no browser was opened. Use --live to build the search.[/dim]"
        )
        return
    origin = "created" if report.project_created else "existing"
    console.print(f"\n[bold]project[/bold]  {report.project_url}  [dim]({origin})[/dim]")
    console.print(f"[bold]search[/bold]   {report.search_url}")
    for section, values in report.added.items():
        if values:
            console.print(f"  {section}: {', '.join(values)}")
    if report.stage_keys:
        console.print(f"  [dim]funding stages held: {', '.join(report.stage_keys)}[/dim]")
    for section, values in report.refused.items():
        if values:
            console.print(f"  [yellow]{section} refused: {', '.join(values)}[/yellow]")
    if report.saved:
        console.print("\n[green]Filters saved and the search run.[/green]")
    else:
        console.print("\n[red]Filters NOT saved.[/red]")


async def _juicebox_sourcing(
    doc: str,
    project: str | None,
    name: str | None,
    settings: Any,
    dry_run: bool,
    row: Any = None,
    search: str | None = None,
) -> Any:
    from datetime import datetime, timezone

    from app.models import Advert, PlatformError
    from app.pipeline import _row_text, enrich_advert, load_document
    from app.platforms import BrowserRunner, SessionStore, load_recipes, resolve
    from app.platforms.browser import save_failure
    from app.platforms.engine import _role_name
    from app.platforms.juicebox_sourcing import (
        is_search_url,
        set_up_sourcing,
        split_locations,
        stage_plan,
        years_span,
    )
    from app.platforms.targeting_ai import (
        configured,
        draft_companies,
        draft_targeting,
        stage_from_text,
    )

    if project and not project.startswith("http"):
        raise PipelineError(
            "--project should be the project's full URL "
            "(app.juicebox.ai/project/<id>/...)."
        )
    if search and not (search.startswith("http") and is_search_url(search)):
        raise PipelineError(
            "--search should be the search's full URL "
            "(app.juicebox.ai/project/<id>/search?search_id=...)."
        )
    if not configured(settings):
        raise PipelineError(
            "ANTHROPIC_API_KEY is not set, and the search's titles, skills and "
            "years are drafted from the JD. Set it, or fill the filters by hand."
        )

    document = await load_document(doc, settings)
    for filled in enrich_advert(document, row, settings):
        console.print(f"[dim]from --set: {filled}[/dim]")
    jd = document.job_description
    if not jd:
        raise PipelineError(
            f"{doc} has neither a Client JD section nor an advert, so there is "
            "no job description to paste into Juicebox."
        )
    origin = "Client JD" if document.client_jd else "advert"
    console.print(f"[dim]job description: {len(jd)} chars from the {origin} in {doc}[/dim]")

    advert = document.advert or Advert(title="", body_text="", body_html="")
    emails = sorted(document.emails, key=lambda e: e.order)
    project_name = (
        name or _role_name(document.source_name, row, advert, emails) or "New Project"
    )
    # A Client-JD-only document has no advert for --set to enrich, so the
    # column is read directly, the way the adapter reads a real row.
    location = advert.location or (_row_text(row, settings.prop_location) if row else None)

    targeting = await draft_targeting(jd, role_title=project_name, settings=settings)
    if not targeting.similar_titles and not targeting.skills:
        raise PipelineError(
            "No titles or skills could be drafted from this JD, so there are no "
            "filters to set. Check the Anthropic key with `python -m app.cli check`."
        )

    places = split_locations(location)
    console.print(f"\n[bold]project[/bold]    {project or project_name + '  (to be created)'}")
    console.print(f"[bold]titles[/bold]     {', '.join(targeting.similar_titles) or '-'}")
    console.print(f"[bold]skills[/bold]     {', '.join(targeting.skills) or '-'}")
    if places:
        console.print(f"[bold]location[/bold]   {', '.join(places)}")
    else:
        console.print("[bold]location[/bold]   [yellow]none - pass --set 'Location=...'[/yellow]")
    span = years_span(targeting.min_years, targeting.max_years)
    console.print(f"[bold]years[/bold]      {span or '-'}")

    # Same-stage companies and the stages from Seed up to the client's own.
    company = (document.source_name or "").split(" - ")[0].strip()
    stated = stage_from_text(jd, advert.body_text)
    drafted = await draft_companies(
        jd, company=company, stage=stated, location=location or "",
        role_title=project_name, limit=settings.sourcing_max_companies, settings=settings,
    )
    stage = stated or (drafted.stage if drafted.stage and drafted.stage != "Unknown" else None)
    basis = "stated in the document" if stated else ("inferred by Claude" if stage else "unknown")
    console.print(f"[bold]stage[/bold]      {stage or '-'}  [dim]({basis})[/dim]")
    plan = stage_plan(stage)
    console.print(f"[bold]stages[/bold]     {', '.join(plan) if plan else '[yellow]left as Juicebox set them[/yellow]'}")
    console.print(f"[bold]companies[/bold]  {', '.join(drafted.companies) or '[yellow]none drafted[/yellow]'}")
    if dry_run:
        return None

    recipe = resolve("juicebox", load_recipes(settings))
    if recipe is None:
        raise PipelineError("No recipe named 'juicebox'.")
    sessions = SessionStore(settings)
    state = None
    if settings.use_browser_profile:
        sessions.require_profile(recipe.key, recipe.label)
    else:
        state = sessions.require(recipe.key, recipe.label, recipe.session_file)

    async with BrowserRunner(settings) as runner:
        opener = (
            runner.profile_context(
                recipe.key, trace_name="jb-sourcing", channel=recipe.browser_channel
            )
            if settings.use_browser_profile
            else runner.context(storage_state=state, trace_name="jb-sourcing")
        )
        async with opener as (context, page):
            try:
                report = await set_up_sourcing(
                    page,
                    project_name=project_name,
                    project_url=project,
                    search_url=search,
                    jd=jd,
                    titles=targeting.similar_titles,
                    skills=targeting.skills,
                    location=location,
                    min_years=targeting.min_years,
                    max_years=targeting.max_years,
                    companies=drafted.companies,
                    stage=stage,
                )
                # The proof, kept: the reloaded filter editor as the run left it.
                # Not full_page - that blanks this app's virtualised view.
                shots = Path(settings.artifact_dir) / "jb-sourcing"
                shots.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                final = shots / f"{stamp}-filters-after-reload.png"
                try:
                    await page.screenshot(path=str(final))
                    console.print(f"[dim]screenshot: {final}[/dim]")
                except Exception:
                    pass
                return report
            except Exception as exc:
                # Playwright's own timeouts are not PipelineErrors, and the
                # person reading this needs the same screenshot either way.
                artifacts = await save_failure(
                    context, page, "juicebox-sourcing-failed", settings
                )
                if isinstance(exc, PipelineError):
                    raise
                first_line = (str(exc).strip().splitlines() or [""])[0][:200]
                raise PlatformError(
                    f"{exc.__class__.__name__}: {first_line}"
                    + (f" (see {artifacts[0]})" if artifacts else "")
                ) from exc


@app.command()
def criteria(
    job: str = typer.Option(..., "--job", help="Loxo job id, or the job URL."),
    doc: Optional[str] = typer.Option(
        None, "--doc",
        help="A .docx whose advert fills empty criteria. Defaults to the job's "
             "own description.",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="A dry run shows the criteria, writing nothing."
    ),
    marker: Optional[str] = typer.Option(
        None, "--marker", help="Line to prefix the description with, to label a test run."
    ),
    restore: Optional[str] = typer.Option(
        None, "--restore", help="Path to a backup JSON to put back, instead of writing criteria."
    ),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Tighten a Loxo job's candidate criteria — its Skill DNA.

    Every nice-to-have becomes a dealbreaker, and any bucket Loxo left empty is
    drafted from the advert. The job's description is backed up to ARTIFACT_DIR
    before anything is written; `--restore` puts one back.
    """
    settings = _setup(verbose)
    if headed:
        settings.headless = False

    try:
        report = asyncio.run(_criteria(job, doc, settings, dry_run, marker, restore))
    except PipelineError as exc:
        _fail(str(exc))
        return

    if restore:
        console.print(f"\n[green]restored[/green] job {report}")
        return

    console.print(f"\n[bold]job[/bold]      {report.job_id}")
    console.print(f"[bold]backup[/bold]   {report.backup_path}")
    if report.drafted:
        console.print(f"[bold]drafted[/bold]  {', '.join(report.drafted)} (from the advert)")
    console.print(f"[bold]promoted[/bold] {report.promoted} nice-to-have(s) into dealbreakers")
    console.print(f"\n{report.summary}")
    for warning in report.warnings:
        console.print(f"\n[yellow]{warning}[/yellow]")
    if dry_run:
        console.print("\n[dim]Dry run - nothing was written. Use --live to apply.[/dim]")


async def _criteria(
    job: str,
    doc: str | None,
    settings: Any,
    dry_run: bool,
    marker: str | None,
    restore: str | None,
) -> Any:
    from app.pipeline import load_document
    from app.platforms import BrowserRunner, SessionStore, load_recipes, resolve
    from app.platforms.browser import save_failure
    from app.platforms.loxo import job_id_from
    from app.platforms.loxo_sourcing import restore_description, set_criteria

    job_id = job_id_from(job)
    if not job_id:
        raise PipelineError(
            f"{job!r} does not contain a Loxo job id. Pass the job URL or its id."
        )

    advert_text, role_name = "", ""
    if doc:
        document = await load_document(doc, settings)
        advert_text = document.job_description
        if advert_text:
            role_name = document.source_name or (document.advert.title if document.advert else "")
            origin = "Client JD" if document.client_jd else "advert"
            console.print(f"[dim]{origin}: {len(advert_text)} chars from {doc}[/dim]")
        else:
            console.print(
                f"[yellow]{doc} has neither a Client JD nor an advert; using the "
                "job's own description[/yellow]"
            )

    recipe = resolve("loxo", load_recipes(settings))
    if recipe is None:
        raise PipelineError("No recipe named 'loxo'.")
    agency_id = str(recipe.defaults.get("agency_id", "28356"))

    sessions = SessionStore(settings)
    state = None
    if settings.use_browser_profile:
        sessions.require_profile(recipe.key, recipe.label)
    else:
        state = sessions.require(recipe.key, recipe.label, recipe.session_file)

    async with BrowserRunner(settings) as runner:
        opener = (
            runner.profile_context(
                recipe.key, trace_name="loxo-criteria", channel=recipe.browser_channel
            )
            if settings.use_browser_profile
            else runner.context(storage_state=state, trace_name="loxo-criteria")
        )
        async with opener as (context, page):
            try:
                if restore:
                    await restore_description(page, job_id, restore, agency_id=agency_id)
                    return job_id
                return await set_criteria(
                    page,
                    job_id,
                    advert_text,
                    role_name=role_name,
                    agency_id=agency_id,
                    settings=settings,
                    dry_run=dry_run,
                    marker=marker or "",
                )
            except PipelineError:
                await save_failure(context, page, "loxo-criteria-failed", settings)
                raise


@app.command("loxo-source")
def loxo_source(
    job: str = typer.Option(..., "--job", help="Loxo job id, or the job URL."),
    doc: str = typer.Option(
        ..., "--doc", help="The .docx (or share link) whose Client JD the filters come from."
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live",
        help="A dry run drafts and shows the filters, writing nothing.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Saved-search name. Defaults to '<role> - auto'."
    ),
    location: Optional[str] = typer.Option(
        None, "--location",
        help="The row's Location, for the company list. A file alone carries none.",
    ),
    headed: bool = typer.Option(False, "--headed", help="Watch the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Set a Loxo job's Source filters from a document, on their own.

    Titles, skills, years of experience and past companies - the filters that
    decide which profiles a search looks at, as opposed to the criteria that
    rank them. The posting run does this after the criteria; this command does
    only this, so a change can be tested without re-posting the campaign.
    """
    settings = _setup(verbose)
    if headed:
        settings.headless = False

    try:
        report = asyncio.run(_loxo_source(job, doc, settings, dry_run, name, location))
    except PipelineError as exc:
        _fail(str(exc))
        return

    if report is None:
        console.print("\n[dim]Dry run - nothing was written. Use --live to apply.[/dim]")
        return
    console.print(f"\n{report.summary}")
    for section, refused in (
        ("titles", report.refused_titles),
        ("skills", report.refused_skills),
        ("experience", report.missed_experience),
        ("companies", report.refused_companies),
    ):
        if refused:
            console.print(f"[yellow]refused {section}: {', '.join(refused)}[/yellow]")
    for warning in report.warnings:
        console.print(f"\n[yellow]{warning}[/yellow]")


async def _loxo_source(
    job: str,
    doc: str,
    settings: Any,
    dry_run: bool,
    name: str | None,
    location: str | None,
) -> Any:
    from app.pipeline import load_document
    from app.platforms import BrowserRunner, SessionStore, load_recipes, resolve
    from app.platforms.browser import save_failure
    from app.platforms.engine import _role_name
    from app.platforms.loxo import job_id_from
    from app.platforms.loxo_source import configure_source, experience_bands
    from app.platforms.targeting_ai import (
        draft_companies,
        draft_targeting,
        stage_from_text,
    )

    job_id = job_id_from(job)
    if not job_id:
        raise PipelineError(
            f"{job!r} does not contain a Loxo job id. Pass the job URL or its id."
        )
    if not settings.anthropic_api_key:
        raise PipelineError(
            "ANTHROPIC_API_KEY is not set, and the Source filters are drafted "
            "from the document. Set it and run again."
        )

    document = await load_document(doc, settings)
    jd = document.job_description
    if not jd:
        raise PipelineError(f"{doc} has neither a Client JD nor an advert to draft filters from.")
    advert = document.advert
    emails = sorted(document.emails, key=lambda e: e.order)
    role_name = name or _role_name(document.source_name, None, advert, emails)
    company = (document.source_name or "").split(" - ")[0].strip()
    where = location or (advert.location if advert else None) or ""
    origin = "Client JD" if document.client_jd else "advert"
    console.print(f"[dim]{origin}: {len(jd)} chars from {doc}[/dim]")

    targeting = await draft_targeting(jd, role_title=role_name, settings=settings)
    stated = stage_from_text(jd, advert.body_text if advert else "")
    companies = await draft_companies(
        jd, company=company, stage=stated, location=where,
        role_title=role_name, limit=settings.sourcing_max_companies, settings=settings,
    )
    bands = experience_bands(targeting.min_years, targeting.max_years)

    console.print(f"\n[bold]role[/bold]       {role_name}")
    console.print(f"[bold]company[/bold]    {company or '-'}   [bold]location[/bold] {where or '-'}")
    console.print(f"[bold]titles[/bold]     {', '.join(targeting.similar_titles) or '-'}")
    console.print(f"[bold]skills[/bold]     {', '.join(targeting.skills) or '-'}")
    years = (
        f"{targeting.min_years if targeting.min_years is not None else '?'}"
        f"-{targeting.max_years if targeting.max_years is not None else '+'}"
        if bands else "not stated"
    )
    console.print(f"[bold]experience[/bold] {years} -> bands {', '.join(bands) or '-'}")
    console.print(
        f"[bold]stage[/bold]      {companies.stage} ({companies.stage_basis})"
    )
    console.print(f"[bold]companies[/bold]  {', '.join(companies.companies) or '-'}")
    if companies.inferred:
        console.print(
            "[yellow]the document does not state the funding stage - the company "
            "list rests on Claude's inference; check it[/yellow]"
        )
    if not targeting.similar_titles and not targeting.skills:
        raise PipelineError("no titles or skills could be drafted, so there is nothing to write.")
    if dry_run:
        return None

    recipe = resolve("loxo", load_recipes(settings))
    if recipe is None:
        raise PipelineError("No recipe named 'loxo'.")
    sessions = SessionStore(settings)
    state = None
    if settings.use_browser_profile:
        sessions.require_profile(recipe.key, recipe.label)
    else:
        state = sessions.require(recipe.key, recipe.label, recipe.session_file)

    async with BrowserRunner(settings) as runner:
        opener = (
            runner.profile_context(
                recipe.key, trace_name="loxo-source", channel=recipe.browser_channel
            )
            if settings.use_browser_profile
            else runner.context(storage_state=state, trace_name="loxo-source")
        )
        async with opener as (context, page):
            try:
                report = await configure_source(
                    page,
                    job_id,
                    titles=targeting.similar_titles,
                    skills=targeting.skills,
                    years=(targeting.min_years, targeting.max_years),
                    companies=companies.companies,
                    search_name=f"{role_name} - auto"[:80],
                    base_url=recipe.defaults.get("base_url", "https://app.loxo.co"),
                    agency_id=str(recipe.defaults.get("agency_id", "28356")),
                )
                # The proof, kept: the Source panel as the run left it.
                from datetime import datetime, timezone

                shots = Path(settings.artifact_dir) / "loxo-source"
                shots.mkdir(parents=True, exist_ok=True)
                final = shots / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-source-saved.png"
                try:
                    await page.screenshot(path=str(final))
                    console.print(f"[dim]screenshot: {final}[/dim]")
                except Exception:
                    pass
                return report
            except PipelineError:
                await save_failure(context, page, "loxo-source-failed", settings)
                raise


def _role_uuid(value: str) -> str:
    """Accept a bare uuid or any noon URL that carries one."""
    import re as _re

    match = _re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value or "")
    if not match:
        _fail(f"{value!r} does not contain a role uuid.")
    return match.group(0)


async def _source(
    role: str,
    doc: str,
    settings: Any,
    dry_run: bool,
    name: str | None,
    start: bool,
    row: Any = None,
) -> Any:
    from app.pipeline import enrich_advert, load_document
    from app.platforms import BrowserRunner, SessionStore, load_recipes, resolve
    from app.platforms.engine import _role_name
    from app.models import Advert
    from app.platforms.noon_sourcing import set_up_sourcing, targeting_preamble
    from app.platforms.skills import ensure_skills

    role_id = _role_uuid(role)
    document = await load_document(doc, settings)
    # The same two steps the orchestrator runs before any platform sees the
    # document, in the same order: the row's columns fill the advert fields it
    # left empty, then the skills are drafted if no column named them. Without
    # this the search filters below are built from the document alone, and the
    # document is exactly what does not carry them.
    for filled in enrich_advert(document, row, settings):
        console.print(f"[dim]from --set: {filled}[/dim]")
    drafted = await ensure_skills(document, settings)
    if drafted:
        console.print(f"[dim]skills drafted: {', '.join(drafted)}[/dim]")
    advert = document.advert
    jd = document.job_description
    if not jd:
        raise PipelineError(
            f"{doc} has neither a Client JD section nor an advert, so there is no "
            "job description to give noon. Sourcing criteria come from those, not "
            "from the emails."
        )

    emails = [e for e in document.emails if e.is_email]
    # A Client JD is enough on its own, so the advert may be absent entirely.
    advert = advert or Advert(title="", body_text="", body_html="")
    role_name = name or _role_name(document.source_name, None, advert, emails)
    # The advert's title only - `role_name` is the filename, whose leading
    # segment is the company rather than the role.
    targeting = targeting_preamble(
        title=advert.title,
        location=advert.location or "",
        employment_type=advert.employment_type or "",
        skills=advert.tags,
    )
    origin = "Client JD" if document.client_jd else "advert"
    console.print(f"[dim]job description: {len(jd)} chars from the {origin} in {doc}[/dim]")
    if targeting:
        console.print(f"[dim]targeting:\n{targeting}[/dim]")
    else:
        console.print(
            "[yellow]no location, type or skills on this document, so noon will "
            "search globally[/yellow]"
        )

    recipe = resolve("noon", load_recipes(settings))
    if recipe is None:
        raise PipelineError("No recipe named 'noon'.")

    sessions = SessionStore(settings)
    state = None
    if settings.use_browser_profile:
        sessions.require_profile(recipe.key, recipe.label)
    else:
        state = sessions.require(recipe.key, recipe.label, recipe.session_file)

    async with BrowserRunner(settings) as runner:
        opener = (
            runner.profile_context(
                recipe.key, trace_name="noon-sourcing", channel=recipe.browser_channel
            )
            if settings.use_browser_profile
            else runner.context(storage_state=state, trace_name="noon-sourcing")
        )
        async with opener as (context, page):
            from app.platforms.browser import save_failure

            try:
                return await set_up_sourcing(
                    page,
                    role_id,
                    role_name,
                    jd,
                    source=settings.noon_sourcing_source,
                    start_sourcing=start,
                    dry_run=dry_run,
                    targeting=targeting,
                )
            except PipelineError:
                await save_failure(context, page, "noon-sourcing-failed", settings)
                raise


def _stand_in_row(pairs: list[str]) -> Any:
    """A NotionRow built from COLUMN=VALUE pairs, shaped like the API's rich_text.

    Lets `post --doc` exercise everything a real row would supply - advert
    fields on columns, `row.property[...]` in recipes - without Notion.
    """
    from app.models import NotionRow

    props: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            _fail(f"--set needs COLUMN=VALUE, got {pair!r}")
        name, _, value = pair.partition("=")
        props[name.strip()] = {
            "type": "rich_text",
            "rich_text": [{"type": "text", "plain_text": value.strip()}],
        }
    return NotionRow(
        page_id="local", title="", document_url=None, status=None,
        platforms=[], raw_properties=props,
    )


async def _post(
    platform: str, source: str, settings: Any, dry_run: bool, row: Any = None
) -> Any:
    from app.models import Outcome, PostResult
    from app.pipeline import enrich_advert, load_document
    from app.platforms import BrowserRunner, get_adapter
    from app.platforms.engine import describe_emails
    from app.platforms.skills import ensure_skills

    document = await load_document(source, settings)
    for filled in enrich_advert(document, row, settings):
        console.print(f"[dim]from --set: {filled}[/dim]")
    # Same order as the orchestrator: the column first, the advert second.
    drafted = await ensure_skills(document, settings)
    if drafted:
        console.print(f"[dim]skills drafted: {', '.join(drafted)}[/dim]")
    console.print(
        f"[dim]parsed: {describe_emails(document.emails)}"
        f"{', advert present' if document.advert else ', no advert'}[/dim]"
    )
    for warning in document.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")

    async with BrowserRunner(settings) as runner:
        adapter = get_adapter(
            platform, runner=runner, settings=settings, dry_run=dry_run
        )
        try:
            return await adapter.post(document, row)
        except PipelineError as exc:
            return PostResult(
                platform=platform,
                outcome=Outcome.FAILED,
                detail=str(exc),
                artifacts=list(getattr(exc, "artifacts", []) or []),
            )


@app.command()
def run(
    page: Optional[str] = typer.Option(None, "--page", help="One Notion page id or URL."),
    watch: bool = typer.Option(False, "--watch", help="Keep polling."),
    interval: int = typer.Option(60, help="Seconds between polls."),
    limit: Optional[int] = typer.Option(None, help="Maximum rows per poll."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop before submitting."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Process rows from Notion."""
    from app.pipeline import run_once, run_page

    _setup(verbose)
    try:
        if page:
            report = asyncio.run(run_page(page, dry_run=dry_run))
            _print_reports([report])
            return
        if not watch:
            _print_reports(asyncio.run(run_once(limit=limit, dry_run=dry_run)))
            return
        asyncio.run(_watch(interval, limit, dry_run))
    except PipelineError as exc:
        _fail(str(exc))
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")


async def _watch(interval: int, limit: int | None, dry_run: bool) -> None:
    from app.pipeline import run_once

    while True:
        reports = await run_once(limit=limit, dry_run=dry_run)
        if reports:
            _print_reports(reports)
        await asyncio.sleep(interval)


def _print_reports(reports: list[Any]) -> None:
    if not reports:
        console.print("[dim]nothing ready[/dim]")
        return
    table = Table(title="Rows")
    for column in ("Row", "Platforms", "Result", "Detail"):
        table.add_column(column, overflow="fold")
    for report in reports:
        outcomes = ", ".join(f"{r.platform}:{r.outcome.value}" for r in report.results)
        table.add_row(
            report.row.title[:44],
            ", ".join(report.row.platforms) or "-",
            "[red]failed[/red]" if report.error else "[green]ok[/green]",
            (report.error or outcomes)[:80],
        )
    console.print(table)


# ----------------------------------------------------------------------
# selector discovery
# ----------------------------------------------------------------------

_PROBE = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const label = (el) => {
    const id = el.getAttribute('id');
    if (id) {
      const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l) return l.innerText.trim();
    }
    const wrap = el.closest('label');
    if (wrap) return wrap.innerText.trim();
    return el.getAttribute('aria-label') || '';
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    role: el.getAttribute('role') || '',
    id: el.getAttribute('id') || '',
    name: el.getAttribute('name') || '',
    testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || '',
    placeholder: el.getAttribute('placeholder') || '',
    label: label(el).slice(0, 60),
    text: (el.innerText || el.value || '').trim().slice(0, 60),
    classes: (el.getAttribute('class') || '').split(/\s+/).slice(0, 3).join(' '),
    editable: el.getAttribute('contenteditable') === 'true',
  });

  const groups = {
    buttons: 'button, [role=button], a[href], [role=tab], [role=menuitem]',
    inputs: 'input:not([type=hidden]), textarea, select',
    editors: '[contenteditable=true], .ProseMirror, .ql-editor, .tiptap, [data-lexical-editor]',
    dialogs: '[role=dialog], [aria-modal=true]',
  };
  const out = {url: location.href, title: document.title};
  for (const [key, selector] of Object.entries(groups)) {
    out[key] = [...document.querySelectorAll(selector)]
      .filter(visible).slice(0, 60).map(describe);
  }
  out.frames = [...document.querySelectorAll('iframe')].map((f) => ({
    src: (f.getAttribute('src') || '').slice(0, 100),
    id: f.getAttribute('id') || '',
    testid: f.getAttribute('data-testid') || '',
  }));
  return out;
}
"""


@app.command()
def inspect(
    platform: str = typer.Argument(..., help="Recipe key, e.g. noon"),
    url: Optional[str] = typer.Option(None, "--url", help="Page to open. Defaults to login.url."),
    pause: bool = typer.Option(
        True, "--pause/--no-pause", help="Open the Playwright inspector and wait."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Open a page with the saved session and list every element worth targeting.

    This is how a recipe gets its real selectors. The output is written to
    ARTIFACT_DIR as JSON, which is the thing to look at when a step cannot find
    its element.
    """
    settings = _setup(verbose)
    try:
        path = asyncio.run(_inspect(platform, url, pause, settings))
    except PipelineError as exc:
        _fail(str(exc))
        return
    console.print(f"\n[green]Written:[/green] {path}")


async def _inspect(platform: str, url: str | None, pause: bool, settings: Any) -> str:
    from app.platforms import BrowserRunner, load_recipes, resolve
    from app.sessions.store import SessionStore

    recipe = resolve(platform, load_recipes(settings))
    if recipe is None:
        raise PipelineError(f"No recipe named {platform!r}.")

    target = url or recipe.login.url
    if not target:
        raise PipelineError("No URL to open. Pass --url.")

    sessions = SessionStore(settings)
    state = sessions.load(recipe.key, recipe.session_file)
    if state is None:
        console.print(
            f"[yellow]No saved session - opening logged out. "
            f"Run: python -m app.cli login {recipe.key}[/yellow]"
        )

    runner = BrowserRunner(settings, headless=False)
    await runner.start()
    try:
        async with runner.context(storage_state=state) as (context, page):
            await page.goto(target, wait_until="domcontentloaded")
            console.print(f"[dim]opened {target}[/dim]")

            if pause:
                console.print(
                    "\n[bold]Navigate to the screen you want to map, then close "
                    "the inspector to capture it.[/bold]\n"
                )
                await page.pause()

            probe = await page.evaluate(_PROBE)
            _print_probe(probe)

            directory = Path(settings.artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            out = directory / f"{recipe.key}-probe.json"
            out.write_text(json.dumps(probe, indent=2), encoding="utf-8")

            shot = directory / f"{recipe.key}-probe.png"
            await page.screenshot(path=str(shot), full_page=True)
            console.print(f"[dim]screenshot: {shot}[/dim]")
            return str(out)
    finally:
        await runner.stop()


@app.command()
def record(
    platform: str = typer.Argument(..., help="Key for the new recipe, e.g. noon"),
    url: str = typer.Option(..., "--url", help="Where the job starts."),
    doc: Optional[str] = typer.Option(
        None, "--doc", help="A .docx to type real values from, so they map to templates."
    ),
    label: Optional[str] = typer.Option(None, "--label", help="Human name for the platform."),
    kind: str = typer.Option("email_sequence", help="email_sequence or advert."),
    out: Optional[str] = typer.Option(None, "--out", help="Where to write the recipe."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Do the job once by hand; this writes the recipe from what you actually did.

    Every click and every field you fill is captured with the most stable
    selector available for that element. Pass --doc and type the real values from
    that document, and they are written back as {{ advert.title }} rather than as
    literals - so the recipe works for every future document, not just that one.
    """
    settings = _setup(verbose)
    try:
        path = asyncio.run(_record(platform, url, doc, label, kind, out, settings))
    except PipelineError as exc:
        _fail(str(exc))
        return

    console.print(f"\n[green]Recipe written:[/green] {path}")
    console.print("\n[bold]Next[/bold]")
    console.print("  1. Open it and split the repeated steps into [cyan]per_email:[/cyan]")
    console.print("  2. Mark the publishing step with [cyan]submit: true[/cyan]")
    console.print("  3. Set [cyan]login.ready_selector[/cyan] to something only a logged-in page shows")
    console.print(f"  4. [cyan]python -m app.cli post {platform} --doc <file> --dry-run --headed[/cyan]")


async def _record(
    platform: str,
    url: str,
    doc: str | None,
    label: str | None,
    kind: str,
    out: str | None,
    settings: Any,
) -> str:
    from app.pipeline import load_document
    from app.platforms.recorder import record_session
    from app.sessions.store import SessionStore

    document = None
    if doc:
        document = await load_document(doc, settings)
        console.print(
            f"[dim]loaded {len(document.emails)} emails - type these real values "
            "and they map to templates automatically[/dim]"
        )
        if document.advert:
            console.print(f"[dim]  title:    {document.advert.title}[/dim]")
            if document.advert.location:
                console.print(f"[dim]  location: {document.advert.location}[/dim]")
        for email in document.emails:
            console.print(f"[dim]  email #{email.order}: {email.subject}[/dim]")

    state = SessionStore(settings).load(platform)
    if state is None:
        console.print(
            f"[yellow]No saved session - you will need to log in during the "
            f"recording (that is fine). Or run: python -m app.cli login {platform}[/yellow]"
        )

    target = Path(out) if out else Path(settings.platform_config_dir) / f"{platform}.recorded.yaml"
    written = await record_session(
        key=platform,
        label=label or platform,
        start_url=url,
        output=target,
        document=document,
        kind=kind,
        settings=settings,
        storage_state=state,
        browser_channel=_recipe_channel(platform, settings),
    )
    return str(written)


def _recipe_channel(platform: str, settings: Any) -> str | None:
    """The browser a recipe asks for, when a recipe for it exists yet.

    `record` runs before there is anything to record, so a missing recipe here
    is ordinary rather than an error.
    """
    from app.platforms import load_recipes, resolve

    try:
        recipe = resolve(platform, load_recipes(settings))
    except PipelineError:
        return None
    return recipe.browser_channel if recipe else None


def _print_probe(probe: dict[str, Any]) -> None:
    console.print(f"\n[bold]{probe.get('title', '')}[/bold]  [dim]{probe.get('url', '')}[/dim]")

    for group, columns in (
        ("editors", ("tag", "testid", "id", "classes")),
        ("inputs", ("tag", "type", "label", "name", "id", "placeholder", "testid")),
        ("buttons", ("tag", "role", "text", "testid", "id")),
        ("frames", ("id", "testid", "src")),
    ):
        entries = probe.get(group) or []
        if not entries:
            continue
        table = Table(title=f"{group} ({len(entries)})")
        for column in columns:
            table.add_column(column, overflow="fold")
        for entry in entries[:40]:
            table.add_row(*[str(entry.get(c, ""))[:44] for c in columns])
        console.print(table)


if __name__ == "__main__":
    app()
