"""The "Create New Role" Submit, pinned against the screen that broke it.

On Railway, 2026-09-16, a row died here: the recipe's first candidate was a
bare `text='Submit'`, which `find()` resolved to a greyed-out Submit on the
inbox page *behind* the modal, and the "Chrome extension not detected" toast
covered it and ate the clicks. Nothing in the suite would have caught that -
`recipe.py` only checks that exactly one submit step exists, never what it
resolves to.

So these tests load the step out of `platforms/noon.yaml` and drive the real
`find` / `action_click` / `action_dismiss` against a reconstruction of that
screen (`tests/fixtures/pages/mock-noon-role-modal.html`). The old selector and
the new one are asserted side by side, so the regression needs no revert to
show itself: reorder the ladder in the YAML and these fail.

What this cannot prove is the markup. The fixture is built from the failure
description, not from noon - see the blockers in docs/platforms/noon.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

from app.config import get_settings, reset_settings_cache
from app.platforms.actions import StepRun, action_click, action_dismiss, find
from app.platforms.browser import BrowserRunner
from app.platforms.recipe import Step, load_recipe

FIXTURE = "mock-noon-role-modal.html"


def _noon_steps() -> tuple[Step, Step]:
    """The real submit step and the dismiss step that clears the way for it.

    Read from the YAML rather than copied here, so that reordering the
    candidate ladder in production breaks this file.
    """
    reset_settings_cache()
    recipe = load_recipe(Path(get_settings().platform_config_dir) / "noon.yaml")
    submit = next(step for step in recipe.steps if step.submit)
    before = recipe.steps[: recipe.steps.index(submit)]
    dismiss = next(step for step in reversed(before) if step.action == "dismiss")
    return submit, dismiss


def _selectors(step: Step) -> list[str]:
    selector = step.params["selector"]
    return [selector] if isinstance(selector, str) else list(selector)


def _drive(page_url: str, body: Callable[[Any], Awaitable[Any]], *, clash: bool = False):
    url = page_url.replace("mock-sequence.html", FIXTURE) + ("?clash=1" if clash else "")

    async def run() -> Any:
        reset_settings_cache()
        settings = get_settings()
        async with BrowserRunner(settings, headless=True) as runner:
            async with runner.context() as (_context, page):
                await page.goto(url)
                return await body(page)

    return asyncio.run(run())


async def _button_id(locator) -> str:
    """Which button the locator is really pointing at.

    The ancestor-hop candidate lands on the inner `<span>Submit</span>`, not on
    the button, and it is the button that matters.
    """
    return await locator.evaluate("el => (el.closest('button') || el).id")


# ----------------------------------------------------------------------
# what the old selector did, and what the new one does
# ----------------------------------------------------------------------


def test_the_bare_submit_selector_still_reaches_the_greyed_out_decoy(page_url):
    """The regression anchor. `find()` returns the first *visible* match and a
    disabled button is visible, so the 2026-09-16 selector picks the inbox's
    Submit - which is why the ladder below has to exist."""

    async def body(page):
        locator = await find(
            StepRun(page=page, params={"selector": ["text='Submit'"]}, timeout_ms=3_000)
        )
        return {
            "id": await _button_id(locator),
            "disabled": await locator.evaluate("el => (el.closest('button')||el).disabled"),
        }

    hit = _drive(page_url, body)
    assert hit["id"] == "decoy", "a bare text='Submit' must still be shown to reach the wrong control"
    assert hit["disabled"] is True, "the decoy is the greyed-out one - the fixture is not honest otherwise"


def test_the_recipe_ladder_reaches_the_modals_own_control(page_url):
    """The fix, as loaded from the YAML. Reordering the candidates - putting the
    bare text back in front - resolves to `decoy` and fails here."""
    submit, _ = _noon_steps()

    async def body(page):
        locator = await find(
            StepRun(page=page, params={"selector": _selectors(submit)}, timeout_ms=5_000)
        )
        return await _button_id(locator)

    assert _drive(page_url, body) == "real-submit"


def test_the_ladder_reaches_create_a_new_role_anyway_when_the_name_clashes(page_url):
    """The other live variant: an existing name leaves the modal with no Submit
    at all. Only the first candidate is scoped tightly enough to survive it -
    the ancestor hop would walk past the modal and find the inbox's Submit."""
    submit, _ = _noon_steps()

    async def body(page):
        locator = await find(
            StepRun(page=page, params={"selector": _selectors(submit)}, timeout_ms=5_000)
        )
        return {
            "id": await _button_id(locator),
            "text": (await locator.inner_text()).strip(),
            "submits_left_in_modal": await page.evaluate(
                "() => [...document.querySelectorAll('#role-modal button')]"
                ".filter(b => b.textContent.trim() === 'Submit').length"
            ),
        }

    hit = _drive(page_url, body, clash=True)
    assert hit["submits_left_in_modal"] == 0
    assert hit["id"] == "real-submit"
    assert hit["text"] == "Create a new role anyway"


