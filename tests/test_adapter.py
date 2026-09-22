"""Decisions the adapter takes before any browser is opened, and the session
check itself, driven against mock platform pages served over HTTP."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.models import (
    Advert,
    AuthenticationRequired,
    EmailStep,
    Outcome,
    ParsedDocument,
)
from app.platforms.adapter import RecipeAdapter
from app.platforms.browser import BrowserRunner
from app.platforms.recipe import Recipe, load_recipe


def _advert_recipe(**kwargs) -> Recipe:
    return Recipe(
        key="wellfound", label="Wellfound", kind="advert",
        path=Path("wellfound.yaml"), **kwargs,
    )


def _emails_only() -> ParsedDocument:
    return ParsedDocument(
        advert=None,
        emails=[EmailStep(order=1, subject="s", body_text="b", body_html="<p>b</p>")],
    )


def test_a_job_board_skips_an_emails_only_document():
    """Wellfound posts the advert half. A document with no advert must skip
    with a message naming the fix - not open the form and type empty strings
    into it. The skip happens before any session or browser work, which is
    also what makes it testable without either.
    """
    adapter = RecipeAdapter(_advert_recipe(), settings=Settings())

    result = asyncio.run(adapter.post(_emails_only()))

    assert result.outcome == Outcome.SKIPPED
    assert "no advert" in (result.detail or "")
    assert "emails only" in (result.detail or "")


def test_a_board_advert_section_is_enough():
    """An emails-only document *with* a `Wellfound` section still posts: the
    board's own copy is an advert for that board. It must get past the skip
    (and then stop at the missing-profile gate, proving the skip was the
    only thing standing before login).
    """
    document = _emails_only()
    document.platform_adverts["wellfound"] = Advert(
        title="Platform Engineer", body_text="Board copy.", body_html="<p>Board copy.</p>"
    )
    # No browser profile exists in a test checkout, so a run that passes the
    # advert gate fails at the session gate - a different, named failure.
    recipe = _advert_recipe()
    recipe.login.url = "https://wellfound.com/recruit/jobs-beta"
    adapter = RecipeAdapter(recipe, settings=Settings(browser_profile_dir=Path("nowhere")))

    import pytest
    from app.models import AuthenticationRequired

    with pytest.raises(AuthenticationRequired):
        asyncio.run(adapter.post(document))


# -- login capture without a terminal -----------------------------------------


def test_login_wait_does_not_take_eof_for_the_operator(monkeypatch):
    """From a script or an agent's shell, stdin is end-of-file at once. The old
    wait read that as Enter, checked a session nobody had signed into, and
    reported the login dead (Loxo, 2026-09-02/03). Without a terminal the wait
    now watches the browser and runs to its timeout instead."""
    import asyncio

    from app.platforms.adapter import _await_login

    class Login:
        ready_selector = None
        logged_out_pattern = None

    class Page:
        def is_closed(self):
            return False

        async def wait_for_event(self, name, timeout=0):
            await asyncio.Event().wait()

    # pytest's captured stdin raises on read, the way a closed pipe does.
    assert asyncio.run(_await_login(Page(), Login(), timeout_seconds=1)) == "timeout"


# -- the session check against the screens that broke it -----------------------
#
# On 2026-09-16 Wellfound answered /recruit/jobs-beta with its "Hand-picked for
# you" interstitial. The session was alive; none of the three ready selectors
# was visible on that page, so the check polled for 90s and sent the operator
# to re-login for nothing (failure artifact 20260916-111732). The recipe now
# leads with the recruiter logo link.
#
# These run the shipped platforms/wellfound.yaml, not a hand-written recipe, so
# weakening the selector list breaks them. The pair that matters is the live
# page being called alive AND the signed-out page still being called dead: a
# selector loose enough to never detect a dead session would be the worse bug.

WELLFOUND_YAML = Path(__file__).resolve().parents[1] / "platforms" / "wellfound.yaml"

# What the recipe listed before the fix. Kept here so the fixture can be shown
# to reproduce the failure without reverting anything.
PRE_FIX_SELECTORS = [
    "a:has-text('Post Job')",
    "a[href='/recruit/jobs/new']",
    "a:has-text('Find Talent')",
]


def _wellfound(page_url: str, page_name: str, **overrides) -> Recipe:
    recipe = load_recipe(WELLFOUND_YAML)
    recipe.login.url = page_url.replace("mock-sequence.html", page_name)
    for field, value in overrides.items():
        setattr(recipe.login, field, value)
    return recipe


def _check_settings(tmp_path: Path, seconds: int) -> Settings:
    return Settings(
        _env_file=None,
        session_dir=tmp_path / "sessions",
        browser_profile_dir=tmp_path / "profiles",
        artifact_dir=tmp_path / "artifacts",
        headless=True,
        login_check_seconds=seconds,
        # No stored login, so a session read as dead stops at the check instead
        # of driving the real /login. Whether it is dead is the whole question.
        wellfound_login_username="",
    )


def _session_check(recipe: Recipe, settings: Settings) -> tuple[bool, float, str]:
    """Run the production check. Returns (alive, seconds taken, message).

    The clock starts after the browser is up: what the tests read off it is
    whether the check ended on a selector or ran to its deadline, and a slow
    Chromium launch must not be mistaken for either.
    """

    async def run() -> tuple[bool, float, str]:
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (_context, page):
                adapter = RecipeAdapter(recipe, settings=settings)
                started = time.monotonic()
                try:
                    await adapter.ensure_logged_in(page)
                    return True, time.monotonic() - started, ""
                except AuthenticationRequired as exc:
                    return False, time.monotonic() - started, str(exc)

    return asyncio.run(run())


def test_the_handpicked_interstitial_is_a_live_session(page_url, tmp_path):
    """The 2026-09-16 screen: signed in, no Post Job link, nav labels hidden,
    and a company-profile dialog over the top. It must read as logged in."""
    recipe = _wellfound(page_url, "mock-wellfound-handpicked.html")
    assert "aria-label='Wellfound'" in " ".join(recipe.login.ready_selector), (
        "the shipped recipe must still lead with the recruiter logo link"
    )

    alive, seconds, message = _session_check(recipe, _check_settings(tmp_path, 8))

    assert alive is True, f"a live session was called expired after {seconds:.1f}s: {message}"
    # Found on the first pass, not scraped in just before the 8s deadline.
    assert seconds < 4


def test_the_pre_fix_selectors_still_fail_on_that_page(page_url, tmp_path):
    """Keeps the fixture honest: the same page with the three selectors the
    recipe carried until 2026-09-16 reproduces the false alarm. If this ever
    passes as alive, the fixture stopped reproducing anything and the test
    above proves nothing."""
    recipe = _wellfound(
        page_url, "mock-wellfound-handpicked.html", ready_selector=PRE_FIX_SELECTORS
    )

    alive, _seconds, message = _session_check(recipe, _check_settings(tmp_path, 4))

    assert alive is False
    assert "not logged in" in message


def test_a_bounce_to_the_sign_in_page_is_still_dead(page_url, tmp_path):
    """The converse, via the URL: Wellfound's redirect leaves the tab on
    /login?after_sign_in=..., and that must still end the check - quickly, and
    with something the recruiter can act on."""
    recipe = _wellfound(page_url, "mock-wellfound-signedout.html?bounce=1")

    # Long enough that the deadline cannot be what ends it; only the bounce can.
    alive, seconds, message = _session_check(recipe, _check_settings(tmp_path, 40))

    assert alive is False
    assert seconds < 25, "the sign-in URL should end the check, not the deadline"
    assert "Wellfound is not logged in" in message
    assert "WELLFOUND_LOGIN_USERNAME" in message


def test_the_ready_selector_alone_still_calls_a_signed_out_page_dead(page_url, tmp_path):
    """The assertion that matters most. With `logged_out_pattern` cleared, the
    selector list is the only judge, and it must judge the public page dead -
    even though that page carries its own `aria-label="Wellfound"` logo and a
    `/recruit` link. Loosen the selector to either and this fails."""
    recipe = _wellfound(
        page_url, "mock-wellfound-signedout.html", logged_out_pattern=""
    )

    alive, _seconds, message = _session_check(recipe, _check_settings(tmp_path, 4))

    assert alive is False
    assert "not logged in" in message
