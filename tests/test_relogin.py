"""The service signing a platform in by itself.

Run against the mock platform page over HTTP and a mock Microsoft sign-in
served at the real Microsoft host through a Playwright route, so what is
tested is the flow the adapter runs at 3am: the session check fails, the
recipe's login steps replay, the check passes, the session is exported.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app.config import Settings, reset_settings_cache
from app.models import AuthenticationRequired, Credentials
from app.platforms.adapter import RecipeAdapter
from app.platforms.browser import BrowserRunner
from app.platforms.recipe import LoginSpec, Recipe, Step
from app.platforms.relogin import can_relogin, login_context, scrub, why_not
from app.utils.totp import totp_now

FIXTURES = Path(__file__).parent / "fixtures"
MOCK_MS = (FIXTURES / "pages" / "mock-microsoft.html").read_text(encoding="utf-8")

USER = "marcus@trust-in.test"
PASS = "correct horse battery"
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.fixture(autouse=True)
def _fresh_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        session_dir=tmp_path / "sessions",
        browser_profile_dir=tmp_path / "profiles",
        artifact_dir=tmp_path / "artifacts",
        headless=True,
        # The check would otherwise wait 90s before calling a session dead.
        login_check_seconds=3,
        **overrides,
    )


def _login_url(page_url: str) -> str:
    return page_url.replace("mock-sequence.html", "mock-login.html")


def _recipe(login_url: str, steps: list[Step], ready: str = "#user-menu") -> Recipe:
    # Keyed `wellfound` because credentials are looked up by recipe key and
    # the mock has no settings fields of its own.
    return Recipe(
        key="wellfound", label="Mock platform", kind="advert", path=Path("mock.yaml"),
        login=LoginSpec(url=login_url, ready_selector=ready, steps=steps),
    )


def _password_steps(login_url: str) -> list[Step]:
    return [
        Step("goto", {"url": login_url}, index=1, phase="login"),
        Step("fill", {"selector": "#email", "value": "{{ username }}"}, index=2, phase="login"),
        Step("fill", {"selector": "#password", "value": "{{ password }}"}, index=3, phase="login"),
        Step("click", {"selector": "#login"}, index=4, phase="login"),
        Step("wait_for", {"selector": "#user-menu"}, index=5, phase="login"),
    ]


# -- pure parts -----------------------------------------------------------------


def test_scrub_removes_every_secret_longest_first():
    text = "fill 'hunter22' failed; seed hunter2 also shown"
    assert scrub(text, ["hunter2", "hunter22"]) == "fill '***' failed; seed *** also shown"
    assert scrub("nothing here", []) == "nothing here"


def test_login_context_mints_the_code_valid_now():
    recipe = _recipe("http://x/", [], ready="")
    creds = Credentials("wellfound", USER, PASS, SEED)
    context = login_context(recipe, creds)
    assert context["username"] == USER
    assert context["password"] == PASS
    assert context["totp_secret"] == SEED
    now = time.time()
    assert context["otp"] in {totp_now(SEED, at=now), totp_now(SEED, at=now - 30)}


def test_login_context_without_a_seed_has_an_empty_code():
    context = login_context(_recipe("http://x/", [], ready=""), Credentials("wellfound", USER, PASS))
    assert context["otp"] == ""
    assert context["totp_secret"] == ""


def test_can_relogin_needs_both_steps_and_credentials(tmp_path):
    with_steps = _recipe("http://x/", _password_steps("http://x/"))
    without_steps = _recipe("http://x/", [])

    bare = _settings(tmp_path)
    assert not can_relogin(with_steps, bare)
    assert "WELLFOUND_LOGIN_USERNAME" in why_not(with_steps, bare)

    stocked = _settings(tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS)
    assert can_relogin(with_steps, stocked)
    assert why_not(with_steps, stocked) == ""
    assert not can_relogin(without_steps, stocked)
    assert "login.steps" in why_not(without_steps, stocked)


# -- the flow against a page ------------------------------------------------------


async def _open(settings: Settings, url: str):
    runner = BrowserRunner(settings, headless=True)
    await runner.start()
    return runner


def test_an_expired_session_is_signed_in_again_and_saved(page_url, tmp_path):
    """The whole point: check fails, steps replay, check passes, session
    exported, and the adapter says a sign-in happened."""
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS
    )
    recipe = _recipe(login_url, _password_steps(login_url))
    adapter = RecipeAdapter(recipe, settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                relogged = await adapter.ensure_logged_in(page)
                who = await page.evaluate("localStorage.getItem('mock.session')")
                return relogged, who

    relogged, who = asyncio.run(run())
    assert relogged is True
    assert who == USER
    saved = settings.session_dir / "wellfound.storage_state.json"
    assert saved.is_file()
    state = json.loads(saved.read_text(encoding="utf-8"))
    assert any(c["name"] == "mock_sid" for c in state["cookies"])
    assert (settings.browser_profile_dir / "wellfound" / ".login-verified").is_file()


def test_a_live_session_is_left_alone(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS
    )
    recipe = _recipe(login_url, _password_steps(login_url))
    adapter = RecipeAdapter(recipe, settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await page.goto(login_url)
                await page.evaluate(f"localStorage.setItem('mock.session', '{USER}')")
                return await adapter.ensure_logged_in(page)

    assert asyncio.run(run()) is False


def test_without_credentials_the_row_is_told_what_to_set(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(tmp_path)
    adapter = RecipeAdapter(_recipe(login_url, _password_steps(login_url)), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await adapter.ensure_logged_in(page)

    with pytest.raises(AuthenticationRequired) as excinfo:
        asyncio.run(run())
    message = str(excinfo.value)
    assert "not logged in" in message
    assert "WELLFOUND_LOGIN_USERNAME" in message


def test_relogin_switch_off_keeps_the_old_behaviour(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS,
        session_relogin=False,
    )
    adapter = RecipeAdapter(_recipe(login_url, _password_steps(login_url)), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await adapter.ensure_logged_in(page)

    with pytest.raises(AuthenticationRequired):
        asyncio.run(run())


def test_a_wrong_password_names_the_step_and_hides_the_secret(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password="wrong-pass-xyz"
    )
    adapter = RecipeAdapter(_recipe(login_url, _password_steps(login_url)), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await adapter.ensure_logged_in(page)

    with pytest.raises(AuthenticationRequired) as excinfo:
        asyncio.run(run())
    message = str(excinfo.value)
    assert "automatic sign-in failed" in message
    assert "login step 5" in message
    assert "wrong-pass-xyz" not in message
    assert "WELLFOUND_LOGIN_" in message


# -- Microsoft SSO ----------------------------------------------------------------


def _microsoft_steps(login_url: str, ms_query: str = "") -> list[Step]:
    """Redirect-style: the same tab goes to Microsoft and comes back."""
    return [
        Step(
            "goto",
            {"url": f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
                    f"?return={login_url}{ms_query}"},
            index=1, phase="login",
        ),
        Step(
            "microsoft_sso",
            {"username": "{{ username }}", "password": "{{ password }}",
             "totp_secret": "{{ totp_secret }}"},
            index=2, phase="login",
        ),
        Step("wait_for", {"selector": "#user-menu"}, index=3, phase="login"),
    ]


async def _route_microsoft(context) -> None:
    async def serve(route):
        await route.fulfill(status=200, content_type="text/html", body=MOCK_MS)

    await context.route("https://login.microsoftonline.com/**", serve)


def _ms_result(page):
    return page.evaluate("JSON.parse(localStorage.getItem('mock.ms') || 'null')")


def test_microsoft_redirect_sign_in_with_a_code(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS,
        wellfound_login_totp_secret=SEED,
    )
    adapter = RecipeAdapter(_recipe(login_url, _microsoft_steps(login_url)), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await _route_microsoft(context)
                relogged = await adapter.ensure_logged_in(page)
                return relogged, await _ms_result(page)

    relogged, result = asyncio.run(run())
    assert relogged is True
    assert result["user"] == USER
    assert result["password_ok"] is True
    assert result["kmsi"] is True, "Stay signed in? must be answered Yes with the box ticked"
    now = time.time()
    assert result["code"] in {totp_now(SEED, at=now), totp_now(SEED, at=now - 30), totp_now(SEED, at=now - 60)}
    assert result["path"] == ["password", "code", "kmsi"]


def test_microsoft_push_prompt_is_steered_to_the_code_method(page_url, tmp_path):
    """Nobody is holding the phone. The Authenticator approval screen must be
    answered by choosing the verification-code method instead."""
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS,
        wellfound_login_totp_secret=SEED,
    )
    adapter = RecipeAdapter(
        _recipe(login_url, _microsoft_steps(login_url, "&push=1")), settings=settings
    )

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await _route_microsoft(context)
                await adapter.ensure_logged_in(page)
                return await _ms_result(page)

    result = asyncio.run(run())
    assert result["path"] == ["password", "push", "methods", "code", "kmsi"]
    assert result["code"]


def test_microsoft_push_prompt_without_a_seed_says_what_the_account_needs(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS,
    )
    adapter = RecipeAdapter(
        _recipe(login_url, _microsoft_steps(login_url, "&push=1")), settings=settings
    )

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await _route_microsoft(context)
                await adapter.ensure_logged_in(page)

    with pytest.raises(AuthenticationRequired) as excinfo:
        asyncio.run(run())
    assert "Authenticator" in str(excinfo.value)
    assert "_LOGIN_TOTP_SECRET" in str(excinfo.value)


def test_microsoft_popup_sign_in_finishes_when_the_popup_closes(page_url, tmp_path):
    """noon's shape: the platform opens Microsoft in a popup, the popup posts
    back and closes itself. The action must find that tab and stop when it
    is gone."""
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password=PASS,
    )
    steps = [
        Step("goto", {"url": f"{login_url}?ms=nocode"}, index=1, phase="login"),
        Step("click", {"selector": "#sso"}, index=2, phase="login"),
        Step(
            "microsoft_sso",
            {"username": "{{ username }}", "password": "{{ password }}",
             "totp_secret": "{{ totp_secret }}"},
            index=3, phase="login",
        ),
        Step("wait_for", {"selector": "#user-menu"}, index=4, phase="login"),
    ]
    adapter = RecipeAdapter(_recipe(login_url, steps), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await _route_microsoft(context)
                relogged = await adapter.ensure_logged_in(page)
                return relogged, await _ms_result(page), len(context.pages)

    relogged, result, open_pages = asyncio.run(run())
    assert relogged is True
    assert result["path"] == ["password", "kmsi"]
    assert open_pages == 1, "the popup closed itself"


def test_microsoft_wrong_password_is_reported_without_the_password(page_url, tmp_path):
    login_url = _login_url(page_url)
    settings = _settings(
        tmp_path, wellfound_login_username=USER, wellfound_login_password="not-it-9",
    )
    adapter = RecipeAdapter(_recipe(login_url, _microsoft_steps(login_url)), settings=settings)

    async def run():
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (context, page):
                await _route_microsoft(context)
                await adapter.ensure_logged_in(page)

    with pytest.raises(AuthenticationRequired) as excinfo:
        asyncio.run(run())
    message = str(excinfo.value)
    assert "rejected the password" in message
    assert "not-it-9" not in message