# ----------------------------------------------------------------------
# the toast
# ----------------------------------------------------------------------


def test_the_toast_really_intercepts_an_unforced_click(page_url):
    """Without this the fixture would be theatre: a toast that does not swallow
    the click proves nothing about the step that had to dismiss one. The
    message here is the Railway symptom, verbatim."""
    submit, _ = _noon_steps()

    async def body(page):
        try:
            await action_click(
                StepRun(
                    page=page,
                    params={"selector": _selectors(submit), "force": False},
                    timeout_ms=3_000,
                )
            )
        except Exception as exc:  # playwright's TimeoutError; the engine wraps it
            return {"error": str(exc), "submitted": await page.evaluate("() => window.__submitted")}
        return {"error": "", "submitted": await page.evaluate("() => window.__submitted")}

    outcome = _drive(page_url, body)
    assert outcome["error"], "the toast must block the click, or the fixture proves nothing"
    assert "intercepts pointer events" in outcome["error"]
    assert 'direction="up"' in outcome["error"]
    assert outcome["submitted"] is False


def test_force_does_not_beat_the_toast(page_url):
    """Measured, against the belief the recipe used to carry. `force` skips the
    interception *check*, not the dispatch: the event still lands at the
    target's centre point, and whatever is painted there receives it. So force
    against a live toast is worse than no force - it turns a loud failure into
    a silent wrong click, and the engine then records the row as submitted."""
    submit, _ = _noon_steps()

    async def body(page):
        await action_click(
            StepRun(
                page=page,
                params={"selector": _selectors(submit), "force": True},
                timeout_ms=5_000,
            )
        )
        return {
            "clicks": await page.evaluate("() => window.__clicks"),
            "submitted": await page.evaluate("() => window.__submitted"),
        }

    outcome = _drive(page_url, body)
    assert outcome["clicks"] == ["toast"], "the forced click lands on the toast, not on Submit"
    assert outcome["submitted"] is False, "and nothing was created - yet nothing raised"


def test_the_submit_step_does_not_force(page_url):
    """Guards the conclusion above against being undone in the YAML. The
    dismiss step is what clears the toast; if it ever misses, the run has to say
    so at this step rather than sail past it."""
    submit, _ = _noon_steps()
    assert not submit.params.get("force"), (
        "force cannot beat an overlay - it only hides the error that names it. "
        "See test_force_does_not_beat_the_toast."
    )


# ----------------------------------------------------------------------
# the dismiss, and why it is scoped
# ----------------------------------------------------------------------


@pytest.mark.parametrize("clash", [False, True], ids=["fresh-name", "name-clash"])
def test_the_scoped_dismiss_clears_the_toast_and_leaves_the_modal_standing(page_url, clash):
    """The whole step in order: dismiss the toast, then click. Both modal
    variants end with the role actually created."""
    submit, dismiss = _noon_steps()

    async def body(page):
        await action_dismiss(
            StepRun(page=page, params={"selector": _selectors(dismiss)}, timeout_ms=3_000)
        )
        state = {
            "toast_gone": await page.evaluate("() => !document.getElementById('toast')"),
            "modal_alive": await page.evaluate("() => !!document.getElementById('role-modal')"),
        }
        await action_click(
            StepRun(
                page=page,
                params={"selector": _selectors(submit), "force": submit.params.get("force", False)},
                timeout_ms=5_000,
            )
        )
        state["submitted"] = await page.evaluate("() => window.__submitted")
        state["last_click"] = (await page.evaluate("() => window.__clicks"))[-1]
        return state

    state = _drive(page_url, body, clash=clash)
    assert state["toast_gone"] is True
    assert state["modal_alive"] is True, "dismissing the toast must not take the modal with it"
    assert state["last_click"] == "real-submit"
    assert state["submitted"] is True


def test_an_unscoped_close_selector_would_have_closed_the_modal(page_url):
    """Why the dismiss step names the toast instead of any close button: the
    modal carries its own [aria-label='Close'], earlier in the DOM, and
    `action_dismiss` swallows whatever it hits."""

    async def body(page):
        await action_dismiss(
            StepRun(page=page, params={"selector": ["[aria-label='Close']"]}, timeout_ms=3_000)
        )
        return {
            "modal_alive": await page.evaluate("() => !!document.getElementById('role-modal')"),
            "toast_alive": await page.evaluate("() => !!document.getElementById('toast')"),
        }

    state = _drive(page_url, body)
    assert state["modal_alive"] is False, "the unscoped selector destroys the modal - that is the point"
    assert state["toast_alive"] is True, "and leaves the toast exactly where it was"
