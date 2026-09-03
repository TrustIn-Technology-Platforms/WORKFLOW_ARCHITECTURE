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
    match_mode,
    new_lines,
    pick_option,
    present,
    project_home,
    project_title,
    same_company,
    split_locations,
    stage_key,
    stages_up_to,
    years_span,
)


# -- autocomplete options ----------------------------------------------------

STRIPE_OPTIONS = [
    'Ask AI for "Stripe"', "Stripe\nstripe.com", "Stripes\nstripes.co",
    "Stripe-A-Zone, LLC\nstripe-a-zone.com", "Stripe Olt\nstripeolt.com",
]


def test_the_ask_ai_suggestion_is_never_the_answer():
    # Live, 2026-09-02: the first suggestion for every company is 'Ask AI for
    # "<name>"', which contains the name. The exact name is the second.
    assert pick_option(STRIPE_OPTIONS, "Stripe", mode="exact") == 1
    assert pick_option(['Ask AI for "Foo"'], "Foo") is None


def test_a_company_needs_its_own_name_not_the_nearest():
    assert pick_option(STRIPE_OPTIONS, "Stripe Olt", mode="exact") == 4
    assert pick_option(STRIPE_OPTIONS, "Stripey", mode="exact") is None
    # The first live run (2026-09-03): a name that merely starts with the value
    # is a different company.
    assert pick_option(["United Nations\nun.org"], "Unit", mode="exact") is None
    assert pick_option(["Sureskills\nsureskills.com"], "Sure", mode="exact") is None
    assert pick_option(["Method Financial Planning"], "Method Financial", mode="exact") is None
    # Legal suffixes and punctuation are not part of the name.
    assert pick_option(["Stripe, Inc.\nstripe.com"], "Stripe", mode="exact") == 0
    # Loose matching (titles, skills) may take a containing or first real option.
    assert pick_option(["Platform Lead\nTITLE", "Platform Engineer"], "engineer") == 1
    assert pick_option(['Ask AI for "x"', "Something"], "x") == 1


def test_project_titles_are_cut_to_what_juicebox_keeps():
    # Juicebox keeps 50 characters; the full name is what the check looked for,
    # which is why three successful renames were reported as failures.
    long = "Axle Insurance - Platform Infrastructure Eng - NY-ATLANTA"
    assert len(project_title(long)) <= 50
    assert project_title(long) == "Axle Insurance - Platform Infrastructure Eng - NY"
    assert project_title("Short name") == "Short name"
    assert project_title("  spaced   out  ") == "spaced out"


def test_a_short_name_with_the_full_name_in_the_domain_is_the_same_company():
    # Server run, 2026-09-03: both were the right company and both were refused.
    assert same_company("Boost Insurance", "Boost\nboostinsurance.com")
    assert same_company("Method Financial", "Method\nmethodfi.com")
    assert pick_option(['Ask AI for "Boost Insurance"', "Boost\nboostinsurance.com"],
                       "Boost Insurance", mode="exact") == 1
    # ... while a lookalike is still not.
    assert not same_company("Unit", "United Nations\nun.org")
    assert not same_company("Unit", "United Airlines\nunited.com")
    assert not same_company("Sure", "Sureskills\nsureskills.com")
    assert not same_company("Stripe", "Stripes\nstripes.co")
    assert not same_company("Alloy", "Alloy Automation\nalloy.com")
    assert not same_company("Boost Insurance", "Boost")  # no domain to vouch for it
    # The domain must add to the name, or "Stripe / stripe.com" passes for Stripe Olt.
    assert not same_company("Stripe Olt", "Stripe\nstripe.com")


def test_a_location_abbreviation_matches_a_whole_word_never_a_prefix():
    # Live, 2026-09-03: "NY" picked "Nyack" under the start-of-name rule.
    options = ['Ask AI for "NY"', "Nyack\nCITY", "New York, NY, United States\nCITY", "New York\nREGION"]
    assert pick_option(options, "NY", mode="token") == 2
    assert pick_option(['Ask AI for "NY"', "Nyack\nCITY"], "NY", mode="token") is None
    assert pick_option(["Atlanta, GA, United States\nCITY"], "Atlanta", mode="token") == 0
    assert pick_option(["Atlanta\nCITY", "Atlanta, GA\nCITY"], "Atlanta", mode="token") == 0
    assert match_mode("Location(s)") == "token"
    assert match_mode("Companies") == "exact"
    assert match_mode("Job Titles") == "loose"


def test_present_is_exact_for_companies_and_loose_elsewhere():
    block = "Current + Past\nClear all\nUnited Nations\nStripe, Inc.\n+ Add company group"
    assert present(block, "Stripe", mode="exact")
    assert not present(block, "Unit", mode="exact")
    assert present("CITY\nAtlanta", "ATL")
    assert not present("CITY\nAtlanta", "NY")


# -- funding stages ----------------------------------------------------------


def test_stage_keys_follow_juicebox_spelling():
    assert stage_key("Series B") == "series_b"
    assert stage_key("series-a") == "series_a"
    assert stage_key("Series D+") == "series_d"
    assert stage_key("Pre-seed") == "pre_seed"
    assert stage_key("Seed") == "seed"
    assert stage_key("Public") == "ipo"
    assert stage_key("Growth stage") == "series_d"
    assert stage_key("Early stage") == "series_a"
    assert stage_key("Bootstrapped") is None
    assert stage_key("Unknown") is None
    assert stage_key(None) is None


def test_stages_run_from_seed_up_to_the_clients_own():
    menu = ["pre_seed", "seed", "series_a", "series_b", "series_c", "series_d", "ipo"]
    # Sohaib's rule: a Series C client takes Seed, A, B and C - not Pre-seed,
    # not D.
    assert stages_up_to("Series C", menu) == ["seed", "series_a", "series_b", "series_c"]
    assert stages_up_to("Seed", menu) == ["seed"]
    assert stages_up_to("Pre-seed", menu) == ["pre_seed"]
    assert stages_up_to("Public", menu) == ["seed", "series_a", "series_b", "series_c", "series_d", "ipo"]
    assert stages_up_to("Unknown", menu) == []
    # Only what the menu offers, in the menu's own order.
    assert stages_up_to("Series C", ["series_c", "seed"]) == ["seed", "series_c"]


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
        "min_years": 5, "max_years": None, "companies": None, "stage": None,
    }


def test_an_existing_search_skips_the_project_and_the_jd_paste(monkeypatch):
    calls: list = []
    _fakes(monkeypatch, calls)

    async def must_not_create(page, name):
        raise AssertionError("no project should be created for an existing search")

    async def must_not_search(page, project_url, jd):
        raise AssertionError("no JD search should run for an existing search")

    monkeypatch.setattr(sourcing, "create_project", must_not_create)
    monkeypatch.setattr(sourcing, "jd_search", must_not_search)
    search = "https://app.juicebox.ai/project/abc/search?search_id=S9"
    report = asyncio.run(
        sourcing.set_up_sourcing(
            None, project_name="x", jd="jd", titles=[], skills=["S"], location=None,
            companies=["Stripe"], stage="Series B", search_url=search,
        )
    )
    assert report.project_created is False
    assert report.project_url == "https://app.juicebox.ai/project/abc/home"
    assert calls == [("filters", search, {
        "titles": [], "skills": ["S"], "location": None, "min_years": None,
        "max_years": None, "companies": ["Stripe"], "stage": "Series B",
    })]


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
