"""The Juicebox sourcing flow's offline parts: URLs, places, the report, and
which project a run uses. The page-driving is proven live against the real app
(docs/platforms/juicebox.md#sourcing)."""

from __future__ import annotations

import asyncio

from app.platforms import juicebox_sourcing as sourcing
from app.platforms.juicebox_sourcing import (
    SourcingReport,
    _landed,
    chip_label,
    is_search_url,
    new_lines,
    project_home,
    split_locations,
    years_span,
)


# -- URLs --------------------------------------------------------------------


def test_project_home_from_any_url_inside_the_project():
    home = "https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW/home"
    assert project_home("https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW/sequences?x=1") == home
    assert project_home("https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW/search?search_id=9") == home
    assert project_home("https://app.juicebox.ai/project/AXAaleEq2JfO29jIjBXW") == home


def test_project_home_leaves_a_url_without_a_project_alone():
    assert project_home("https://example.com/x?y=1") == "https://example.com/x"
    assert project_home("") == ""


def test_search_urls_by_query_or_path():
    assert is_search_url("https://app.juicebox.ai/project/abc/search?search_id=Q1")
    assert is_search_url("https://app.juicebox.ai/project/abc/search/Q1")
    assert not is_search_url("https://app.juicebox.ai/project/abc/home")
    assert not is_search_url("https://app.juicebox.ai/project/abc/search")
    assert not is_search_url("")


# -- places ------------------------------------------------------------------


def test_one_chip_per_place():
    assert split_locations("NY, ATL") == ["NY", "ATL"]
    assert split_locations("New York / Atlanta") == ["New York", "Atlanta"]
    assert split_locations("London or Manchester") == ["London", "Manchester"]


def test_working_arrangements_are_not_places():
    assert split_locations("Manchester (hybrid)") == ["Manchester"]
    assert split_locations("London or Remote") == ["London"]
    assert split_locations("Remote") == []
    assert split_locations(None) == []
    assert split_locations("") == []


def test_places_are_deduplicated_case_insensitively():
    assert split_locations("Leeds, leeds, LEEDS") == ["Leeds"]


def test_the_chip_that_landed_is_the_line_the_section_gained():
    # Live, 2026-09-02: "NY" typed, and the section gained a REGION tag and a
    # "New York" chip. The chip is the label, the tag is not.
    before = "Within 25 miles\nCITY\nNew York\nCITY\nAtlanta"
    after = "Within 25 miles\nREGION\nNew York\nCITY\nNew York\nCITY\nAtlanta"
    gained = new_lines(before, after)
    assert gained == ["REGION", "New York"]
    assert chip_label(gained, "NY") == "New York"


def test_new_lines_counts_multiplicity_and_nothing_gained_is_empty():
    assert new_lines("a\nb", "a\nb") == []
    assert new_lines("a", "a\na") == ["a"]
    assert chip_label(["Infrastructure AS Code"], "Infrastructure as Code") == "Infrastructure AS Code"
    assert chip_label(["CITY"], "Atlanta") == "CITY"  # nothing better to report


def test_a_chip_can_land_under_the_options_own_label():
    # Typing "NY" lands as the option's label, "New York"; the section text is
    # what proves it, not the text typed.
    assert _landed("New York\nCITY", "city\nnew york\npast locations")
    assert _landed("New York, NY, United States", "new york")
    assert not _landed("", "new york")
    assert not _landed("Atlanta", "new york")


# -- the report --------------------------------------------------------------


def test_summary_counts_added_refused_and_saved():
    report = SourcingReport(
        added={"Job Titles": ["a", "b"], "Location(s)": ["NY"], "Skills or Keywords": []},
        refused={"Skills or Keywords": ["x"]},
        saved=True,
    )
    assert report.summary == "2 Job Titles, 1 Location(s), 1 refused, saved"
    assert SourcingReport().summary == "nothing added, NOT saved"


# -- which project -----------------------------------------------------------


def _fakes(monkeypatch, calls: list) -> None:
    async def fake_search(page, project_url, jd):
        calls.append(("search", project_url, jd))
        return "https://app.juicebox.ai/project/abc/search?search_id=S1"

    async def fake_filters(page, search_url, **kwargs):
        calls.append(("filters", search_url, kwargs))
        return SourcingReport(search_url=search_url, saved=True)

    monkeypatch.setattr(sourcing, "jd_search", fake_search)
    monkeypatch.setattr(sourcing, "configure_filters", fake_filters)


def test_an_existing_project_is_opened_not_created(monkeypatch):
    calls: list = []
    _fakes(monkeypatch, calls)

    async def must_not_create(page, name):
        raise AssertionError("create_project must not run when a project URL is given")

    monkeypatch.setattr(sourcing, "create_project", must_not_create)
    existing = "https://app.juicebox.ai/project/abc/home"
    report = asyncio.run(
        sourcing.set_up_sourcing(
            None, project_name="Axle", jd="jd", titles=["T"], skills=["S"],
            location="NY, ATL", min_years=5, project_url=existing,
        )
    )
    assert report.project_created is False
    assert report.project_url == existing
    assert calls[0] == ("search", existing, "jd")
    assert calls[1][2] == {
        "titles": ["T"], "skills": ["S"], "location": "NY, ATL",
        "min_years": 5, "max_years": None,
    }


def test_years_span_reads_as_a_person_would_say_it():
    assert years_span(5, None) == "5+ years"
    assert years_span(3, 5) == "3-5 years"
    assert years_span(None, 5) == "up to 5 years"
    assert years_span(None, None) == ""


def test_without_a_project_url_one_is_created_and_named(monkeypatch):
    calls: list = []
    _fakes(monkeypatch, calls)

    async def fake_create(page, name):
        calls.append(("create", name))
        return "https://app.juicebox.ai/project/new/home"

    monkeypatch.setattr(sourcing, "create_project", fake_create)
    report = asyncio.run(
        sourcing.set_up_sourcing(
            None, project_name="ZZ TEST", jd="jd", titles=["T"], skills=[],
            location=None,
        )
    )
    assert report.project_created is True
    assert report.project_url == "https://app.juicebox.ai/project/new/home"
    assert calls[0] == ("create", "ZZ TEST")
    assert calls[1][1] == "https://app.juicebox.ai/project/new/home"
