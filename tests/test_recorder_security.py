"""The recorder must never write a credential into a recipe file.

A recipe is written to disk and read by people. An earlier version recorded a
real sign-in form - email, password and all - straight into `platforms/`, which
is also a directory that would have been committed. These tests exist so that
cannot come back.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings, reset_settings_cache
from app.platforms.browser import BrowserRunner
from app.platforms.recorder import RECORDER_JS, Recorded, Recording, _is_sensitive

PASSWORD = "hunter2-do-not-record"
EMAIL = "someone@example.com"

# Modelled on the Microsoft Entra sign-in page, which is what noon.ai federates
# to - the exact form that leaked before.
LOGIN_PAGE = """
<!doctype html><html><body>
  <form>
    <input id="i0116" name="loginfmt" placeholder="Email or phone">
    <input id="i0118" name="passwd" type="password" placeholder="Password">
    <input id="KmsiCheckboxField" name="DontShowAgain" type="checkbox">
    <button id="idSIButton9">Sign in</button>
  </form>
</body></html>
"""

# An ordinary app page that happens to carry a secret-shaped field.
APP_PAGE = """
<!doctype html><html><body>
  <input id="title" name="title" placeholder="Role title">
  <input id="api-token" name="apiToken" placeholder="API token">
  <input id="pw" name="userPassword" type="text" placeholder="Password">
  <button id="save">Save</button>
</body></html>
"""


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    reset_settings_cache()
    yield
    reset_settings_cache()


async def _record_on(url: str, html: str, actions) -> Recording:
    recording = Recording()
    async with BrowserRunner(get_settings(), headless=True) as runner:
        async with runner.context() as (context, page):
            await context.expose_binding(
                "__record", lambda source, payload: recording.add(payload or {})
            )
            await context.add_init_script(RECORDER_JS)
            await context.route(
                "**/*",
                lambda route: asyncio.ensure_future(
                    route.fulfill(status=200, content_type="text/html", body=html)
                ),
            )
            await page.goto(url)
            await actions(page)
            await page.wait_for_timeout(200)
    return recording


def test_nothing_is_recorded_on_an_identity_provider():
    async def act(page):
        await page.fill("#i0116", EMAIL)
        await page.fill("#i0118", PASSWORD)
        await page.check("#KmsiCheckboxField")
        await page.click("#idSIButton9")

    recording = asyncio.run(
        _record_on("https://login.microsoftonline.com/common/oauth2/authorize",
                   LOGIN_PAGE, act)
    )

    assert recording.events == [], "an identity provider page must record nothing"
    text = recording.to_yaml("x", "X", "https://example.com", "advert")
    assert PASSWORD not in text
    assert EMAIL not in text


def test_nothing_is_recorded_on_a_login_path():
    async def act(page):
        await page.fill("#i0116", EMAIL)
        await page.fill("#i0118", PASSWORD)

    recording = asyncio.run(_record_on("https://www.noon.ai/log-in", LOGIN_PAGE, act))
    assert recording.events == []


def test_secret_shaped_fields_are_skipped_on_an_ordinary_page():
    async def act(page):
        await page.fill("#title", "Senior Recruitment Consultant")
        await page.fill("#api-token", "sk-live-abcdef")
        await page.fill("#pw", PASSWORD)
        await page.click("#save")

    recording = asyncio.run(_record_on("https://www.noon.ai/portal", APP_PAGE, act))
    text = recording.to_yaml("x", "X", "https://www.noon.ai/portal", "advert")

    # The legitimate field is captured...
    assert any("#title" in " ".join(e.selectors) for e in recording.events)
    # ...and the secret-shaped ones are not, even as a type=text field.
    assert PASSWORD not in text
    assert "sk-live-abcdef" not in text
    assert not any("token" in " ".join(e.selectors).lower() for e in recording.events)


def test_python_side_filter_is_independent_of_the_browser():
    """The last line of defence must hold even if the JS filter is bypassed."""
    assert _is_sensitive(Recorded(type="fill", selectors=['input[name="passwd"]']))
    assert _is_sensitive(Recorded(type="fill", selectors=["#api-token"]))
    assert _is_sensitive(
        Recorded(type="click", selectors=["#ok"], url="https://login.microsoftonline.com/x")
    )
    assert _is_sensitive(Recorded(type="fill", selectors=["#f"], url="https://a.com/sign-in"))
    assert not _is_sensitive(Recorded(type="fill", selectors=["#title"], url="https://a.com/portal"))


def test_a_bypassed_event_is_still_dropped():
    recording = Recording()
    recording.add({
        "type": "fill",
        "selectors": ['input[name="passwd"]'],
        "value": PASSWORD,
        "url": "https://www.noon.ai/portal",
    })
    assert recording.events == []
    assert recording.skipped_sensitive == 1
    assert PASSWORD not in recording.to_yaml("x", "X", "https://x", "advert")
