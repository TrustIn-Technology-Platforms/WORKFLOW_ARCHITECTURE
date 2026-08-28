"""Upload locally-captured logins to the deployed service's volume.

The 2FA logins can only be created on a machine with a screen. This packs the
local `.sessions/` and `.profiles/` into a gzipped tar and POSTs it to the
service's /admin/import-sessions endpoint, which unpacks it onto the Railway
volume. Chrome cache directories are excluded, so the upload stays small
(auth lives in Cookies / Local Storage, not the caches).

Usage:
    python scripts/push_sessions.py --url https://<app>.up.railway.app --secret <WEBHOOK_SECRET>

Run `python -m app.cli login <platform>` for each platform first.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys
import tarfile

# Allow `python scripts/push_sessions.py` from the project root (put the repo
# root on the path so `app` imports without needing -m or PYTHONPATH).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx

from app.config import get_settings

# Chrome keeps hundreds of MB of caches that carry no auth. Skipping them turns
# a multi-hundred-MB profile into tens of MB.
_SKIP_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "DawnCache",
    "DawnGraphiteCache", "DawnWebGPUCache", "GrShaderCache", "GraphiteDawnCache",
    "component_crx_cache", "extensions_crx_cache", "CacheStorage",
}


def _excluding(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(tarinfo.name.replace("\\", "/").split("/"))
    if parts & _SKIP_DIRS:
        return None
    return tarinfo


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload sessions/profiles to the deployed volume.")
    ap.add_argument("--url", required=True, help="Base service URL, e.g. https://app.up.railway.app")
    ap.add_argument("--secret", required=True, help="WEBHOOK_SECRET (same as on the server)")
    args = ap.parse_args()

    settings = get_settings()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if settings.session_dir.exists():
            tar.add(settings.session_dir, arcname="sessions", filter=_excluding)
        if settings.browser_profile_dir.exists():
            tar.add(settings.browser_profile_dir, arcname="profiles", filter=_excluding)

    data = buf.getvalue()
    if len(data) < 200:
        sys.exit(
            "Nothing to upload — .sessions and .profiles look empty. "
            "Run `python -m app.cli login noon` (and loxo, juicebox) first."
        )

    url = args.url.rstrip("/") + "/admin/import-sessions"
    print(f"Uploading {len(data) / 1_000_000:.1f} MB to {url} ...")
    try:
        resp = httpx.post(
            url,
            content=data,
            headers={"X-Webhook-Secret": args.secret, "Content-Type": "application/gzip"},
            # Generous per-phase timeouts: a large profile upload over a slow
            # link can take a while, and we would rather wait than fail.
            timeout=httpx.Timeout(60.0, read=1800.0, write=1800.0, connect=60.0),
        )
    except httpx.HTTPError as exc:
        sys.exit(f"Upload failed to connect: {exc}")

    print(resp.status_code, resp.text[:2000])
    if resp.status_code != 200:
        sys.exit(1)
    print("Done. Check GET /health, then fire a ZZ TEST row.")


if __name__ == "__main__":
    main()
