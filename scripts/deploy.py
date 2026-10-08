"""Deploy to Railway with the commit stamped on it, or check what is live.

    python scripts/deploy.py --check     # live sha vs GitHub main vs this folder
    python scripts/deploy.py             # stamp HEAD, `railway up`, wait until /health shows it

Railway here deploys from `railway up` - an upload of this folder - not from
GitHub, so Railway itself cannot say which commit a deployment holds, and
/health used to carry only a version string bumped by hand. This script sets
BUILD_SHA / BUILD_BRANCH / BUILD_TIME as service variables (without triggering
a deploy of their own), uploads, then polls /health until `build.sha` is the
commit it started from. `--check` is the same comparison on demand.

Refuses a dirty tree: an upload includes uncommitted edits, and a sha that
does not describe what was uploaded is worse than no sha. `--allow-dirty`
exists for a hotfix you intend to commit immediately afterwards.

SERVICE_URL comes from `.env`, as for push_sessions.py and pull_artifacts.py.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
POLL_SECONDS = 20
WAIT_MINUTES = 15


def _tool(name: str) -> str:
    """The executable's full path. On Windows the Railway CLI is an npm shim
    (`railway.cmd`), which a bare name does not launch from Python; `which`
    applies PATHEXT and finds it. Missing tools fail here, by name, rather
    than as WinError 2 three calls in."""
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name!r} is not on PATH. Install it (railway: npm i -g @railway/cli) and log in.")
    return found


def _git(*args: str) -> str:
    return subprocess.run(
        [_tool("git"), *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _railway(*args: str) -> None:
    subprocess.run([_tool("railway"), *args], cwd=ROOT, check=True)


def _live_build(url: str) -> dict:
    data = httpx.get(f"{url}/health", timeout=30).json()
    build = data.get("build") or {}
    build["version"] = data.get("version")
    return build


def _short(sha: str | None) -> str:
    return (sha or "unstamped")[:7]


def check(url: str) -> int:
    """Print the three shas and say whether they agree. Exit 0 only when live == origin/main."""
    try:
        _git("fetch", "-q", "origin")
    except subprocess.CalledProcessError:
        print("could not fetch origin - comparing against the last fetched origin/main")
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    origin_main = _git("rev-parse", "origin/main")
    dirty = bool(_git("status", "--porcelain"))
    live = _live_build(url)
    live_sha = live.get("sha")

    print(f"live        {_short(live_sha)}  branch={live.get('branch') or '?'}  "
          f"built_at={live.get('built_at') or '?'}  version={live.get('version')}  "
          f"deployment={live.get('deployment_id') or '?'}")
    print(f"origin/main {_short(origin_main)}")
    print(f"this folder {_short(head)}  branch={branch}{'  (UNCOMMITTED CHANGES)' if dirty else ''}")

    if not live_sha:
        print("\nThe live build is not stamped - it was deployed before this script "
              "existed, or with a bare `railway up`. Deploy once with "
              "`python scripts/deploy.py` and the question answers itself from then on.")
        return 2
    if live_sha == origin_main:
        print("\nUP TO DATE: Railway runs exactly what GitHub main holds.")
        code = 0
    else:
        behind = _git("rev-list", "--count", f"{live_sha}..{origin_main}") if _known(live_sha) else "?"
        print(f"\nBEHIND: GitHub main is {behind} commit(s) ahead of what Railway runs.")
        code = 1
    if head != origin_main:
        print("This folder differs from GitHub main - push or pull before deploying.")
    return code


def _known(sha: str) -> bool:
    try:
        _git("cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def deploy(url: str, allow_dirty: bool) -> int:
    if _git("status", "--porcelain") and not allow_dirty:
        print("The tree has uncommitted changes. Commit them (or --allow-dirty for a "
              "hotfix you will commit straight after).")
        return 1
    sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if branch != "main":
        print(f"note: deploying from branch {branch!r}, not main")

    print(f"stamping {sha[:7]} ({branch}, {stamp}) on the service...")
    for key, value in (("BUILD_SHA", sha), ("BUILD_BRANCH", branch), ("BUILD_TIME", stamp)):
        _railway("variables", "--set", f"{key}={value}", "--skip-deploys")

    print("uploading...")
    _railway("up", "--detach")

    print(f"waiting for /health to report {sha[:7]} (up to {WAIT_MINUTES} min)...")
    deadline = time.time() + WAIT_MINUTES * 60
    last = None
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            live = _live_build(url)
        except Exception as exc:  # noqa: BLE001 - the old build may be mid-restart
            print(f"  {datetime.now():%H:%M:%S} health unreachable ({exc.__class__.__name__})")
            continue
        last = live.get("sha")
        print(f"  {datetime.now():%H:%M:%S} live={_short(last)}")
        if last == sha:
            print(f"LIVE: {sha[:7]} is serving (deployment {live.get('deployment_id') or '?'}).")
            return 0
    print(f"gave up after {WAIT_MINUTES} min; live is still {_short(last)}. "
          "Check the build logs: railway logs --build")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare, deploy nothing")
    ap.add_argument("--url", help="service URL (default: SERVICE_URL from .env)")
    ap.add_argument("--allow-dirty", action="store_true", help="deploy with uncommitted changes")
    args = ap.parse_args()

    url = (args.url or get_settings().service_url or "").strip().rstrip("/")
    if not url:
        print("Set SERVICE_URL in .env or pass --url.")
        return 2
    return check(url) if args.check else deploy(url, args.allow_dirty)


if __name__ == "__main__":
    sys.exit(main())
