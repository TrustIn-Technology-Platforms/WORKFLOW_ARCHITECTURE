"""Decisions the adapter takes before any browser is opened."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.models import Advert, EmailStep, Outcome, ParsedDocument
from app.platforms.adapter import RecipeAdapter
from app.platforms.recipe import Recipe


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
