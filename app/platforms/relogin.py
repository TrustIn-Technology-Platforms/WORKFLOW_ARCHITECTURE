"""Sign a platform in with its stored credentials, unattended.

Until 2026-09-21 a login was a person at a keyboard, once, and the saved
session was all the service ever had ([D-010](../../docs/11-decisions.md)).
That left the deployed service unable to recover on its own: a session that
expired at 3am waited for someone to notice the row, sign in on a laptop and
push the profile up. Now a recipe may carry its sign-in as ordinary steps under
`login.steps`, the credentials live as service secrets, and the adapter replays
the steps the moment a session check fails ([D-021](../../docs/11-decisions.md)).

What this module owns is the replay itself: rendering the credentials into the
steps, minting a fresh one-time code per step, and turning whatever went wrong
into one message a recruiter can act on - with every secret scrubbed out of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import AuthenticationRequired, Credentials, PipelineError
from app.platforms.engine import RecipeEngine
from app.platforms.recipe import Recipe
from app.utils.totp import TotpError, totp_now

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

log = get_logger(__name__)


def scrub(text: str, secrets: list[str]) -> str:
    """Replace every secret in `text` with a marker.

    Error text from a browser can quote what was typed into a field. Nothing
    that reaches a log, a row or an artifact name may carry a password.
    """
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def login_context(recipe: Recipe, credentials: Credentials) -> dict[str, Any]:
    """What `login.steps` expressions can reach. Built per step so `otp` is
    the code valid *now*, not the one that was valid when the flow started."""
    otp = ""
    if credentials.totp_secret:
        try:
            otp = totp_now(credentials.totp_secret)
        except TotpError as exc:
            raise AuthenticationRequired(
                f"{recipe.label}: the stored TOTP seed is not usable ({exc}). "
                f"Set {credentials.platform.upper()}_LOGIN_TOTP_SECRET to the "
                "base32 seed the authenticator app was registered with."
            ) from exc
    return {
        **recipe.defaults,
        "username": credentials.username,
        "password": credentials.password,
        "otp": otp,
        "totp_secret": credentials.totp_secret,
    }


def can_relogin(recipe: Recipe, settings: Settings | None = None) -> bool:
    """True when this platform can sign itself in: steps in the recipe and
    credentials in the settings. Neither alone is enough."""
    settings = settings or get_settings()
    return bool(recipe.login.steps) and settings.credentials_for(recipe.key) is not None


def why_not(recipe: Recipe, settings: Settings | None = None) -> str:
    """One sentence for the row saying what an automatic re-login would need."""
    settings = settings or get_settings()
    key = recipe.key.upper().replace("-", "_")
    if not recipe.login.steps:
        return (
            f"{recipe.path.name} has no login.steps, so the service cannot sign "
            "in by itself."
        )
    if settings.credentials_for(recipe.key) is None:
        return (
            f"No stored login: set {key}_LOGIN_USERNAME and {key}_LOGIN_PASSWORD "
            "on the service and it will sign in by itself next time."
        )
    return ""


async def login_with_credentials(
    recipe: Recipe,
    page: "Page",
    settings: Settings | None = None,
    credentials: Credentials | None = None,
) -> None:
    """Replay the recipe's sign-in steps. Raises AuthenticationRequired with a
    recruiter-facing message when it cannot; proves nothing itself - the caller
    re-runs the session check afterwards, which is the only evidence that counts."""
    settings = settings or get_settings()
    credentials = credentials or settings.credentials_for(recipe.key)
    if credentials is None or not recipe.login.steps:
        raise AuthenticationRequired(
            f"{recipe.label} is not logged in. {why_not(recipe, settings)} "
            f"Until then: python -m app.cli login {recipe.key}"
        )

    log.warning(
        "signing in with stored credentials",
        extra={"platform": recipe.key, "username": credentials.username,
               "steps": len(recipe.login.steps), "totp": bool(credentials.totp_secret)},
    )
    # A sign-in is never a dry run: there is nothing to publish, and stopping
    # halfway leaves a half-typed form.
    engine = RecipeEngine(recipe, page, settings, dry_run=False)
    try:
        await engine.run_steps(
            recipe.login.steps, lambda: login_context(recipe, credentials)
        )
    except PipelineError as exc:
        raise AuthenticationRequired(
            f"{recipe.label}: the session had expired and the automatic sign-in "
            f"failed - {scrub(str(exc), credentials.secrets)}. Check the stored "
            f"{credentials.platform.upper()}_LOGIN_* variables and the saved "
            f"screenshot; a person can still sign in with: python -m app.cli "
            f"login {recipe.key}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a Playwright error mid-sign-in
        first = (str(exc).strip().splitlines() or [exc.__class__.__name__])[0]
        raise AuthenticationRequired(
            f"{recipe.label}: the session had expired and the automatic sign-in "
            f"broke on the page - {exc.__class__.__name__}: "
            f"{scrub(first[:200], credentials.secrets)}. See the saved screenshot."
        ) from exc

    log.info(
        "sign-in steps finished",
        extra={"platform": recipe.key, "steps": engine.report.executed},
    )
