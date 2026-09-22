"""The round the service runs on a timer to keep every login alive."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.platforms.keepalive import KeepaliveResult, keepalive, keepalive_platform
from app.platforms.recipe import LoginSpec, Recipe


def _recipe(key: str, enabled: bool = True) -> Recipe:
    return Recipe(
        key=key, label=key.title(), kind="advert", path=Path(f"{key}.yaml"),
        enabled=enabled, login=LoginSpec(url="https://example.com/"),
    )


def test_a_platform_with_no_profile_is_a_result_not_an_exception(tmp_path):
    """The timer loop must survive any one platform, and the message must say
    where the profile was looked for and how it gets there."""
    settings = Settings(
        _env_file=None, browser_profile_dir=tmp_path / "profiles",
        session_dir=tmp_path / "sessions", artifact_dir=tmp_path / "artifacts",
    )
    result = asyncio.run(keepalive_platform(_recipe("wellfound"), settings))

    assert result.ok is False
    assert result.logged_in is False
    assert "no browser profile" in result.detail
    assert "login wellfound" in result.detail
    assert result.as_dict()["platform"] == "wellfound"
    assert "NOT logged in" in result.summary


def test_the_round_visits_enabled_platforms_only(tmp_path):
    settings = Settings(
        _env_file=None, browser_profile_dir=tmp_path / "profiles",
        session_dir=tmp_path / "sessions", artifact_dir=tmp_path / "artifacts",
    )
    recipes = {"noon": _recipe("noon"), "old": _recipe("old", enabled=False)}

    results = asyncio.run(keepalive(settings, recipes=recipes))
    assert [r.platform for r in results] == ["noon"]

    # Named explicitly, a disabled platform is still visited.
    results = asyncio.run(keepalive(settings, keys=["old"], recipes=recipes))
    assert [r.platform for r in results] == ["old"]


def test_summary_reads_as_a_status_line():
    alive = KeepaliveResult("noon", "noon.ai", "t", ok=True, logged_in=True, cookies=28)
    assert alive.summary == "noon.ai: alive, 28 cookies exported"
    again = KeepaliveResult("noon", "noon.ai", "t", ok=True, logged_in=True, relogged_in=True, cookies=30)
    assert again.summary.startswith("noon.ai: signed in again")
    dead = KeepaliveResult("loxo", "Loxo", "t", ok=False, detail="Microsoft wants approval")
    assert dead.summary == "Loxo: NOT logged in, 0 cookies exported - Microsoft wants approval"
