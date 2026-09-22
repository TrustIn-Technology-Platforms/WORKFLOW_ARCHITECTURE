"""Keep every platform's login alive from the machine that uses it.

A session ends on the platform's clock and the only thing that extends it is
use. This visits each platform on its saved profile, lets the platform's own
check judge the session, signs in again with the stored credentials when the
check fails, and re-exports the cookies. The deployed service runs it on a
timer (`SESSION_KEEPALIVE_HOURS`) under the same lock the rows run under, so a
keepalive never opens a platform while a row is posting to it. The CLI runs
the same code by hand: `python -m app.cli keepalive`.

It replaces the Windows scheduled task that did this from a laptop and pushed
the result up: two copies of one session on two machines is how Loxo ends
both, and a login that needed the laptop could not recover at 3am.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import AuthenticationRequired, PlatformError
from app.platforms.browser import BrowserRunner, save_failure
from app.platforms.recipe import Recipe, load_recipes
from app.sessions.store import SessionStore

log = get_logger(__name__)


@dataclass(slots=True)
class KeepaliveResult:
    """What one platform's visit found and did. Plain values, for /health."""

    platform: str
    label: str
    checked_at: str
    ok: bool
    logged_in: bool = False
    relogged_in: bool = False
    cookies: int = 0
    seconds: float = 0.0
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "label": self.label,
            "checked_at": self.checked_at,
            "ok": self.ok,
            "logged_in": self.logged_in,
            "relogged_in": self.relogged_in,
            "cookies": self.cookies,
            "seconds": round(self.seconds, 1),
            "detail": self.detail,
            "artifacts": list(self.artifacts),
        }

    @property
    def summary(self) -> str:
        state = (
            "signed in again" if self.relogged_in
            else "alive" if self.logged_in
            else "NOT logged in"
        )
        tail = f" - {self.detail}" if self.detail and not self.ok else ""
        return f"{self.label}: {state}, {self.cookies} cookies exported{tail}"


async def keepalive_platform(
    recipe: Recipe,
    settings: Settings | None = None,
    runner: BrowserRunner | None = None,
) -> KeepaliveResult:
    """Visit one platform. Never raises: the timer loop must outlive any one
    platform's bad day, so every failure is a result with a `detail`."""
    from app.platforms import get_adapter

    settings = settings or get_settings()
    started = time.monotonic()
    result = KeepaliveResult(
        platform=recipe.key,
        label=recipe.label,
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ok=False,
    )
    store = SessionStore(settings)
    if not store.has_profile(recipe.key):
        result.detail = (
            f"no browser profile in {store.profile_dir(recipe.key)} - capture one "
            f"with `python -m app.cli login {recipe.key}` and upload it once; the "
            "service keeps it alive from there."
        )
        result.seconds = time.monotonic() - started
        return result

    adapter = get_adapter(recipe.key, recipes={recipe.key: recipe}, runner=runner,
                          settings=settings, dry_run=True)
    owned = runner is None
    runner = runner or BrowserRunner(settings)
    try:
        if owned:
            await runner.start()
        async with runner.profile_context(
            recipe.key, trace_name=f"{recipe.key}-keepalive", channel=recipe.browser_channel
        ) as (context, page):
            try:
                result.relogged_in = await adapter.ensure_logged_in(page)
                result.logged_in = True
                result.cookies = await adapter.record_session(page)
                result.ok = True
            except (PlatformError, AuthenticationRequired) as exc:
                result.detail = str(exc)
                result.artifacts = await save_failure(
                    context, page, f"{recipe.key}-keepalive-failed", settings
                )
            except Exception as exc:  # noqa: BLE001 - a browser error is a result too
                first = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0]
                result.detail = f"{exc.__class__.__name__}: {first[:200]}"
                result.artifacts = await save_failure(
                    context, page, f"{recipe.key}-keepalive-failed", settings
                )
    except Exception as exc:  # noqa: BLE001 - the profile would not even open
        first = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0]
        result.detail = f"could not open the browser profile - {exc.__class__.__name__}: {first[:200]}"
    finally:
        if owned:
            try:
                await runner.stop()
            except Exception:  # noqa: BLE001
                pass
    result.seconds = time.monotonic() - started
    log.info(
        "keepalive visit finished",
        extra={
            "platform": recipe.key, "ok": result.ok, "logged_in": result.logged_in,
            "relogged_in": result.relogged_in, "cookies": result.cookies,
            "seconds": round(result.seconds, 1), "detail": result.detail[:200],
        },
    )
    return result


async def keepalive(
    settings: Settings | None = None,
    keys: list[str] | None = None,
    recipes: dict[str, Recipe] | None = None,
) -> list[KeepaliveResult]:
    """Every enabled platform in turn, one browser at a time.

    Sequential on purpose: a profile cannot be shared by two Chromes, and two
    contexts on one account is how a platform logs both out.
    """
    settings = settings or get_settings()
    recipes = recipes if recipes is not None else load_recipes(settings)
    wanted = [k for k in (keys or sorted(recipes)) if k in recipes]
    if not keys:
        wanted = [k for k in wanted if recipes[k].enabled]
    results: list[KeepaliveResult] = []
    for key in wanted:
        results.append(await keepalive_platform(recipes[key], settings))
    return results
