"""The recorder must produce a recipe from what a person actually did.

Playwright stands in for the human here, performing the same flow an operator
would. What matters is that the captured selectors are stable ones and that
typed values are mapped back to the document they came from.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings, reset_settings_cache
from app.platforms.browser import BrowserRunner
from app.platforms.recorder import RECORDER_JS, Recording
from tests.test_engine import _document


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    reset_settings_cache()
    yield
    reset_settings_cache()


async def _drive(url: str) -> Recording:
    recording = Recording(_document())
    settings = get_settings()

    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (context, page):
            await context.expose_binding(
                "__record", lambda source, payload: recording.add(payload or {})
            )
            await context.add_init_script(RECORDER_JS)
            await page.goto(url)

            # Exactly what a person would do, in the same order.
            await page.fill("#title", "Senior Recruitment Consultant")
            await page.fill("#location", "Manchester (hybrid)")
            await page.select_option("#employmentType", "FULL_TIME")
            await page.click("#description")
            await page.keyboard.type("We are hiring.")
            await page.click("h1")  # blur, so the editor reports its content
            await page.click("#save-role")

            await page.click("#tab-sequence")

            for subject in ("Senior Recruitment Consultant - Manchester", "Following up"):
                await page.click("#add-step")
                await page.fill("#subject", subject)
                await page.click("#step-body")
                await page.keyboard.type("Body copy.")
                await page.click("#subject")  # blur the editor
                await page.click("#save-step")

            await page.click("#save-sequence")
            await page.wait_for_timeout(250)

    return recording


def test_recorder_captures_the_flow(page_url):
    recording = asyncio.run(_drive(page_url))
    kinds = [e.type for e in recording.events]

    assert "fill" in kinds
    assert "select" in kinds
    assert "click" in kinds
    assert "fill_rich" in kinds, "a contenteditable must be captured on blur"

    # Stable selectors, not generated nth-of-type paths.
    title = next(e for e in recording.events if e.type == "fill" and "title" in e.selectors[0])
    assert title.selectors[0] in ("#title", "label=Role title")

    # The add-step / subject / save-step block happens twice.
    add_steps = [e for e in recording.events if e.type == "click" and "add-step" in str(e.selectors)]
    assert len(add_steps) == 2, "each email must appear as its own pass"


def test_typed_values_become_templates(page_url):
    recording = asyncio.run(_drive(page_url))
    yaml_text = recording.to_yaml("mockrec", "Mock ATS", page_url, "email_sequence")

    # Values typed from the document are written as their source, not as literals.
    assert "{{ advert.title }}" in yaml_text
    assert "{{ advert.location }}" in yaml_text
    assert "{{ email.subject }}" in yaml_text

    # The literal may appear in a `# typed:` comment, but never as a live value.
    live = "\n".join(
        line for line in yaml_text.split("steps:")[1].splitlines()
        if not line.strip().startswith("#")
    )
    assert "Senior Recruitment Consultant" not in live

    assert "key: mockrec" in yaml_text
    assert "enabled: false" in yaml_text
    assert "REPEAT" in yaml_text, "the per-email repeat must be flagged"


def test_recorded_yaml_is_parseable(page_url):
    import yaml

    recording = asyncio.run(_drive(page_url))
    text = recording.to_yaml("mockrec", "Mock ATS", page_url, "email_sequence")
    data = yaml.safe_load(text)

    assert data["key"] == "mockrec"
    assert isinstance(data["steps"], list) and data["steps"]
    assert all("action" in step for step in data["steps"])


def test_a_ui_built_entirely_from_divs_is_still_recorded(page_url):
    """No button, no role, no label, no id - only a pointer cursor and text.

    Real platforms are built this way, and the whole recorder is worthless on
    them if it waits for semantic markup. What it has to fall back on is what a
    person sees: the words on the control.
    """
    url = page_url.replace("mock-sequence.html", "div-ui.html")
    recording = asyncio.run(_drive_div_ui(url))

    clicks = [e for e in recording.events if e.type == "click"]
    assert clicks, "a div-built control must still be recorded as a click"

    labels = {e.text for e in clicks}
    assert "Create new role" in labels
    assert "Accept all" in labels

    # Text is the only stable handle such a page offers, so it must be the
    # selector that is offered first - ahead of a positional CSS path.
    create = next(e for e in clicks if e.text == "Create new role")
    assert create.selectors[0] == 'text="Create new role"'

    # The wrapper is never the target: text= matches the innermost element.
    assert not create.selectors[0].startswith("body")

    # Round-trip it: the selector carries quotes, so what matters is the value
    # after YAML parsing, not the escaping in the file.
    import yaml

    data = yaml.safe_load(recording.to_yaml("divui", "Div UI", url, "email_sequence"))
    recorded = [
        s
        for step in data["steps"]
        for s in ([step["selector"]] if isinstance(step.get("selector"), str) else step.get("selector") or [])
    ]
    assert 'text="Create new role"' in recorded


async def _drive_div_ui(url: str) -> Recording:
    recording = Recording(_document())
    settings = get_settings()

    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (context, page):
            await context.expose_binding(
                "__record", lambda source, payload: recording.add(payload or {})
            )
            await context.add_init_script(RECORDER_JS)
            await page.goto(url)

            await page.click("text=Create new role")
            await page.fill("input", "Senior Recruitment Consultant")
            await page.click("[contenteditable=true]")
            await page.keyboard.type("We are hiring.")
            await page.click("text=Home")  # blur, so the editor reports content
            await page.click("text=Accept all")
            await page.wait_for_timeout(250)

    return recording
