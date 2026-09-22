"""Juicebox ranks criteria rather than splitting them, so the ranking is the policy.

The second half drives the real dialog functions against a mock MUI criteria
dialog served over HTTP. On 2026-09-22 a production run saved a search with 12
job titles and 28 companies, left the ranking empty, and reported it as one
sentence on a row that otherwise read OK: "search criteria not set: Could not
add another criterion row." That sentence stood for at least four unrelated
faults and told the recruiter nothing to do about any of them.

The pre-fix JavaScript is kept here verbatim as `LEGACY_*`, so each regression
is visible side by side - the old lookup misses the control, the new one finds
it - without reverting anything to prove it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings, get_settings, reset_settings_cache
from app.models import PlatformError
from app.platforms import juicebox_criteria as jc
from app.platforms.browser import BrowserRunner
from app.platforms.criteria_ai import DraftCriteria, DraftCriterion
from app.platforms.juicebox_criteria import MAX_CRITERIA, rank_criteria


def _draft(**kwargs) -> DraftCriteria:
    return DraftCriteria(
        dealbreakers=[
            DraftCriterion(category="Hard skills", text="The candidate has deep AWS experience."),
            DraftCriterion(category="Location", text="The candidate is based in London."),
        ],
        baseline=[
            DraftCriterion(category="Seniority", text="The candidate has 5+ years in platform roles.")
        ],
        traits_to_avoid=["The candidate does not require visa sponsorship."],
        **kwargs,
    )


def test_dealbreakers_rank_above_baseline_above_disqualifiers():
    """Position is the only weighting Juicebox has, so order is the policy."""
    assert rank_criteria(_draft()) == [
        "The candidate has deep AWS experience.",
        "The candidate is based in London.",
        "The candidate has 5+ years in platform roles.",
        "The candidate does not require visa sponsorship.",
    ]


def test_a_requirement_stated_twice_does_not_take_two_slots():
    draft = _draft()
    draft.baseline.append(
        DraftCriterion(category="Hard skills", text="The candidate has deep AWS experience")
    )
    ranked = rank_criteria(draft)
    assert len([c for c in ranked if "AWS" in c]) == 1


def test_blank_criteria_are_dropped():
    draft = _draft()
    draft.baseline.append(DraftCriterion(category="Hard skills", text="   "))
    assert all(c.strip() for c in rank_criteria(draft))


def test_the_list_is_capped_so_the_ranking_stays_meaningful():
    draft = DraftCriteria(
        dealbreakers=[
            DraftCriterion(category="Hard skills", text=f"The candidate knows tool number {n}.")
            for n in range(MAX_CRITERIA + 5)
        ]
    )
    assert len(rank_criteria(draft)) == MAX_CRITERIA


def test_whitespace_is_normalised():
    draft = DraftCriteria(
        dealbreakers=[DraftCriterion(category="Hard skills", text="The  candidate\n has   AWS.")]
    )
    assert rank_criteria(draft) == ["The candidate has AWS."]


# ----------------------------------------------------------------------
# The dialog itself, against tests/fixtures/pages/mock-juicebox-criteria.html
# ----------------------------------------------------------------------

LEGACY_DIALOG = """() => [...document.querySelectorAll('[class*=MuiDialog-paper],[role=dialog]')]
  .find(el => (el.innerText || '').trim().startsWith('Criteria')) || null"""

LEGACY_CLICK_IN_DIALOG = """(label) => {
  const dialog = [...document.querySelectorAll('[class*=MuiDialog-paper],[role=dialog]')]
    .find(el => (el.innerText || '').trim().startsWith('Criteria'));
  if (!dialog) return false;
  const button = [...dialog.querySelectorAll('button,[role=button]')]
    .find(el => (el.innerText || '').trim() === label);
  if (!button) return false;
  button.click();
  return true;
}"""

RANKED = [
    "The candidate has deep AWS experience.",
    "The candidate is based in London.",
    "The candidate has 5+ years in platform roles.",
    "The candidate has run Kubernetes in production.",
    "The candidate does not require visa sponsorship.",
]


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    """The real waits are tuned for an app that paints in 20s; the mock is not."""
    monkeypatch.setattr(jc, "DIALOG_POLL_MS", 100)
    monkeypatch.setattr(jc, "DIALOG_WAIT_MS", 3_000)
    monkeypatch.setattr(jc, "ROW_POLL_MS", 100)
    monkeypatch.setattr(jc, "ROW_WAIT_MS", 1_000)


def _url(page_url: str, **switches) -> str:
    url = page_url.replace("mock-sequence.html", "mock-juicebox-criteria.html")
    query = "&".join(f"{key}={value}" for key, value in switches.items())
    return f"{url}?{query}" if query else url


async def _drive(url: str, work):
    reset_settings_cache()
    async with BrowserRunner(get_settings(), headless=True) as runner:
        async with runner.context() as (_context, page):
            await page.goto(url)
            return await work(page)


def _run(url: str, work):
    return asyncio.run(_drive(url, work))


# -- the happy path ----------------------------------------------------


def test_the_list_grows_and_every_criterion_lands_in_reacts_own_model(page_url):
    """Three rows, five criteria: the two new rows are added one at a time and
    all five survive the re-render, which only the native setter achieves."""

    async def work(page):
        await jc._open_dialog(page)
        await jc._write_list(page, RANKED)
        assert (await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL))["ok"]
        await page.wait_for_timeout(300)
        return await page.evaluate("() => window.__saved")

    assert _run(_url(page_url, rows=3), work) == RANKED


def test_rows_beyond_the_drafted_list_are_left_as_the_recruiter_left_them(page_url):
    """Shrinking someone's list is not this automation's call."""

    async def work(page):
        await jc._open_dialog(page)
        await jc._write_list(page, RANKED[:3])
        await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL)
        await page.wait_for_timeout(300)
        return await page.evaluate("() => window.__saved")

    saved = _run(_url(page_url, rows=5), work)
    assert saved[:3] == RANKED[:3]
    assert saved[3:] == ["Existing criterion 4", "Existing criterion 5"]


# -- the four faults that shared one sentence --------------------------


def test_a_capped_list_says_where_it_stopped_and_that_nothing_was_saved(page_url):
    async def work(page):
        await jc._open_dialog(page)
        with pytest.raises(PlatformError) as exc:
            await jc._write_list(page, RANKED)
        return str(exc.value), await page.evaluate("() => window.__saved")

    message, saved = _run(_url(page_url, rows=2, cap=4), work)
    assert "caps the list at 4" in message
    assert "5 were drafted" in message
    assert "Nothing was saved" in message
    assert saved is None, "Update must not run after a cap"
    assert "Could not add another criterion row" not in message


def test_a_greyed_out_add_button_is_named_as_that_and_not_blamed_on_a_cap(page_url):
    """The old code clicked a disabled button, was told it worked, and then
    reported a cap Juicebox does not have."""

    async def work(page):
        await jc._open_dialog(page)
        legacy = await page.evaluate(LEGACY_CLICK_IN_DIALOG, jc.ADD_LABEL)
        with pytest.raises(PlatformError) as exc:
            await jc._write_list(page, RANKED)
        return legacy, str(exc.value)

    legacy, message = _run(_url(page_url, rows=2, disabled="always"), work)
    assert legacy is True, "the old lookup called a click on a disabled button a success"
    assert "greyed out" in message
    assert "blank" in message, "say why Juicebox greys it out"
    assert "caps the list" not in message, "a disabled button is not a cap"


def test_a_button_juicebox_renamed_is_reported_with_what_the_dialog_does_offer(page_url):
    async def work(page):
        await jc._open_dialog(page)
        with pytest.raises(PlatformError) as exc:
            await jc._write_list(page, RANKED)
        return str(exc.value)

    message = _run(_url(page_url, rows=2, label="renamed"), work)
    assert "no 'Add Criterion' button" in message
    assert "'New rule'" in message, "name the buttons a recruiter can read back"
    assert "'Update'" in message
    assert "by hand" in message


def test_add_torn_out_of_the_dialog_is_reported_rather_than_clicked_blind(page_url):
    async def work(page):
        await jc._open_dialog(page)
        with pytest.raises(PlatformError) as exc:
            await jc._write_list(page, RANKED)
        return str(exc.value), await page.evaluate("() => window.__addClicks")

    message, clicks = _run(_url(page_url, rows=2, portal="body"), work)
    assert "not inside the Criteria dialog" in message
    assert clicks == 0, "a control outside the dialog must not be clicked"


def test_a_blank_row_never_blocks_the_list_because_rows_are_filled_first(page_url):
    """Juicebox greys Add Criterion out while the last row is blank. Adding all
    the rows first walked straight into that and then blamed a cap; filling each
    row before adding the next cannot."""

    async def work(page):
        await jc._open_dialog(page)
        # What the old strategy did: click Add for every missing row, up front.
        legacy_clicks = [
            await page.evaluate(LEGACY_CLICK_IN_DIALOG, jc.ADD_LABEL) for _ in range(4)
        ]
        await page.wait_for_timeout(300)
        stuck = len(await page.evaluate(jc.READ))
        await page.reload()
        await jc._open_dialog(page)
        await jc._write_list(page, RANKED)
        await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL)
        await page.wait_for_timeout(300)
        return legacy_clicks, stuck, await page.evaluate("() => window.__saved")

    legacy_clicks, stuck, saved = _run(_url(page_url, rows=1, disabled="blank"), work)
    assert all(legacy_clicks), "every old click reported success"
    assert stuck == 2, "and the list stopped growing after the first blank row"
    assert saved == RANKED


def test_the_dialog_closing_between_the_read_and_the_write_says_exactly_that(page_url):
    """The AI draft sits between the two with the page idle, which is the window
    the dialog can close in - and the one the old message could not name."""

    async def work(page):
        await jc._open_dialog(page)
        await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL)  # closes it
        await page.wait_for_timeout(200)
        with pytest.raises(PlatformError) as exc:
            await jc._write_list(page, RANKED)
        return str(exc.value)

    message = _run(_url(page_url, rows=3), work)
    assert "no longer open" in message
    assert "nothing was written" in message
    assert "Add Criterion" not in message, "this is not a button problem"


# -- shapes the old lookup could not see -------------------------------


@pytest.mark.parametrize("label", ["plus", "lower", "icon", "iconOnly"])
def test_a_decorated_add_button_is_found_where_exact_innertext_missed_it(page_url, label):
    """'+ Add Criterion', 'Add criterion', an icon ligature and an icon-only
    button are all the same control. Exact innerText saw none of them."""

    async def work(page):
        await jc._open_dialog(page)
        legacy = await page.evaluate(LEGACY_CLICK_IN_DIALOG, jc.ADD_LABEL)
        await jc._write_list(page, RANKED)
        await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL)
        await page.wait_for_timeout(300)
        return legacy, await page.evaluate("() => window.__saved")

    legacy, saved = _run(_url(page_url, rows=2, label=label), work)
    assert legacy is False, "the old lookup missed this button"
    assert saved == RANKED


def test_dialog_buttons_portalled_beside_the_paper_are_still_this_dialogs(page_url):
    async def work(page):
        await jc._open_dialog(page)
        legacy = await page.evaluate(LEGACY_CLICK_IN_DIALOG, jc.ADD_LABEL)
        await jc._write_list(page, RANKED)
        await page.evaluate(jc.CLICK_IN_DIALOG, jc.SAVE_LABEL)
        await page.wait_for_timeout(300)
        return legacy, await page.evaluate("() => window.__saved")

    legacy, saved = _run(_url(page_url, rows=2, portal="root"), work)
    assert legacy is False, "the old lookup only searched inside the paper"
    assert saved == RANKED


@pytest.mark.parametrize(
    "heading, title", [("search", "Search Criteria"), ("preset", "Criteria")]
)
def test_a_dialog_that_does_not_start_with_the_word_criteria_is_still_it(
    page_url, heading, title
):
    """A 'Search Criteria' heading, or a preset bar above the title, used to end
    the run with 'The Criteria dialog did not open' when it plainly had."""

    async def work(page):
        legacy = await page.evaluate(LEGACY_DIALOG)
        before = await jc._open_dialog(page)
        return legacy, before, (await page.evaluate(jc.DIALOG_INFO))["title"]

    legacy, before, found = _run(_url(page_url, rows=3, heading=heading), work)
    assert legacy is None, "the old heading match failed on this dialog"
    assert before == [f"Existing criterion {n}" for n in (1, 2, 3)]
    assert found == title


def test_the_cookie_banner_is_never_read_as_the_criteria_dialog(page_url):
    async def work(page):
        first = await page.evaluate(
            "() => document.querySelector('[role=dialog]').innerText.slice(0, 6)"
        )
        await jc._open_dialog(page)
        return first, await page.evaluate(jc.DIALOG_INFO)

    first, info = _run(_url(page_url, rows=2), work)
    assert first == "We use", "Osano is still first in the DOM"
    assert info["title"] == "Criteria"
    assert info["rows"] == 2


def test_rows_that_mount_late_are_waited_for_not_read_as_an_empty_search(page_url):
    """A fixed 5s wait read a half-mounted dialog as a search with no criteria,
    which is how a backup of nothing got written before the failure."""

    async def work(page):
        await page.evaluate(jc.OPEN_CRITERIA)
        immediately = await page.evaluate(jc.READ)
        return immediately, await jc._open_dialog(page)

    immediately, values = _run(_url(page_url, rows=4, mount=1200), work)
    assert immediately == [], "the rows really are absent at first"
    assert values == [f"Existing criterion {n}" for n in (1, 2, 3, 4)]


def test_a_search_with_no_criteria_yet_opens_without_complaint(page_url):
    """An empty list is a legitimate state for a new search - it must not be
    confused with a dialog that has not finished mounting."""

    async def work(page):
        return await jc._open_dialog(page), await page.evaluate(jc.DIALOG_INFO)

    values, info = _run(_url(page_url, rows=0), work)
    assert values == []
    assert info["found"] and info["ready"]


# -- what the row says afterwards --------------------------------------


def test_an_unsaved_criteria_list_does_not_read_like_progress():
    """'0 criteria before, 8 after; not saved' is what the recruiter was shown
    for a search that ranks nobody."""
    report = jc.SearchCriteriaReport(before=[], after=["a", "b"], saved=False)
    assert "NOT SAVED" in report.summary

    dry = jc.SearchCriteriaReport(before=["x"], after=["a"], dry_run=True)
    assert dry.summary.startswith("dry run")

    saved = jc.SearchCriteriaReport(before=["x"], after=["a", "b"], saved=True)
    assert saved.summary == "1 criteria before, 2 after; saved"


def test_set_criteria_reports_the_button_it_could_not_find_and_saves_nothing(
    page_url, tmp_path, monkeypatch
):
    """End to end on the real entry point, with only the draft stubbed."""
    monkeypatch.setattr(jc, "configured", lambda settings: True)

    async def _stub_draft(*args, **kwargs):
        return DraftCriteria(
            dealbreakers=[DraftCriterion(category="Hard skills", text=t) for t in RANKED]
        )

    monkeypatch.setattr(jc, "draft_criteria", _stub_draft)
    url = _url(page_url, rows=2, label="renamed")

    async def work(page):
        settings = Settings(_env_file=None, artifact_dir=tmp_path, headless=True)
        with pytest.raises(PlatformError) as exc:
            await jc.set_criteria(
                page, url, "The advert text", role_name="Platform", settings=settings
            )
        return str(exc.value), await page.evaluate("() => window.__saved")

    message, saved = _run(url, work)
    assert "no 'Add Criterion' button" in message
    assert saved is None
