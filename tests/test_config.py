"""Settings that decide whether the service may sign a platform in itself."""

from __future__ import annotations

from app.config import Settings


def test_no_credentials_means_none_not_a_blank_login():
    settings = Settings(_env_file=None)
    assert settings.credentials_for("noon") is None
    assert settings.credentials_for("wellfound") is None
    # A platform with no fields at all is simply one without a stored login.
    assert settings.credentials_for("totaljobs") is None


def test_credentials_come_back_per_platform(monkeypatch):
    monkeypatch.setenv("NOON_LOGIN_USERNAME", " nicholas@trust-in.co.uk ")
    monkeypatch.setenv("NOON_LOGIN_PASSWORD", "hunter2")
    monkeypatch.setenv("NOON_LOGIN_TOTP_SECRET", "GEZD GNBV GY3T QOJQ")
    settings = Settings(_env_file=None)

    creds = settings.credentials_for("noon")
    assert creds is not None
    assert creds.platform == "noon"
    assert creds.username == "nicholas@trust-in.co.uk"
    assert creds.password == "hunter2"
    assert creds.totp_secret == "GEZD GNBV GY3T QOJQ"
    assert settings.credentials_for("loxo") is None


def test_a_username_without_a_password_is_not_a_login(monkeypatch):
    monkeypatch.setenv("LOXO_LOGIN_USERNAME", "nicholas@trust-in.co.uk")
    settings = Settings(_env_file=None)
    assert settings.credentials_for("loxo") is None


def test_secrets_never_appear_in_repr(monkeypatch):
    """A `%r` of the settings or the credentials in a log line must not be a
    leak. pydantic masks SecretStr; Credentials masks itself."""
    monkeypatch.setenv("WELLFOUND_LOGIN_USERNAME", "marcus@trust-in.co.uk")
    monkeypatch.setenv("WELLFOUND_LOGIN_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("WELLFOUND_LOGIN_TOTP_SECRET", "GEZDGNBVGY3TQOJQ")
    settings = Settings(_env_file=None)

    assert "s3cret-pass" not in repr(settings)
    assert "GEZDGNBVGY3TQOJQ" not in repr(settings)
    creds = settings.credentials_for("wellfound")
    assert "s3cret-pass" not in repr(creds)
    assert "GEZDGNBVGY3TQOJQ" not in repr(creds)
    assert creds.secrets == ["s3cret-pass", "GEZDGNBVGY3TQOJQ"]


def test_configured_map_is_booleans_only(monkeypatch):
    monkeypatch.setenv("NOON_LOGIN_USERNAME", "a@b.c")
    monkeypatch.setenv("NOON_LOGIN_PASSWORD", "x")
    settings = Settings(_env_file=None)
    assert settings.credentials_configured(["noon", "loxo"]) == {"noon": True, "loxo": False}


def test_keepalive_defaults_are_on_and_daily():
    settings = Settings(_env_file=None)
    assert settings.session_keepalive_hours == 24
    assert settings.session_relogin is True
    assert settings.login_check_seconds == 90
