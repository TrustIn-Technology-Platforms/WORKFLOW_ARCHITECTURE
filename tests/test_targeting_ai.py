"""The search-targeting drafter's pure parts."""

from __future__ import annotations

import asyncio

from app.platforms.targeting_ai import SearchTargeting, _clean, draft_targeting


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
        return SearchTargeting(similar_titles=["Platform Engineer"], skills=["AWS"])

    monkeypatch.setattr("app.platforms.targeting_ai.draft_targeting", fake_targeting)

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
    assert any("dry run" in w for w in report.warnings)

    jb = JuiceboxAdapter(resolve("juicebox", recipes), settings=settings, dry_run=True)
    report = RunReport()
    asyncio.run(jb._set_up_sourcing(None, document, None, report))
    assert any("dry run" in w for w in report.warnings)
