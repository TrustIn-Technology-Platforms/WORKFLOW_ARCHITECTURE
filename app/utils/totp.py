"""Time-based one-time passwords (RFC 6238), for a login's second factor.

Six digits, thirty-second steps, HMAC-SHA1 - the defaults every authenticator
app uses, and what Microsoft's "verification code" option and Loxo's app-based
2FA expect. Standard library only: the whole algorithm is a dozen lines and a
dependency for it would be one more thing to pin.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time

from app.models import PipelineError

_NOT_BASE32 = re.compile(r"[^A-Z2-7]")


class TotpError(PipelineError):
    pass


def normalise_secret(secret: str) -> str:
    """The seed as an authenticator app shows it, with the spaces and dashes
    people copy along with it removed, and the `=` padding restored."""
    cleaned = _NOT_BASE32.sub("", (secret or "").upper().replace("=", ""))
    if not cleaned:
        raise TotpError("The TOTP secret is empty or not base32.")
    return cleaned + "=" * (-len(cleaned) % 8)


def totp_now(secret: str, *, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    key = base64.b32decode(normalise_secret(secret), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10 ** digits:0{digits}d}"


def seconds_left(*, at: float | None = None, period: int = 30) -> float:
    """How long the current code stays valid. A code typed with two seconds
    left is a coin toss, so callers wait for the next window when it is short."""
    now = time.time() if at is None else at
    return period - (now % period)
