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


# -- login steps: the sign-in the service replays ------------------------------------

LOGIN_STUB = """
key: stub
label: Stub
kind: advert
enabled: false

login:
  url: https://example.com/
  ready_selector: "#user-menu"
  steps:
{steps}

steps:
  - action: goto
    url: https://example.com/jobs/new
"""


def test_login_steps_reach_the_credentials_and_load(tmp_path):
    steps = """
    - action: goto
      url: https://example.com/login
    - action: fill
      selector: "#email"
      value: "{{ username }}"
    - action: fill
      selector: "#password"
      value: "{{ password }}"
    - action: fill
      selector: "#code"
      value: "{{ otp }}"
      optional: true
    - action: microsoft_sso
      username: "{{ username }}"
      password: "{{ password }}"
      totp_secret: "{{ totp_secret }}"
"""
    recipe = load_recipe(_write(tmp_path, "stub.yaml", LOGIN_STUB.format(steps=steps)))
    assert [s.action for s in recipe.login.steps] == ["goto", "fill", "fill", "fill", "microsoft_sso"]
    assert all(s.phase == "login" for s in recipe.login.steps)
    # Login steps are not run steps: the Steps column and submit rules ignore them.
    assert [s.phase for s in recipe.all_steps] == ["steps"]


def test_login_steps_cannot_read_the_document(tmp_path):
    """A sign-in that depends on which row is running is not a sign-in."""
    steps = """
    - action: fill
      selector: "#email"
      value: "{{ advert.title }}"
"""
    with pytest.raises(PipelineError) as caught:
        load_recipe(_write(tmp_path, "stub.yaml", LOGIN_STUB.format(steps=steps)))
    message = str(caught.value)
    assert "login[1] fill" in message
    assert "unknown root 'advert'" in message
    assert "username" in message  # the known roots are listed


def test_login_steps_cannot_carry_the_submit_marker(tmp_path):
    steps = """
    - action: click
      selector: "#login"
      submit: true
"""
    with pytest.raises(PipelineError) as caught:
        load_recipe(_write(tmp_path, "stub.yaml", LOGIN_STUB.format(steps=steps)))
    assert "login[1]: a login step cannot set submit: true" in str(caught.value)


def test_every_shipped_recipe_carries_login_steps():
    """The service can only sign in where the recipe says how. All four
    platforms have a flow written down, proven or not."""
    from pathlib import Path as _P

    class _Settings:
        platform_config_dir = _P(__file__).parent.parent / "platforms"

    recipes = load_recipes(_Settings())
    assert {k for k, r in recipes.items() if r.login.steps} >= {"noon", "loxo", "juicebox", "wellfound"}
