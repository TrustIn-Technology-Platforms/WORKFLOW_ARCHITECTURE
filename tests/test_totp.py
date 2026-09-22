"""The one-time codes typed into a second-factor prompt."""

from __future__ import annotations

import pytest

from app.utils.totp import TotpError, normalise_secret, seconds_left, totp_now

# RFC 6238, appendix B: the ASCII seed "12345678901234567890" in base32.
RFC_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc_6238_vectors():
    """The last six digits of the RFC's eight-digit SHA-1 values, at its
    published timestamps - what every authenticator app would show."""
    assert totp_now(RFC_SEED, at=59) == "287082"
    assert totp_now(RFC_SEED, at=1111111109) == "081804"
    assert totp_now(RFC_SEED, at=1234567890) == "005924"
    assert totp_now(RFC_SEED, at=20000000000) == "353130"


def test_seed_is_taken_as_an_authenticator_app_shows_it():
    """Spaces, dashes and lower case are what a person copies along with the
    seed; padding is what base32 needs and the app never displays."""
    assert normalise_secret("gezd gnbv-gy3t qojq gezd gnbv gy3t qojq") == RFC_SEED
    assert totp_now("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", at=59) == "287082"


def test_empty_or_garbage_seed_is_a_named_error():
    with pytest.raises(TotpError):
        normalise_secret("")
    with pytest.raises(TotpError):
        normalise_secret("!!!")


def test_seconds_left_counts_down_the_window():
    assert seconds_left(at=0) == 30
    assert seconds_left(at=29) == 1
    assert seconds_left(at=30) == 30
