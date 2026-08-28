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

    if problems:
        console.print(f"\n[red]{len(problems)} problem(s).[/red]")
        raise typer.Exit(code=1)
    console.print("\n[green]All checks passed.[/green]")


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

    console.print(f"\n[bold]Emails[/bold] ({len(document.emails)})")
    for email in document.emails:
        delay = f"  +{email.delay_days}d" if email.delay_days is not None else ""
        console.print(f"  [cyan]#{email.order}[/cyan]{delay}  {email.subject}")
        preview = " ".join(email.body_text.split())[:110]
        console.print(f"      [dim]{preview}{'...' if len(preview) == 110 else ''}[/dim]")
        if show_html:
            console.print(f"      [dim]{email.body_html[:800]}[/dim]")

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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one recipe against one document. No Notion involved."""
    settings = _setup(verbose)
    if headed:
        settings.headless = False
    if slow:
        settings.slow_mo_ms = slow

    try:
        result = asyncio.run(_post(platform, doc, settings, dry_run))
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


async def _post(platform: str, source: str, settings: Any, dry_run: bool) -> Any:
    from app.models import Outcome, PostResult
    from app.pipeline import load_document
    from app.platforms import BrowserRunner, get_adapter
    from app.platforms.engine import describe_emails

    document = await load_document(source, settings)
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
            return await adapter.post(document)
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
