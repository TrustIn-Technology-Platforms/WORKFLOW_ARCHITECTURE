"""The funding-stage writer's browser half, against a mock of Juicebox's select.

The live proof of the filter editor is in docs/platforms/juicebox.md; what a
live run cannot show by eye is what the *absence* of a stage does, because the
select is never empty - Juicebox's own AI pre-selects it. This drives the real
`_set_stages` against `mock-juicebox-stages.html`, whose select arrives holding
`seed,series_a,series_b,series_c`: the value read off the live Axle search.

What it pins is the rule of D-022, both halves side by side:

- A stage the client STATED narrows the select to Seed + Series A.
- A stage Claude INFERRED does not reach the select at all, so the search keeps
  Juicebox's own wider pre-selection - the row note "left as Juicebox set it"
  is true, and nothing is cleared.

The 2026-09-22 Axle run is the regression: the document named no round, Claude
inferred Series A, and the saved search came back holding two Company Funding
Stages under a row that said OK. Reverting `stage_for_filter` fails the first
test here, because the value narrows to `seed,series_a`.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings, reset_settings_cache
from app.platforms.browser import BrowserRunner
from app.platforms.juicebox_sourcing import (
    _MARK_STAGE_SELECT,
    _set_stages,
    stage_for_filter,
)


async def _drive(page_url: str, stage: str | None, *, stated: bool) -> dict:
    """Run the real writer over the real decision, on the mock select."""
    url = page_url.replace("mock-sequence.html", "mock-juicebox-stages.html")
    reset_settings_cache()
    settings = get_settings()
    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (_context, page):
            await page.goto(url)
            before = await page.evaluate(_MARK_STAGE_SELECT)
            result = await _set_stages(page, stage_for_filter(stage, stated=stated))
            after = await page.evaluate(_MARK_STAGE_SELECT)
            return {
                "before": str(before.get("value") or ""),
                "after": str(after.get("value") or ""),
                "labels": result.labels,
                "missing": result.missing,
                "offered": result.offered,
            }


def test_an_inferred_stage_leaves_juiceboxs_own_selection_alone(page_url):
    """The Axle regression. The stage was a guess, so the select keeps what
    Juicebox chose - wider than the guess, and nothing is claimed on the row."""
    state = asyncio.run(_drive(page_url, "Series A", stated=False))
    assert state["before"] == "seed,series_a,series_b,series_c"
    assert state["after"] == "seed,series_a,series_b,series_c"
    # Nothing added and nothing refused: the run made no claim about a filter
    # it did not set.
    assert state["labels"] == []
    assert state["missing"] == []


def test_a_stated_stage_still_narrows_the_select(page_url):
    """The other half of the rule: a stage the client wrote does set the
    filter, Seed up to its own, deselecting what Juicebox's AI added past it."""
    state = asyncio.run(_drive(page_url, "Series A", stated=True))
    assert state["before"] == "seed,series_a,series_b,series_c"
    assert state["after"] == "seed,series_a"
    assert state["labels"] == ["Seed", "Series A"]
    assert state["missing"] == []


def test_the_stages_select_is_the_last_in_the_section_not_the_scope_one(page_url):
    """The section holds a scope select before the stages one. Marking the
    first would write the scope; the menu read proves the right one opened."""
    state = asyncio.run(_drive(page_url, "Series A", stated=True))
    assert state["offered"] == [
        "pre_seed", "seed", "series_a", "series_b", "series_c", "series_d", "ipo",
    ]
