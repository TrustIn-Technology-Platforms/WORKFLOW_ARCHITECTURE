"""Validation rules that decide whether a recipe file loads at all.

One broken recipe raises out of `load_recipes`, which takes every other recipe
with it - so these rules are load-bearing for every platform, not just the one
being edited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import PipelineError
from app.platforms.recipe import load_recipe, load_recipes

STUB = """
key: {key}
label: Stub
kind: email_sequence
enabled: {enabled}

login:
  url: https://example.com/

defaults:
  base_url: https://example.com

steps:
  - action: goto
    url: "{{{{ base_url }}}}"
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_disabled_stub_loads(tmp_path):
    """A disabled recipe needs no submit step and no per_email block.

    This is the shape every platform starts as: a file that exists only so
    `login <key>` has somewhere to send the browser. Demanding a runnable shape
    here would mean inventing a submit step before anyone has seen the page.
    """
    recipe = load_recipe(_write(tmp_path, "stub.yaml", STUB.format(key="stub", enabled="false")))

    assert recipe.key == "stub"
    assert recipe.enabled is False
    assert recipe.login.url == "https://example.com/"


def test_enabling_a_stub_demands_a_runnable_shape(tmp_path):
    """The same file with enabled: true is rejected, naming both gaps."""
    with pytest.raises(PipelineError) as caught:
        load_recipe(_write(tmp_path, "stub.yaml", STUB.format(key="stub", enabled="true")))

    message = str(caught.value)
    assert "submit: true" in message
    assert "per_email" in message


def test_one_bad_recipe_does_not_hide_the_others(tmp_path):
    """A directory with a broken file fails loudly, naming the file."""
    _write(tmp_path, "good.yaml", STUB.format(key="good", enabled="false"))
    _write(tmp_path, "bad.yaml", "key: bad\nsteps:\n  - action: not_an_action\n")

    class _Settings:
        platform_config_dir = tmp_path

    with pytest.raises(PipelineError) as caught:
        load_recipes(_Settings())

    assert "bad.yaml" in str(caught.value)
