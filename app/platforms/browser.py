"""Playwright lifecycle: launching, contexts, and artifacts from failed runs."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import PlatformError

if TYPE_CHECKING:  # pragma: no cover - import cost only matters at runtime
    from playwright.async_api import BrowserContext, Page

log = get_logger(__name__)

CHANNEL_MARKER = ".browser-channel"


def claim_profile_channel(directory: Path, channel: str | None) -> None:
    """Bind a profile directory to the browser that created it, and keep it there.

    Chrome encrypts its cookie store with a key held in `Local State`. Opening
    the same directory with a different build re-keys it, and every cookie the
    other browser wrote becomes undecryptable - so a live, logged-in session
    silently becomes a logged-out one. That is not theoretical: it destroyed the
    captured Juicebox session on 2026-08-27, and it looks exactly like an
    ordinary session expiry, which is what makes it worth an explicit guard.
    """
    marker = directory / CHANNEL_MARKER
    current = channel or "bundled"

    if not marker.exists():
        marker.write_text(current, encoding="utf-8")
        return

    owner = marker.read_text(encoding="utf-8").strip() or "bundled"
    if owner == current:
        return

    raise PlatformError(
        f"{directory.name} was captured with {owner!r} but is being opened with "
        f"{current!r}. Chrome and Chromium cannot share a profile: the second one "
        f"re-keys the cookie store and silently logs the session out.\n"
        f"  Either set `browser_channel: {owner}` on the recipe, or delete "
        f"{directory} and capture the login again with {current!r}."
    )

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# A headless Chromium leaves a handful of obvious traces. The account is the
# operator's own and the work is their own, but an app that treats automation as
# hostile will end a valid session on sight - and the symptom is a login that
# looks expired, which sends you looking in entirely the wrong place.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
if (!navigator.languages || !navigator.languages.length) {
  Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
}
if (!navigator.plugins || navigator.plugins.length === 0) {
  Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
}
window.chrome = window.chrome || {runtime: {}};
const _query = window.navigator.permissions && window.navigator.permissions.query;
if (_query) {
  window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _query(p);
}
"""

# Headless Chromium announces itself in the UA string. Anything driving a real
# platform should look like the browser a person would have used.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _import_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise PlatformError(
            "Playwright is not installed. Run: pip install -r requirements.txt "
            "&& playwright install chromium"
        ) from exc
    return async_playwright


