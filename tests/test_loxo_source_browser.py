"""The Source writer's browser half, against a mock of Loxo's filter panel.

The live run is what finally proves the writer (docs/platforms/loxo.md), but the
Loxo session dies weekly and cannot be leaned on. This drives the same section
helpers the live run does against `mock-loxo-source.html`, which reproduces the
one shape the Past Company writer has never been run against: a Company section
with two name+domain autocompletes, Current Company before Past Company.

What it pins is the reasoning a live run cannot check by eye:

- Past-company chips land in the PAST Company box, not the Current one next to it.
- A drafted company Loxo does not list is refused, not committed as free text -
  "Axle" never settles for "Axle Logistics".
- Skills still land in Skills, so the shared primitive did not regress.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings, reset_settings_cache
from app.platforms.browser import BrowserRunner
from app.platforms.loxo_source import _fill_companies, _fill_section


async def _drive(page_url: str, *, skills: list[str], companies: list[str]) -> dict:
    url = page_url.replace("mock-sequence.html", "mock-loxo-source.html")
    reset_settings_cache()
    settings = get_settings()
    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (_context, page):
            await page.goto(url)
            added_skills, refused_skills = await _fill_section(
                page, "Skills", skills, loose=True
            )
            added_companies, refused_companies = await _fill_companies(page, companies)

            def chips(box: str) -> list[str]:
                return page.evaluate(
                    "(box) => [...document.querySelectorAll('[data-chips=\"' + box + '\"] .chip')]"
                    ".map((c) => c.dataset.name)",
                    box,
                )

            return {
                "added_skills": added_skills,
                "refused_skills": refused_skills,
                "added_companies": added_companies,
                "refused_companies": refused_companies,
                "skills_box": await chips("skills"),
                "current_company_box": await chips("company-current"),
                "past_company_box": await chips("company-past"),
            }


def test_past_companies_land_in_the_past_company_box(page_url):
    """The list goes into Past Company; the Current Company box beside it stays
    empty. Putting the list in the wrong box finds the wrong people."""
    state = asyncio.run(
        _drive(page_url, skills=["Terraform"], companies=["Stripe", "Ramp"])
    )
    assert state["past_company_box"] == ["Stripe", "Ramp"]
    assert state["current_company_box"] == []
    assert state["added_companies"] == ["Stripe", "Ramp"]


def test_a_company_loxo_does_not_list_is_refused_not_guessed(page_url):
    """"Axle" must not settle for "Axle Logistics", and nothing is left half
    typed in the box."""
    state = asyncio.run(
        _drive(page_url, skills=["Terraform"], companies=["Stripe", "Axle"])
    )
    assert state["past_company_box"] == ["Stripe"]
    assert state["added_companies"] == ["Stripe"]
    assert state["refused_companies"] == ["Axle"]


def test_a_name_domain_suggestion_still_commits(page_url):
    """Every offered company carries a domain on a second line; the writer must
    read past it to the name and commit the chip."""
    state = asyncio.run(
        _drive(page_url, skills=["Terraform"], companies=["Brex", "Plaid", "Mercury"])
    )
    assert state["past_company_box"] == ["Brex", "Plaid", "Mercury"]
    assert state["refused_companies"] == []


def test_skills_still_land_in_skills(page_url):
    """The Past Company writer shares the skills primitive; skills must still
    go into the Skills box and nowhere else."""
    state = asyncio.run(
        _drive(page_url, skills=["Terraform", "Kubernetes"], companies=["Stripe"])
    )
    assert state["skills_box"] == ["Terraform", "Kubernetes"]
    assert state["past_company_box"] == ["Stripe"]
    assert state["current_company_box"] == []
