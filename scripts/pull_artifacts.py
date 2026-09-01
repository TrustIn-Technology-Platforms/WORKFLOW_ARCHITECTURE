"""Fetch failure artifacts from the deployed service's volume.

Every failed run on the server saves a screenshot, the page DOM and a trace —
next to nobody, on the volume. This lists them and pulls them down, so a
Railway failure is diagnosed by looking at what the browser actually saw
instead of by guessing from the one-line error on the Notion row.

    python scripts/pull_artifacts.py                       # list what is there
    python scripts/pull_artifacts.py --grep wellfound      # list matching
    python scripts/pull_artifacts.py --pull 3              # download newest 3
    python scripts/pull_artifacts.py --pull 3 --grep wellfound

URL and secret come from SERVICE_URL and WEBHOOK_SECRET in .env, same as
scripts/push_sessions.py. Downloads land in artifacts/from-server/, which is
git-ignored with the rest of artifacts/.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="List/fetch failure artifacts from the server.")
    ap.add_argument("--url", help="Base service URL. Defaults to SERVICE_URL from .env")
    ap.add_argument("--secret", help="Defaults to WEBHOOK_SECRET from .env")
    ap.add_argument("--grep", default="", help="Only artifacts whose name contains this.")
    ap.add_argument("--pull", type=int, default=0, metavar="N", help="Download the newest N matches.")
    args = ap.parse_args()

    settings = get_settings()
    url = (args.url or settings.service_url or "").strip().rstrip("/")
    secret = (args.secret or settings.webhook_secret or "").strip()
    if not url:
        sys.exit("No service URL. Pass --url or add SERVICE_URL=... to .env.")
    if not secret:
        sys.exit("No WEBHOOK_SECRET in .env.")

    headers = {"X-Webhook-Secret": secret}
    listing = httpx.get(f"{url}/admin/artifacts", headers=headers, timeout=60)
    listing.raise_for_status()
    artifacts = [
        a for a in listing.json().get("artifacts", [])
        if args.grep.lower() in a["name"].lower()
    ]
    if not artifacts:
        print("No artifacts" + (f" matching {args.grep!r}" if args.grep else "") + " on the server.")
        return 0

    for entry in artifacts[:30]:
        stamp = datetime.fromtimestamp(entry["modified"], timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"  {stamp}  {entry['bytes']:>9}  {entry['name']}")
    if len(artifacts) > 30:
        print(f"  ... and {len(artifacts) - 30} more")

    if not args.pull:
        print("\nAdd --pull N to download the newest N of these.")
        return 0

    dest = pathlib.Path(settings.artifact_dir) / "from-server"
    dest.mkdir(parents=True, exist_ok=True)
    for entry in artifacts[: args.pull]:
        name = entry["name"]
        response = httpx.get(f"{url}/admin/artifacts/{name}", headers=headers, timeout=300)
        response.raise_for_status()
        target = dest / name.replace("/", "__")
        target.write_bytes(response.content)
        print(f"pulled {name} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