class BrowserRunner:
    """One browser process, reused across the contexts of a single run.

    Contexts are never shared between platforms - a shared context leaks
    cookies from one destination into another.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        headless: bool | None = None,
        slow_mo_ms: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.headless = self.settings.headless if headless is None else headless
        self.slow_mo_ms = self.settings.slow_mo_ms if slow_mo_ms is None else slow_mo_ms
        self._playwright: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> "BrowserRunner":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        await self._ensure_playwright()

    async def _ensure_playwright(self) -> None:
        if self._playwright is not None:
            return
        async_playwright = _import_playwright()
        self._playwright = await async_playwright().start()

    async def _ensure_browser(self) -> None:
        """A shared browser, for ephemeral contexts only.

        A profile context launches its own, because a Chrome profile directory
        cannot be shared between two running browsers.
        """
        await self._ensure_playwright()
        if self._browser is not None:
            return

        launch: dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
            # Chromium advertises itself as automated by default. Some platforms
            # respond by force-logging-out a perfectly valid session, which
            # looks exactly like an expired login and is impossible to diagnose
            # from the symptom. These two lines drop the obvious tells.
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if self.settings.browser_channel:
            # A real installed Chrome or Edge. Some platforms serve a different
            # experience to the bundled Chromium build.
            launch["channel"] = self.settings.browser_channel

        self._browser = await self._playwright.chromium.launch(**launch)
        log.info(
            "browser launched",
            extra={
                "headless": self.headless,
                "channel": self.settings.browser_channel or "bundled",
            },
        )

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def profile_context(
        self,
        profile: str,
        trace_name: str | None = None,
        channel: str | None = None,
    ) -> AsyncIterator[tuple["BrowserContext", "Page"]]:
        """A context backed by a real Chrome profile directory on disk.

        Unlike a replayed `storage_state`, this is the whole profile - cookies,
        local storage, IndexedDB, service workers. An app that keeps auth state
        outside cookies therefore finds everything where it left it, instead of
        noticing the gap and logging itself out.

        The profile is the session: logging in once here means every later run
        against the same profile is already logged in.
        """
        await self._ensure_playwright()

        settings = self.settings
        # Absolute, always. Chrome resolves a relative user_data_dir against its
        # own working directory, not ours, so a relative path silently puts the
        # profile somewhere else - and the one here stays empty.
        directory = (
            Path(settings.browser_profile_dir).resolve() / _UNSAFE.sub("-", profile)
        )
        directory.mkdir(parents=True, exist_ok=True)
        chosen_channel = channel or settings.browser_channel
        claim_profile_channel(directory, chosen_channel)

        options: dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
                # Windows renderers die with STATUS_ACCESS_VIOLATION when
                # security software injects a DLL into them and Chrome's code
                # integrity check kills the process for it. The check is already
                # defeated by the injection; keeping it on only costs us the tab.
                "--disable-features=RendererCodeIntegrity",
            ],
            "ignore_default_args": ["--enable-automation"],
            "viewport": {
                "width": settings.viewport_width,
                "height": settings.viewport_height,
            },
            "locale": settings.locale,
            "timezone_id": settings.timezone,
            "user_agent": settings.user_agent or DEFAULT_USER_AGENT,
            "accept_downloads": False,
        }
        if chosen_channel:
            options["channel"] = chosen_channel

        context = await self._playwright.chromium.launch_persistent_context(
            str(directory), **options
        )
        context.set_default_timeout(settings.action_timeout_ms)
        context.set_default_navigation_timeout(settings.nav_timeout_ms)
        await context.add_init_script(_STEALTH_JS)

        # Chrome encrypts the profile's cookie store with an OS-bound key
        # (DPAPI on Windows), so a profile captured on a laptop arrives on a
        # Linux server with unreadable cookies - localStorage/IndexedDB survive,
        # cookies do not (noon and Loxo authenticate by cookie; Juicebox does
        # not, which is why only it survived the move). The upload endpoint
        # drops an `.import-cookies` flag next to freshly imported profiles;
        # when present, inject the decrypted cookies Playwright exported into
        # <session_dir>/<profile>.storage_state.json, then consume the flag so
        # a server-side cookie refresh is not overwritten on later runs.
        flag = directory / ".import-cookies"
        # Consumed only after this context CLOSES cleanly, not here. Injected
        # cookies live in memory until Chrome flushes them to the profile's
        # (now Linux-keyed) store at close; unlinking the flag up front meant a
        # run killed mid-flight - an OOM, a redeploy - lost the cookies AND the
        # flag, and every later run opened a cookie-less profile until the next
        # upload. A crashed run flushed nothing worth protecting, so re-running
        # the injection is the correct recovery, and the flag staying put is
        # what makes it happen.
        cookies_injected = False
        if flag.exists():
            state_path = Path(settings.session_dir) / f"{profile}.storage_state.json"
            try:
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    cookies = state.get("cookies") or []
                    if cookies:
                        await context.add_cookies(cookies)
                    cookies_injected = True
                    log.info(
                        "cookies imported into profile",
                        extra={"profile": profile, "cookies": len(cookies)},
                    )
                else:
                    log.warning(
                        "import-cookies flag set but no storage_state file",
                        extra={"profile": profile, "path": str(state_path)},
                    )
            except Exception as exc:
                log.warning(
                    "cookie import failed",
                    extra={"profile": profile, "error": str(exc)[:200]},
                )

        tracing = False
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            tracing = True
        except Exception as exc:  # pragma: no cover
            log.debug("tracing unavailable", extra={"error": str(exc)})

        context._trace_name = trace_name or profile  # type: ignore[attr-defined]
        context._tracing_on = tracing  # type: ignore[attr-defined]

        # A persistent context opens with a page already.
        page = context.pages[0] if context.pages else await context.new_page()

        log.info(
            "profile context open",
            extra={
                "profile": profile,
                "dir": str(directory),
                "channel": chosen_channel or "bundled",
            },
        )
        try:
            yield context, page
        finally:
            if tracing:
                try:
                    await context.tracing.stop()
                except Exception:  # pragma: no cover
                    pass
            await context.close()
            # The close flushed the injected cookies into the profile's own
            # store, so the import is durable now and the flag has done its
            # job. A run that died before reaching here keeps the flag, and
            # the next run injects again - which is the recovery, not a bug.
            if cookies_injected:
                try:
                    flag.unlink()
                except OSError:
                    pass

    @asynccontextmanager
    async def context(
        self,
        storage_state: dict | None = None,
        trace_name: str | None = None,
    ) -> AsyncIterator[tuple["BrowserContext", "Page"]]:
        """A fresh context, optionally seeded with a saved login.

        Tracing runs for the whole context but is only written to disk when the
        caller reports a failure via `save_failure`. A trace of a successful run
        is noise; a trace of a failed one is how a selector problem gets solved
        without reproducing it.
        """
        await self._ensure_browser()

        settings = self.settings
        options: dict[str, Any] = {
            "viewport": {
                "width": settings.viewport_width,
                "height": settings.viewport_height,
            },
            "locale": settings.locale,
            "timezone_id": settings.timezone,
            "accept_downloads": False,
        }
        # Headless Chromium puts "HeadlessChrome" in the UA, which is the single
        # loudest tell there is.
        options["user_agent"] = settings.user_agent or DEFAULT_USER_AGENT
        if storage_state is not None:
            options["storage_state"] = storage_state

        context = await self._browser.new_context(**options)
        context.set_default_timeout(settings.action_timeout_ms)
        context.set_default_navigation_timeout(settings.nav_timeout_ms)
        await context.add_init_script(_STEALTH_JS)

        tracing = False
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            tracing = True
        except Exception as exc:  # pragma: no cover - tracing is a nicety
            log.debug("tracing unavailable", extra={"error": str(exc)})

        context._trace_name = trace_name or "run"  # type: ignore[attr-defined]
        context._tracing_on = tracing  # type: ignore[attr-defined]

        page = await context.new_page()
        try:
            yield context, page
        finally:
            if tracing:
                try:
                    await context.tracing.stop()
                except Exception:  # pragma: no cover
                    pass
            await context.close()


async def save_failure(
    context: "BrowserContext",
    page: "Page",
    name: str,
    settings: Settings | None = None,
) -> list[str]:
    """Write a screenshot and a trace for a failed step. Never raises.

    Diagnosis happens after the fact, on a machine that cannot reproduce the
    run, so an artifact that fails to save must not replace the real error.
    """
    settings = settings or get_settings()
    directory = Path(settings.artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}-{_UNSAFE.sub('-', name).strip('-')}"
    saved: list[str] = []

    try:
        shot = directory / f"{stem}.png"
        await page.screenshot(path=str(shot), full_page=True)
        saved.append(str(shot))
    except Exception as exc:  # pragma: no cover
        log.debug("screenshot failed", extra={"error": str(exc)})

    if getattr(context, "_tracing_on", False):
        try:
            trace = directory / f"{stem}.trace.zip"
            await context.tracing.stop(path=str(trace))
            context._tracing_on = False  # type: ignore[attr-defined]
            saved.append(str(trace))
        except Exception as exc:  # pragma: no cover
            log.debug("trace save failed", extra={"error": str(exc)})

    try:
        html = directory / f"{stem}.html"
        html.write_text(await page.content(), encoding="utf-8")
        saved.append(str(html))
    except Exception as exc:  # pragma: no cover
        log.debug("html dump failed", extra={"error": str(exc)})

    if saved:
        log.info("failure artifacts saved", extra={"files": saved})
    return saved
