"""Export fresh, decrypted cookies from the local browser profiles.

Chrome encrypts a profile's cookie store with an OS-bound key, so the raw
profile is only fully usable on the machine that wrote it. Playwright, however,
reads cookies through the running browser - decrypted - and exports them in a
portable form. This opens each platform's local profile, visits the app so the
session is exercised (and refreshed if the platform rotates it), and writes
`<session_dir>/<key>.storage_state.json`.

Run this right before scripts/push_sessions.py. The server injects these
cookies into the imported profile on its first launch.

Usage:
    python scripts/refresh_storage_state.py            # all platforms with a profile
    python scripts/refresh_storage_state.py loxo noon  # just these
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.platforms.browser import BrowserRunner
from app.platforms.recipe import load_recipes
from app.sessions.store import SessionStore


async def refresh(key: str) -> bool:
    settings = get_settings()
    recipes = load_recipes(settings)
    recipe = recipes.get(key)
    if recipe is None:
        print(f"  {key}: no recipe, skipped")
        return False

    store = SessionStore(settings)
    if not store.has_profile(key):
        print(f"  {key}: no verified profile locally, skipped "
              f"(run: python -m app.cli login {key})")
        return False

    url = recipe.login.url or "about:blank"
    runner = BrowserRunner(settings, headless=True)
    await runner.start()
    try:
        async with runner.profile_context(
            key, trace_name=f"{key}-refresh", channel=recipe.browser_channel
        ) as (context, page):
            try:
                await page.goto(url, wait_until="commit", timeout=60_000)
                # Give the app time to boot and rotate/refresh its cookies.
                await page.wait_for_timeout(12_000)
            except Exception as exc:
                print(f"  {key}: page load was rough ({str(exc)[:80]}); exporting anyway")
            state = await context.storage_state()
    finally:
        await runner.stop()

    path = pathlib.Path(settings.session_dir) / f"{key}.storage_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    cookies = len(state.get("cookies") or [])
    print(f"  {key}: exported {cookies} cookies -> {path}")
    return cookies > 0


async def main() -> None:
    keys = sys.argv[1:] or ["noon", "loxo", "juicebox"]
    print("Refreshing decrypted cookie exports:")
    results = [await refresh(k) for k in keys]
    if not any(results):
        sys.exit("nothing exported")
    print("\nNow upload: python scripts/push_sessions.py --url <app url> --secret <secret>")


asyncio.run(main())
