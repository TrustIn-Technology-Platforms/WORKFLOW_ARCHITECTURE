"""`post` must not quietly disable the criteria stage.

`--sourcing/--no-sourcing` is a three-state flag: on, off, or "leave
CRITERIA_ENABLED alone". Click renders the unset state as `[default:
no-sourcing]` in `--help`, which reads as though omitting it turns the stage
off. If that were true, every posted row would silently stop setting criteria
and nothing would fail — so it is pinned here.
"""

from __future__ import annotations

from typer.testing import CliRunner

from app import cli
from app.config import Settings

runner = CliRunner()


def _capture(monkeypatch) -> dict:
    """Run `post` far enough to see what it did to the settings."""
    seen: dict = {}

    async def fake_post(platform, source, settings, dry_run, row=None):
        seen["criteria_enabled"] = settings.criteria_enabled
        seen["dry_run"] = dry_run

        class _Result:
            outcome = type("O", (), {"value": "dry_run"})()
            platform = "noon"
            post_url = None
            detail = None
            artifacts: list = []

        return _Result()

    monkeypatch.setattr(cli, "_post", fake_post)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: Settings())
    return seen


def test_omitting_the_flag_leaves_the_setting_alone(monkeypatch):
    seen = _capture(monkeypatch)
    result = runner.invoke(cli.app, ["post", "noon", "--doc", "x.docx"])

    assert result.exit_code == 0, result.output
    assert seen["criteria_enabled"] is True  # the Settings default, untouched


def test_no_sourcing_turns_it_off_for_the_run(monkeypatch):
    seen = _capture(monkeypatch)
    result = runner.invoke(cli.app, ["post", "noon", "--doc", "x.docx", "--no-sourcing"])

    assert result.exit_code == 0, result.output
    assert seen["criteria_enabled"] is False


def test_sourcing_turns_it_on_even_when_the_setting_is_off(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: Settings(criteria_enabled=False))
    result = runner.invoke(cli.app, ["post", "noon", "--doc", "x.docx", "--sourcing"])

    assert result.exit_code == 0, result.output
    assert seen["criteria_enabled"] is True
