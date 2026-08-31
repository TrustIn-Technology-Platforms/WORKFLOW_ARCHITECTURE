"""Upload locally-captured logins to the deployed service's volume.

The 2FA logins can only be created on a machine with a screen. This packs the
local `.sessions/` and `.profiles/` into a gzipped tar and POSTs it to the
service's /admin/import-sessions endpoint, which unpacks it onto the Railway
volume. Chrome cache directories are excluded, so the upload stays small
(auth lives in Cookies / Local Storage, not the caches).

Usage:
    python scripts/refresh_storage_state.py          # always run this first
    python scripts/push_sessions.py --dry-run        # see the archive, upload nothing
    python scripts/push_sessions.py --url https://myapp.up.railway.app

The URL and the secret both fall back to SERVICE_URL and WEBHOOK_SECRET in
`.env`, so with those set the last line is just `python scripts/push_sessions.py`.
Keep the secret there rather than passing it: a secret on a command line ends up
in the shell history.
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
from app.platforms.recipe import load_recipes

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
    ap.add_argument(
        "--url",
        help="Base service URL, e.g. https://app.up.railway.app. "
             "Defaults to SERVICE_URL from .env",
    )
    ap.add_argument(
        "--secret",
        help="WEBHOOK_SECRET, same as on the server. Defaults to the one in "
             ".env, which is where it should stay - a secret typed here goes "
             "into the shell history",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Build the archive and report what is in it. Uploads nothing.",
    )
    args = ap.parse_args()

    settings = get_settings()
    url = (args.url or settings.service_url or "").strip()
    secret = (args.secret or settings.webhook_secret or "").strip()

    if not args.dry_run:
        # Named separately: "one of these is missing" sends people looking at
        # the wrong one.
        if not url:
            sys.exit(
                "No service URL. Either pass --url https://<app>.up.railway.app "
                "or add SERVICE_URL=... to .env.\nFind it in Railway: your "
                "service -> Settings -> Networking -> Public Domain."
            )
        if not secret:
            sys.exit(
                "No WEBHOOK_SECRET. Add it to .env - it must match the "
                "WEBHOOK_SECRET variable set on the Railway service."
            )
        if url.startswith("<") or "your-app" in url:
            sys.exit(
                f"{url!r} is the placeholder from the docs, not a real address. "
                "Put your own Railway domain there."
            )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if settings.session_dir.exists():
            tar.add(settings.session_dir, arcname="sessions", filter=_excluding)
        # One directory per platform that exists, verified, and nothing else.
        # A retired profile keeps its `.login-verified` marker - the abandoned
        # `juicebox.chromium-failed-20260827` still carries one - so the marker
        # alone does not identify a live platform. Matching recipe keys does,
        # and it also stops a stray directory arriving on the volume looking
        # like a platform that does not exist.
        keys = set(load_recipes(settings))
        if settings.browser_profile_dir.exists():
            for profile in sorted(settings.browser_profile_dir.iterdir()):
                if not profile.is_dir():
                    continue
                if profile.name not in keys:
                    print(f"  skipping {profile.name} - not a platform in platforms/")
                    continue
                if not (profile / ".login-verified").is_file():
                    print(f"  skipping {profile.name} - no verified login in it")
                    continue
                print(f"  including {profile.name}")
                tar.add(profile, arcname=f"profiles/{profile.name}", filter=_excluding)

    data = buf.getvalue()
    if args.dry_run:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
        tops = sorted({n.split("/")[1] for n in names if n.count("/") >= 1})
        print(f"\narchive: {len(data) / 1_000_000:.1f} MB, {len(names)} entries")
        print("would upload:", ", ".join(tops))
        print("\nDry run - nothing was uploaded.")
        return
    if len(data) < 200:
        sys.exit(
            "Nothing to upload — .sessions and .profiles look empty. "
            "Run `python -m app.cli login noon` (and loxo, juicebox) first."
        )

    endpoint = url.rstrip("/") + "/admin/import-sessions"
    print(f"Uploading {len(data) / 1_000_000:.1f} MB to {endpoint} ...")
    try:
        resp = httpx.post(
            endpoint,
            content=data,
            headers={"X-Webhook-Secret": secret, "Content-Type": "application/gzip"},
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
