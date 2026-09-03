"""Drive one platform through its recipe, and capture its login."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import TYPE_CHECKING, Any, Protocol

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import (
    AuthenticationRequired,
    NotionRow,
    Outcome,
    ParsedDocument,
    PlatformError,
    PostResult,
)
from app.platforms.actions import StepRun, find
from app.platforms.browser import BrowserRunner, save_failure
from app.platforms.engine import RecipeEngine, RunReport
from app.platforms.recipe import Recipe
from app.sessions.store import SessionStore

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)

# How long the login check waits for the logged-in shell before declaring the
# session expired. Generous on purpose: the check runs once per platform per
# row, and a false "expired" costs a human a trip through SSO where a slow
# true positive costs ninety seconds.
LOGIN_CHECK_SECONDS = 90


class PlatformAdapter(Protocol):
    name: str

    async def post(
        self, document: ParsedDocument, row: NotionRow | None = None
    ) -> PostResult: ...


class RecipeAdapter:
    """A platform driven entirely by its YAML recipe.

    Returns a `PostResult` and never writes to Notion. Write-back belongs to the
    orchestrator, so a row posting to three platforms records one coherent
    status rather than racing three updates.
    """

    def __init__(
        self,
        recipe: Recipe,
        runner: BrowserRunner | None = None,
        settings: Settings | None = None,
        sessions: SessionStore | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.recipe = recipe
        self.settings = settings or get_settings()
        self.runner = runner
        self.sessions = sessions or SessionStore(self.settings)
        self.dry_run = self.settings.dry_run if dry_run is None else dry_run

    @property
    def name(self) -> str:
        return self.recipe.key

    async def post(
        self, document: ParsedDocument, row: NotionRow | None = None
    ) -> PostResult:
        recipe = self.recipe

        if not recipe.enabled:
            return PostResult(
                platform=recipe.key,
                outcome=Outcome.SKIPPED,
                detail=f"{recipe.label} is disabled in {recipe.path.name}.",
                finished_at=datetime.now(timezone.utc),
            )

        # A job board posts the advert half of the document, and an emails-only
        # document has none - the engine would otherwise open the form and type
        # empty strings into it. Skipping is a decision the recruiter can act
        # on; a half-filled draft with no title is a mystery they cannot.
        if recipe.kind == "advert" and document.advert_for(recipe.key) is None:
            return PostResult(
                platform=recipe.key,
                outcome=Outcome.SKIPPED,
                detail=(
                    f"{recipe.label} posts the advert half of the document, and "
                    "this document has no advert - it is emails only. Add an "
                    f"advert section (or an 'Ad - {recipe.label}' section) and "
                    "re-run."
                ),
                finished_at=datetime.now(timezone.utc),
            )

        # A recipe with no login block needs no session - that covers a public
        # destination and the local fixture the engine is tested against.
        needs_login = bool(recipe.login.url or recipe.login.ready_selector)
        use_profile = bool(self.settings.use_browser_profile)

        state = None
        if needs_login:
            if use_profile:
                self.sessions.require_profile(recipe.key, recipe.label)
            else:
                state = self.sessions.require(
                    recipe.key, recipe.label, recipe.session_file
                )

        owned_runner = self.runner is None
        runner = self.runner or BrowserRunner(self.settings)
        if owned_runner:
            await runner.start()

        opener = (
            runner.profile_context(
                recipe.key, trace_name=recipe.key, channel=recipe.browser_channel
            )
            if (use_profile and needs_login)
            else runner.context(storage_state=state, trace_name=recipe.key)
        )

        try:
            async with opener as (context, page):
                try:
                    await self._assert_logged_in(page)
                    report = await self._drive(page, document, row)
                except (PlatformError, AuthenticationRequired) as exc:
                    artifacts = await save_failure(
                        context, page, f"{recipe.key}-failed", self.settings
                    )
                    exc.artifacts = artifacts  # type: ignore[attr-defined]
                    raise

                outcome = Outcome.DRY_RUN if self.dry_run else Outcome.POSTED
                detail = "; ".join(report.warnings) or None
                log.info(
                    "platform finished",
                    extra={
                        "platform": recipe.key,
                        "outcome": outcome.value,
                        "steps": report.executed,
                        "emails": report.emails_written,
                        "post_url": report.post_url,
                    },
                )
                return PostResult(
                    platform=recipe.key,
                    outcome=outcome,
                    post_url=report.post_url,
                    detail=detail,
                    finished_at=datetime.now(timezone.utc),
                )
        finally:
            if owned_runner:
                await runner.stop()

    async def _drive(
        self, page: "Page", document: ParsedDocument, row: NotionRow | None
    ) -> "RunReport":
        """Run the platform's steps and return what happened.

        The default runs the YAML recipe through the engine. A driver-backed
        platform overrides this with a hand-written flow while keeping all of
        the session, login and failure-artifact handling above it unchanged.
        """
        engine = RecipeEngine(self.recipe, page, self.settings, dry_run=self.dry_run)
        return await engine.run(document, row)

    async def _assert_logged_in(self, page: "Page") -> None:
        """Check the session before any real work.

        Checking up front turns an expired login into one clear message rather
        than a confusing selector failure three steps deep.
        """
        login = self.recipe.login
        if not login.ready_selector and not login.logged_out_pattern:
            return

        start_url = login.url or self._first_goto()
        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded")

        def expired() -> AuthenticationRequired:
            return AuthenticationRequired(
                f"{self.recipe.label} is not logged in (or the session has "
                f"expired). Run: python -m app.cli login {self.recipe.key}"
            )

        # Pattern-only recipes (no shell selector) get the single check: let
        # the app settle, then read the URL. A bounce to the sign-in page is
        # unambiguous either way.
        if not login.ready_selector:
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            if re.search(login.logged_out_pattern, page.url):
                log.info(
                    "redirected to login",
                    extra={"platform": self.recipe.key, "url": page.url},
                )
                raise expired()
            return

        # A slow page is not a dead session, and a fixed 15s selector wait
        # could not tell them apart: the Railway container renders this class
        # of app in 15-45s where a laptop takes 5-12s (measured 2026-09-01,
        # when Wellfound "expired" on the container while perfectly logged in
        # locally - intermittently, which sent everyone chasing cookies). So
        # poll: the sign-in URL ends it as expired at once, the logged-in
        # shell ends it as fine at once, and only the deadline - long enough
        # that a live session cannot plausibly still be rendering - reads as
        # expired.
        deadline = asyncio.get_event_loop().time() + LOGIN_CHECK_SECONDS
        bounced = 0
        while True:
            # Two consecutive sightings, because polling sees more than a
            # single check ever did: a live session refreshing its token can
            # pass through /login?after_sign_in=... on its way back to the
            # dashboard, and reading that one frame as expiry is the false
            # alarm the wellfound recipe already documents (2026-08-28).
            if login.logged_out_pattern and re.search(login.logged_out_pattern, page.url):
                bounced += 1
                if bounced >= 2:
                    log.info(
                        "redirected to login",
                        extra={"platform": self.recipe.key, "url": page.url},
                    )
                    raise expired()
                await page.wait_for_timeout(2_000)
            else:
                bounced = 0
            probe = StepRun(page=page, params={"selector": login.ready_selector})
            probe.timeout_ms = 4_000
            if await find(probe, required=False) is not None:
                return
            if asyncio.get_event_loop().time() >= deadline:
                raise expired()

    def _first_goto(self) -> str | None:
        for step in self.recipe.steps:
            if step.action == "goto":
                url = step.params.get("url")
                return str(url) if url and "{{" not in str(url) else None
        return None


async def capture_login(
    recipe: Recipe,
    settings: Settings | None = None,
    sessions: SessionStore | None = None,
    timeout_seconds: int = 300,
) -> str:
    """Open a visible browser, wait for a human to log in, save the session.

    Nothing is typed for the user and nothing is read back from the form. The
    browser is visible regardless of HEADLESS - the entire point is that a
    person completes the login, including MFA or SSO.

    The operator pressing Enter is always sufficient. On a platform nobody has
    automated yet, the selector that would prove a successful login is exactly
    the thing not yet known, so making the save depend on it would mean a login
    that plainly worked in the browser saves nothing at all.
    """
    settings = settings or get_settings()
    sessions = sessions or SessionStore(settings)
    login = recipe.login

    if not login.url:
        raise PlatformError(
            f"{recipe.path.name} has no login.url, so there is nowhere to send "
            "the browser."
        )

    use_profile = bool(settings.use_browser_profile)
    runner = BrowserRunner(settings, headless=False)
    await runner.start()

    opener = (
        runner.profile_context(
            recipe.key,
            trace_name=f"{recipe.key}-login",
            channel=recipe.browser_channel,
        )
        if use_profile
        else runner.context(trace_name=f"{recipe.key}-login")
    )

    try:
        async with opener as (context, page):
            # `commit` - the response arrived and the browser is showing the
            # page - rather than `domcontentloaded`. Some apps hold the document
            # open long enough that domcontentloaded never fires inside the
            # timeout (Juicebox does), and failing there would abort a login
            # that a person could plainly have completed. For the same reason a
            # slow page is a warning, not an error: the browser is open, and
            # navigating by hand is a perfectly good recovery.
            try:
                await page.goto(login.url, wait_until="commit", timeout=60_000)
            except Exception as exc:
                log.warning(
                    "login page was slow to load",
                    extra={"platform": recipe.key, "url": login.url, "error": str(exc)[:120]},
                )
                print(f"\n  {login.url} is slow to load. The browser is open - "
                      "give it a moment, or navigate there yourself.")

            print(f"\n  A browser has opened at {login.url}")
            print(f"  Log in to {recipe.label} - take as long as you need.")
            print("\n  Press Enter here once you are logged in - or just wait: the "
                  "session saves itself once the app's own shell appears. Closing "
                  "the window cancels.\n")

            signal = await _await_login(page, login, timeout_seconds)

            if signal == "timeout":
                raise AuthenticationRequired(
                    f"Nothing happened for {timeout_seconds}s and the browser "
                    "closed. Run the command again, and press Enter once you "
                    "are logged in."
                )

            # An SSO round-trip can still be in flight when Enter is pressed.
            # Let it land before touching anything - navigating over the top of
            # it destroys the login that was about to complete.
            print("  Letting the sign-in settle...")
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            # Verify in a SECOND TAB. Re-navigating the operator's own tab is
            # what aborted an in-flight redirect once already; a new tab proves
            # the same thing and cannot interfere.
            print("  Checking the session...")
            still_out = True
            checked_url = page.url
            try:
                check = await context.new_page()
                try:
                    await check.goto(login.url, wait_until="commit", timeout=60_000)
                    if login.ready_selector:
                        # Positive evidence. A platform whose URL never changes
                        # (Juicebox renders a blank page at the same address when
                        # logged out) cannot be judged by `logged_out_pattern`,
                        # which returns False when unset - so without this the
                        # check passes no matter what, and a failed login is
                        # recorded as a success.
                        run = StepRun(page=check, params={"selector": login.ready_selector})
                        run.timeout_ms = 45_000
                        still_out = await find(run, required=False) is None
                    else:
                        await check.wait_for_timeout(6_000)
                        still_out = await _looks_logged_out(check, login)
                    checked_url = check.url
                finally:
                    await check.close()
            except Exception as exc:
                log.debug("verification tab failed", extra={"error": str(exc)})
                still_out = await _looks_logged_out(page, login)
                checked_url = page.url

            state = await context.storage_state()
            cookies = state.get("cookies") or []
            app_host = (urlparse(login.url).hostname or "").lower()
            app_cookies = [
                c["name"]
                for c in cookies
                if app_host and app_host.endswith(str(c.get("domain", "")).lstrip("."))
                and not str(c["name"]).startswith(("_ga", "_gid", "__stripe", "_fbp"))
            ]

            log.info(
                "login attempt finished",
                extra={
                    "platform": recipe.key,
                    "signal": signal,
                    "cookies": len(cookies),
                    "app_cookies": app_cookies,
                    "logged_in": not still_out,
                },
            )
            print(
                f"  Cookies: {len(cookies)} total, "
                f"{len(app_cookies)} from {app_host or 'the app'}"
                + (f" ({', '.join(app_cookies[:4])})" if app_cookies else "")
            )

            # Reporting success on a failed login is worse than failing: the next
            # command trusts it and fails somewhere far less obvious.
            if still_out or not app_cookies:
                sso_only = len(cookies) > len(app_cookies)
                raise AuthenticationRequired(
                    f"Not logged in. {login.url} still bounces to {checked_url}, "
                    f"and {app_host or 'the app'} set no session cookie"
                    + (
                        ".\n  The single-sign-on step did complete - there are "
                        "identity-provider cookies - but the app never issued its "
                        "own session, so the round trip stopped half way."
                        if sso_only
                        else "."
                    )
                    + "\n  Run it again and this time wait until the app's own "
                    "dashboard is fully loaded before pressing Enter. Signing in "
                    "will be quicker now, since the identity provider is already "
                    "remembered in the profile."
                )

            if use_profile:
                # The profile directory *is* the session; closing the context
                # flushes Chrome's own stores into it. The storage_state copy is
                # kept as well, because that is what the non-profile path reads.
                sessions.save_state(recipe.key, state, recipe.session_file)
                sessions.mark_profile_verified(recipe.key)
                path = str(sessions.profile_dir(recipe.key))
            else:
                path = str(sessions.save_state(recipe.key, state, recipe.session_file))

            print(
                f"  Verified: {login.url} stayed put in a fresh tab, "
                f"session cookie present."
            )
            return str(path)
    finally:
        await runner.stop()


async def _await_login(page: "Page", login: Any, timeout_seconds: int) -> str:
    """Finish on Enter, on a detected login, or when the browser is closed."""
    # Only a real terminal gets the Enter key. From a script, a VS Code task
    # or an agent's shell, stdin is end-of-file at once - `input()` returned
    # immediately, the session was checked before anyone had signed in, and
    # the login was reported dead (Loxo, 2026-09-02/03). Without a terminal
    # the detector and the window are the only signals.
    async def operator() -> None:
        # A stdin that is closed, or that hands back end-of-file the instant it
        # is read - a script, a VS Code task, an agent's shell, even one whose
        # pseudo-terminal claims isatty() - is not a person. Such a task never
        # resolves, and the detector and the window carry the wait.
        started = asyncio.get_event_loop().time()
        try:
            line = await asyncio.to_thread(input)
        except (EOFError, OSError):
            line = None
        if line is None or (line == "" and asyncio.get_event_loop().time() - started < 2.0):
            await asyncio.Event().wait()

    tasks = {
        asyncio.create_task(_poll_logged_in(page, login)): "detected",
        asyncio.create_task(page.wait_for_event("close", timeout=0)): "closed",
        asyncio.create_task(operator()): "operator",
    }

    done, pending = await asyncio.wait(
        tasks, timeout=timeout_seconds, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()

    for task in done:
        if task.exception() is None:
            return tasks[task]
    return "timeout" if not done else "operator"


async def _poll_logged_in(page: "Page", login: Any) -> None:
    """Resolve as soon as the page stops looking logged out."""
    if not login.logged_out_pattern and not login.ready_selector:
        await asyncio.Event().wait()  # nothing to detect; wait for the operator

    while True:
        await asyncio.sleep(1.5)
        if page.is_closed():
            return
        try:
            if await _looks_logged_out(page, login):
                continue
            if login.ready_selector:
                # Require positive evidence the app shell rendered. Being off the
                # login URL is not enough: a platform whose start URL redirects
                # to /login (Loxo) is momentarily "not logged out" before the
                # redirect, and would otherwise be declared logged in instantly
                # and the browser closed before the operator can sign in.
                run = StepRun(page=page, params={"selector": login.ready_selector})
                run.timeout_ms = 1_000
                if await find(run, required=False) is None:
                    continue
            return
        except Exception:
            # The page navigating (an SSO round-trip destroys the execution
            # context) is the operator working, not proof of login - keep
            # polling rather than closing the browser out from under them.
            continue


async def _looks_logged_out(page: "Page", login: Any) -> bool:
    if not login.logged_out_pattern:
        return False
    try:
        return bool(re.search(login.logged_out_pattern, page.url))
    except Exception:
        return False
