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
