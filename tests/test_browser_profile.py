"""A captured profile must stay with the browser that captured it.

Chrome encrypts its cookie store with a key in `Local State`. Opening the
directory with a different build re-keys it and every cookie the other browser
wrote becomes undecryptable - the session is gone, and the app shows an ordinary
"please log in" screen, so nothing about the failure points at the cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import PlatformError
from app.platforms.browser import CHANNEL_MARKER, claim_profile_channel


def test_first_use_records_the_browser(tmp_path):
    claim_profile_channel(tmp_path, "chrome")

    assert (tmp_path / CHANNEL_MARKER).read_text(encoding="utf-8") == "chrome"


def test_bundled_chromium_is_recorded_by_name(tmp_path):
    """`None` means Playwright's bundled Chromium, and must be distinguishable."""
    claim_profile_channel(tmp_path, None)

    assert (tmp_path / CHANNEL_MARKER).read_text(encoding="utf-8") == "bundled"


def test_reopening_with_the_same_browser_is_fine(tmp_path):
    claim_profile_channel(tmp_path, "chrome")
    claim_profile_channel(tmp_path, "chrome")  # must not raise


def test_switching_browser_is_refused_with_both_options_named(tmp_path):
    claim_profile_channel(tmp_path, "chrome")

    with pytest.raises(PlatformError) as caught:
        claim_profile_channel(tmp_path, None)

    message = str(caught.value)
    assert "'chrome'" in message and "'bundled'" in message
    assert "browser_channel: chrome" in message  # the fix, spelled out


def test_the_guard_works_in_the_other_direction_too(tmp_path):
    claim_profile_channel(tmp_path, None)

    with pytest.raises(PlatformError):
        claim_profile_channel(tmp_path, "chrome")


# ----------------------------------------------------------------------
# where the profile directory actually is
# ----------------------------------------------------------------------


def test_relative_directories_are_anchored_to_the_checkout(tmp_path, monkeypatch):
    """A run started from anywhere must find the same `.profiles`.

    These paths belong to the checkout, not to whichever folder the command was
    typed in. Resolving them against the working directory made a Notion-
    triggered run report "Wellfound has no browser profile yet" against a
    profile sitting right there, and create empty `.profiles/`, `.sessions/`
    and `artifacts/` folders wherever it had started (2026-08-31).
    """
    from app.config import PROJECT_ROOT, Settings

    monkeypatch.chdir(tmp_path)
    settings = Settings()

    for attribute in (
        "browser_profile_dir",
        "session_dir",
        "artifact_dir",
        "platform_config_dir",
    ):
        value = getattr(settings, attribute)
        assert value.is_absolute(), f"{attribute} must not depend on the cwd"
        assert value.parent == PROJECT_ROOT, f"{attribute} escaped the checkout"
        assert tmp_path not in value.parents, f"{attribute} followed the cwd"


def test_an_absolute_directory_is_left_alone(monkeypatch):
    """The Railway volume is pointed at with an absolute path."""
    from app.config import Settings

    volume = Path("/data/profiles").resolve()
    monkeypatch.setenv("BROWSER_PROFILE_DIR", str(volume))
    assert Settings().browser_profile_dir == volume


def test_a_missing_profile_on_a_volume_does_not_tell_the_server_to_log_in():
    """The same missing profile has two causes and two different fixes.

    A workstation can capture the login. A server cannot - there is no display,
    and three of the four platforms need a human through 2FA - so telling it to
    run `login` sends whoever reads the row's Error column nowhere. The message
    also has to name the directory it looked in, which is the only way to tell
    the two cases apart from a log line.
    """
    from app.config import Settings
    from app.models import AuthenticationRequired
    from app.sessions.store import SessionStore

    store = SessionStore(Settings(browser_profile_dir=Path("/data/profiles")))
    with pytest.raises(AuthenticationRequired) as raised:
        store.require_profile("wellfound", "Wellfound")

    message = str(raised.value)
    assert "profiles" in message and "wellfound" in message, "names the directory"
    assert "mounted volume" in message
    assert "upload" in message
    assert "docs/09-operations.md" in message


def test_a_missing_profile_locally_still_says_to_run_login(tmp_path):
    from app.config import Settings
    from app.models import AuthenticationRequired
    from app.sessions.store import SessionStore

    store = SessionStore(Settings())
    with pytest.raises(AuthenticationRequired) as raised:
        store.require_profile("nosuchplatform", "Nosuch")

    message = str(raised.value)
    assert "python -m app.cli login nosuchplatform" in message
    assert "mounted volume" not in message


# -- a lock left by a dead process ----------------------------------------------


def test_stale_singleton_lock_is_removed_before_launch(tmp_path):
    """A container stopped mid-run leaves Chrome's SingletonLock on the volume
    pointing at a pid on 'another computer'; Chrome then exits at once and
    Playwright reports TargetClosedError (Juicebox, 2026-09-03)."""
    from app.platforms.browser import clear_stale_profile_lock

    (tmp_path / "SingletonLock").write_text("6195e9d2de60-858")
    (tmp_path / "SingletonCookie").write_text("1")
    (tmp_path / "Preferences").write_text("{}")
    removed = clear_stale_profile_lock(tmp_path)
    assert sorted(removed) == ["SingletonCookie", "SingletonLock"]
    assert not (tmp_path / "SingletonLock").exists()
    assert (tmp_path / "Preferences").exists()
    assert clear_stale_profile_lock(tmp_path) == []
