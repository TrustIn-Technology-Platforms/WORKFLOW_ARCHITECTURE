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


# ----------------------------------------------------------------------
# `source` builds the search filters from the same place `post` does
# ----------------------------------------------------------------------


def test_source_passes_the_columns_through_as_a_stand_in_row(monkeypatch):
    """`--set` is what makes a file-only run exercise the location fix.

    The location, employment type and skills a search filters on live on the
    Notion row, not in the document — that is the whole reason noon was
    searching globally. A `source` run that could not receive them would leave
    the supervised live run unable to prove the thing it exists to prove.
    """
    seen: dict = {}

    async def fake_source(role, doc, settings, dry_run, name, start, row=None):
        seen["row"] = row

        # The real report, so a field the command prints cannot go missing here.
        from app.platforms.noon_sourcing import SourcingReport

        return SourcingReport(role_id="r")

    monkeypatch.setattr(cli, "_source", fake_source)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: Settings())
    result = runner.invoke(
        cli.app,
        [
            "source", "--role", "r", "--doc", "x.docx",
            "--set", "Location=Manchester",
            "--set", "Employment Type=Permanent",
        ],
    )

    assert result.exit_code == 0, result.output
    row = seen["row"]
    assert row is not None, "--set must reach _source, or the filters stay empty"
    assert row.property_text("Location") == "Manchester"
    assert row.property_text("Employment Type") == "Permanent"


def test_source_without_set_still_runs(monkeypatch):
    """A document that states its own location needs no columns."""
    seen: dict = {}

    async def fake_source(role, doc, settings, dry_run, name, start, row=None):
        seen["row"] = row

        # The real report, so a field the command prints cannot go missing here.
        from app.platforms.noon_sourcing import SourcingReport

        return SourcingReport(role_id="r")

    monkeypatch.setattr(cli, "_source", fake_source)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: Settings())
    result = runner.invoke(cli.app, ["source", "--role", "r", "--doc", "x.docx"])

    assert result.exit_code == 0, result.output
    assert seen["row"] is None


# ----------------------------------------------------------------------
# `check` covers the drafting key
# ----------------------------------------------------------------------


def _offline_check(monkeypatch, **settings_kwargs):
    """`check` with the network stubbed out. `_env_file=None` keeps the real
    .env - live Notion credentials included - out of a unit test."""
    settings = Settings(_env_file=None, **settings_kwargs)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: settings)

    async def notion_ok(s):
        return None

    monkeypatch.setattr(cli, "_check_notion", notion_ok)
    return settings


def test_check_names_what_an_unset_key_costs(monkeypatch):
    """No key is not a failure - it is three quiet degradations, and the check
    is where they get said out loud instead of being discovered mid-run."""
    _offline_check(monkeypatch, anthropic_api_key="")
    result = runner.invoke(cli.app, ["check"])

    assert "Criteria drafting" in result.output
    assert "ANTHROPIC_API_KEY unset" in result.output


def test_check_probes_a_configured_key(monkeypatch):
    probed = {}

    async def fake_probe(settings):
        probed["model"] = settings.criteria_model

    monkeypatch.setattr(cli, "_check_anthropic", fake_probe)
    _offline_check(monkeypatch, anthropic_api_key="sk-ant-x")
    result = runner.invoke(cli.app, ["check"])

    assert probed["model"] == "claude-opus-5"
    assert "key accepted" in result.output
