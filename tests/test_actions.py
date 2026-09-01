"""Action-level behaviour that the mock sequence page cannot show.

`fill_rich` against a CodeMirror editor is the case here: Wellfound's job
description is EasyMDE, whose document lives in JS and which ignores the paste
event every other editor accepts. The write has to go through the instance.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings, reset_settings_cache
from app.platforms.actions import StepRun, action_fill_rich, action_tags
from app.platforms.browser import BrowserRunner


async def _fill(page_url: str, params: dict) -> dict:
    url = page_url.replace("mock-sequence.html", "mock-codemirror.html")
    reset_settings_cache()
    settings = get_settings()
    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (_context, page):
            await page.goto(url)
            await action_fill_rich(StepRun(page=page, params=params))
            return {
                "doc": await page.evaluate("() => document.querySelector('.CodeMirror').CodeMirror.getValue()"),
                "model": await page.get_attribute("#model", "data-value"),
                "textarea": await page.input_value("#react-simplemde-editor"),
            }


def test_fill_rich_writes_markdown_through_the_codemirror_instance(page_url):
    """`value` (already Markdown) lands in the editor, the wrapper's model and
    the backing textarea - the three places the real form reads from."""
    state = asyncio.run(
        _fill(
            page_url,
            {
                "selector": ".CodeMirror",
                "value": "**About:**\n\n- one\n- two",
                "value_html": "<p><strong>About:</strong></p><ul><li>one</li><li>two</li></ul>",
            },
        )
    )
    assert state["doc"] == "**About:**\n\n- one\n- two"
    assert state["model"] == state["doc"], "React wrapper must see the change event"
    assert state["textarea"] == state["doc"], "cm.save() syncs the hidden textarea"


def test_fill_rich_codemirror_falls_back_to_text_when_only_html_is_given(page_url):
    state = asyncio.run(
        _fill(
            page_url,
            {"selector": ".CodeMirror", "value_html": "<p>Plain <strong>copy</strong></p>"},
        )
    )
    assert state["doc"] == "Plain copy"


# ----------------------------------------------------------------------
# tags - Wellfound's Skills field
# ----------------------------------------------------------------------


async def _tags(page_url: str, params: dict) -> dict:
    url = page_url.replace("mock-sequence.html", "mock-tags.html")
    reset_settings_cache()
    settings = get_settings()
    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (_context, page):
            await page.goto(url)
            await action_tags(StepRun(page=page, params=params, timeout_ms=4_000))
            return {
                "chips": await page.evaluate(
                    "() => [...document.querySelectorAll('#chips .chip')]"
                    ".map(el => el.textContent)"
                ),
                "left_in_box": await page.input_value("#skills"),
            }


def test_tags_commits_each_skill_the_platform_offers(page_url):
    state = asyncio.run(
        _tags(
            page_url,
            {
                "selector": "input[placeholder='e.g. Python, React']",
                "value": "Python, Kubernetes, Terraform",
                "settle_ms": 150,
            },
        )
    )
    assert state["chips"] == ["Python", "Kubernetes", "Terraform"]
    assert state["left_in_box"] == ""


def test_tags_drops_a_skill_the_platform_does_not_know(page_url):
    """An unknown skill must not survive in the box, where the real form would
    discard it on blur - or worse, prefix the next skill typed after it."""
    state = asyncio.run(
        _tags(
            page_url,
            {
                "selector": "input[placeholder='e.g. Python, React']",
                "value": "Python, Kubeflow, React",
                "settle_ms": 150,
            },
        )
    )
    assert state["chips"] == ["Python", "React"], "the unknown one is skipped"
    assert state["left_in_box"] == "", "and is cleared, not left to poison the next"


def test_tags_honours_max_and_keeps_a_slash_inside_a_skill_name(page_url):
    state = asyncio.run(
        _tags(
            page_url,
            {
                "selector": "input[placeholder='e.g. Python, React']",
                "value": "CI/CD, Go, AWS, React",
                "max": 2,
                "settle_ms": 150,
            },
        )
    )
    assert state["chips"] == ["CI/CD", "Go"]


def test_tags_with_no_value_skips_instead_of_failing(page_url):
    state = asyncio.run(
        _tags(
            page_url,
            {
                "selector": "input[placeholder='e.g. Python, React']",
                "value": "   ",
                "settle_ms": 150,
            },
        )
    )
    assert state["chips"] == []


# ----------------------------------------------------------------------
# goto_until - navigation proven, not trusted
# ----------------------------------------------------------------------


async def _goto_until(page_url: str, params: dict) -> str:
    reset_settings_cache()
    settings = get_settings()
    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (_context, page):
            from app.platforms.actions import action_goto_until

            await action_goto_until(StepRun(page=page, params=params, timeout_ms=3_000))
            return page.url


def test_goto_until_returns_once_the_page_proves_out(page_url):
    landed = asyncio.run(
        _goto_until(page_url, {"url": page_url, "selector": "#role-form"})
    )
    assert landed.startswith(page_url.rsplit("/", 1)[0])


def test_goto_until_names_where_the_app_kept_landing(page_url):
    """The Wellfound case: logged in, no error, and the wanted form nowhere on
    the page because the SPA answered the deep link with an interstitial. The
    failure must say where it actually landed, not just that a selector timed
    out - that one line is the difference between reading the artifact and
    re-guessing."""
    import pytest

    from app.models import PlatformError

    with pytest.raises(PlatformError, match="kept landing"):
        asyncio.run(
            _goto_until(
                page_url,
                {"url": page_url, "selector": "#not-on-this-page", "attempts": 2},
            )
        )
