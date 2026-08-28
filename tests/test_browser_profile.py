"""A captured profile must stay with the browser that captured it.

Chrome encrypts its cookie store with a key in `Local State`. Opening the
directory with a different build re-keys it and every cookie the other browser
wrote becomes undecryptable - the session is gone, and the app shows an ordinary
"please log in" screen, so nothing about the failure points at the cause.
"""

from __future__ import annotations

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
