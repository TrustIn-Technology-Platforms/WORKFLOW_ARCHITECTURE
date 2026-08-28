"""Saved browser logins.

Credentials are never stored and login is never scripted. A human logs in once
per platform in a visible browser, and Playwright's `storage_state` - the
cookies and local storage of that session - is saved and replayed on every
later run. MFA and SSO therefore work, and no password lives in the deployment.

The cost is that sessions expire. This module exists to make that obvious and
quick to fix rather than to pretend it will not happen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import AuthenticationRequired

log = get_logger(__name__)

# Past this, warn before the run rather than after it fails. Platforms differ
# wildly, so this is a nudge to re-capture, never a hard expiry.
STALE_AFTER_DAYS = 14

# Written into a profile directory only after a login has been proved to work.
VERIFIED_MARKER = ".login-verified"


@dataclass(slots=True)
class SessionInfo:
    key: str
    path: Path
    exists: bool
    saved_at: datetime | None = None
    age_days: float | None = None

    @property
    def stale(self) -> bool:
        return self.age_days is not None and self.age_days > STALE_AFTER_DAYS

    @property
    def summary(self) -> str:
        if not self.exists:
            return "no session"
        if self.age_days is None:
            return "saved"
        return f"{self.age_days:.1f}d old" + (" (stale)" if self.stale else "")


class SessionStore:
    """Reads and writes `<session_dir>/<key>.storage_state.json`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.directory = Path(self.settings.session_dir)

    def path_for(self, key: str, filename: str | None = None) -> Path:
        return self.directory / (filename or f"{key}.storage_state.json")

    # ------------------------------------------------------------------
    # browser profiles
    # ------------------------------------------------------------------

    def profile_dir(self, key: str) -> Path:
        # Absolute, to match what is actually handed to Chrome.
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in key)
        return Path(self.settings.browser_profile_dir).resolve() / safe

    def verified_marker(self, key: str) -> Path:
        return self.profile_dir(key) / VERIFIED_MARKER

    def has_profile(self, key: str) -> bool:
        """True only once a login through this profile was actually verified.

        Chrome creates its profile directory the moment it starts, so a merely
        existing directory says nothing about being logged in. Reporting one as a
        session is worse than reporting none: the next command trusts it and
        fails somewhere far less obvious.
        """
        return self.verified_marker(key).is_file()

    def mark_profile_verified(self, key: str) -> Path:
        marker = self.verified_marker(key)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"verified_at": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        return marker

    def profile_info(self, key: str) -> SessionInfo:
        directory = self.profile_dir(key)
        if not self.has_profile(key):
            return SessionInfo(key=key, path=directory, exists=False)

        saved_at = datetime.fromtimestamp(
            self.verified_marker(key).stat().st_mtime, timezone.utc
        )
        age = (datetime.now(timezone.utc) - saved_at).total_seconds() / 86_400
        return SessionInfo(
            key=key, path=directory, exists=True, saved_at=saved_at, age_days=age
        )

    def require_profile(self, key: str, label: str) -> Path:
        if not self.has_profile(key):
            raise AuthenticationRequired(
                f"{label} has no browser profile yet. "
                f"Run: python -m app.cli login {key}"
            )
        return self.profile_dir(key)

    def info(self, key: str, filename: str | None = None) -> SessionInfo:
        path = self.path_for(key, filename)
        if not path.exists():
            return SessionInfo(key=key, path=path, exists=False)

        saved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        age = (datetime.now(timezone.utc) - saved_at).total_seconds() / 86_400
        return SessionInfo(
            key=key, path=path, exists=True, saved_at=saved_at, age_days=age
        )

    def load(self, key: str, filename: str | None = None) -> dict | None:
        """Return the saved state, or None when there is none to load."""
        path = self.path_for(key, filename)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A truncated file is indistinguishable from no login at all, and
            # replaying it would fail three steps deep with a confusing error.
            log.warning(
                "session file unreadable, treating as absent",
                extra={"platform": key, "path": str(path), "error": str(exc)},
            )
            return None

    def require(self, key: str, label: str, filename: str | None = None) -> dict:
        """Load a session, or fail with the message that tells a human the fix."""
        state = self.load(key, filename)
        if state is None:
            raise AuthenticationRequired(
                f"{label} is not logged in. Run: python -m app.cli login {key}"
            )

        info = self.info(key, filename)
        if info.stale:
            log.warning(
                "session is old and may have expired",
                extra={"platform": key, "age_days": round(info.age_days or 0, 1)},
            )
        return state

    def save_state(self, key: str, state: dict, filename: str | None = None) -> Path:
        path = self.path_for(key, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

        # The file is a live credential - anyone holding it is logged in as
        # that user. Keep it off the group/other bits where the OS honours them.
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows and some volumes ignore this
            pass

        log.info("session saved", extra={"platform": key, "path": str(path)})
        return path

    def delete(self, key: str, filename: str | None = None) -> bool:
        path = self.path_for(key, filename)
        if not path.exists():
            return False
        path.unlink()
        log.info("session deleted", extra={"platform": key})
        return True

    def all_keys(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(
            p.name.replace(".storage_state.json", "")
            for p in self.directory.glob("*.storage_state.json")
        )
