"""`juicebox-sourcing` is the supervised runner for the Juicebox sourcing flow.

It exists so the flow can be proven with a person watching a headed browser on
a throwaway project, and re-run against a real project by URL - without a
Notion row, and without re-posting the sequence.
"""

from __future__ import annotations

from typer.testing import CliRunner

from app import cli

runner = CliRunner()


def test_the_command_exists_and_documents_its_options():
    result = runner.invoke(cli.app, ["juicebox-sourcing", "--help"])
    assert result.exit_code == 0, result.output
    for option in ("--doc", "--project", "--name", "--live", "--set", "--headed"):
        assert option in result.output, option


def test_it_defaults_to_a_dry_run_that_opens_no_browser(monkeypatch):
    seen: dict = {}

    async def fake_run(doc, project, name, settings, dry_run, row=None):
        seen.update(doc=doc, project=project, name=name, dry_run=dry_run, row=row)
        return None

    monkeypatch.setattr(cli, "_juicebox_sourcing", fake_run)
    monkeypatch.setattr(cli, "_setup", lambda verbose=False: __import__("app.config").config.Settings())
    result = runner.invoke(
        cli.app,
        ["juicebox-sourcing", "--doc", "x.docx", "--project",
         "https://app.juicebox.ai/project/abc/home", "--set", "Location=NY, ATL"],
    )
    assert result.exit_code == 0, result.output
    assert seen["dry_run"] is True
    assert seen["project"] == "https://app.juicebox.ai/project/abc/home"
    assert seen["row"].property_text("Location") == "NY, ATL"
