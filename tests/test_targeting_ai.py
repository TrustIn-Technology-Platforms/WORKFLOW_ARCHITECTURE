"""The search-targeting drafter's pure parts."""

from __future__ import annotations

import asyncio

from app.platforms.targeting_ai import (
    CompanyTargeting,
    SearchTargeting,
    _clean,
    _clean_companies,
    _years,
    draft_companies,
    draft_targeting,
    stage_from_text,
)


def test_clean_dedupes_case_insensitively_and_caps():
    values = ["DevOps Engineer", "devops  engineer", " SRE ", ""] + [f"T{n}" for n in range(20)]
    cleaned = _clean(values, 10)
    assert cleaned[0] == "DevOps Engineer"
    assert cleaned[1] == "SRE"
    assert len(cleaned) == 10


def test_no_key_returns_empty_lists_not_an_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.config import reset_settings_cache

    reset_settings_cache()
    try:
        result = asyncio.run(draft_targeting("8+ years of Kubernetes."))
    finally:
        reset_settings_cache()
    assert result == SearchTargeting()


def test_the_sourcing_wrappers_compute_the_role_name_correctly(monkeypatch):
    """The production TypeError of 2026-09-02: `_role_name(document)` where the
    signature is (source_name, row, advert, email_steps). It fired only at
    runtime, after three platforms had already posted, and failed the row. Both
    dry-run paths exercise the exact call now."""
    import asyncio

    from app.config import get_settings
    from app.models import Advert, ParsedDocument
    from app.platforms import load_recipes, resolve
    from app.platforms.engine import RunReport
    from app.platforms.juicebox import JuiceboxAdapter
    from app.platforms.loxo import LoxoAdapter

    async def fake_targeting(*args, **kwargs):
        return SearchTargeting(similar_titles=["Platform Engineer"], skills=["AWS"],
                               min_years=5)

    async def fake_companies(*args, **kwargs):
        return CompanyTargeting(stage="Series B", stage_basis="inferred", companies=["Ramp"])

    monkeypatch.setattr("app.platforms.targeting_ai.draft_targeting", fake_targeting)
    monkeypatch.setattr("app.platforms.targeting_ai.draft_companies", fake_companies)

    settings = get_settings()
    recipes = load_recipes(settings)
    document = ParsedDocument(
        advert=Advert(title="Platform Engineer / NY", body_text="JD here.",
                      body_html="<p>JD here.</p>"),
        emails=[],
        source_name="Axle - Platform Engineer",
    )

    loxo = LoxoAdapter(resolve("loxo", recipes), settings=settings, dry_run=True)
    report = RunReport()
    asyncio.run(loxo._configure_source(None, document, None, report, "123"))
    dry = next(w for w in report.warnings if "dry run" in w)
    # Everything the live run would write is named, including the bands the
    # years became and the stage the company list rests on.
    assert "1 title(s), 1 skill(s), experience 6-10/10+ and 1 past company(ies) at Series B" in dry
    # An inferred stage is said so on the row.
    assert any("inferred Series B" in w for w in report.warnings)

    jb = JuiceboxAdapter(resolve("juicebox", recipes), settings=settings, dry_run=True)
    report = RunReport()
    asyncio.run(jb._set_up_sourcing(None, document, None, report))
    assert any("dry run" in w for w in report.warnings)


def test_the_stage_is_read_off_the_text_series_letter_first():
    assert stage_from_text("We are a Series B fintech based in London.") == "Series B"
    assert stage_from_text("a series-a startup") == "Series A"
    # A funding history names the latest round as the current stage.
    assert stage_from_text("We raised our Series A in 2023 and our Series B this year.") == "Series B"
    assert stage_from_text("a seed-stage startup") == "Seed"
    assert stage_from_text("We just raised a $4M seed") == "Seed"
    assert stage_from_text("Pre-seed, founder-led") == "Pre-seed"
    assert stage_from_text("a publicly traded insurer") == "Public"
    assert stage_from_text("a growth-stage company") == "Growth stage"
    assert stage_from_text("bootstrapped and profitable") == "Bootstrapped"


def test_the_stage_is_not_read_into_text_that_does_not_state_one():
    assert stage_from_text("We seed the database nightly.") is None
    assert stage_from_text("A world series of events") is None
    assert stage_from_text("") is None
    # The advert is consulted when the JD is silent.
    assert stage_from_text("nothing here", "the advert calls it a Series C business") == "Series C"


def test_years_pairs_are_sanitised_not_trusted():
    assert _years(5, None) == (5, None)
    assert _years(None, 5) == (None, 5)
    assert _years(8, 3) == (3, 8)
    assert _years(-1, 200) == (None, None)
    assert _years(None, None) == (None, None)


def test_target_companies_drop_the_client_and_duplicates():
    cleaned = _clean_companies(
        ["Stripe", "stripe, inc.", "Axle Insurance", "Axle", "Ramp", "", "Axle Insurance Ltd"],
        "Axle Insurance",
    )
    assert cleaned == ["Stripe", "Ramp"]


def test_no_key_returns_empty_company_targeting_not_an_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.config import reset_settings_cache

    reset_settings_cache()
    try:
        result = asyncio.run(draft_companies("A Series B insurtech.", company="Axle"))
    finally:
        reset_settings_cache()
    assert result == CompanyTargeting()
    assert result.inferred is False
