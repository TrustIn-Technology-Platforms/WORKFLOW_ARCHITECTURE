"""Open Wellfound's pitch-template form and a saved search's filter panels.

The second half of `probe_wellfound_recruit.py`, for the screens that only
appear after a click:

- **Templates** (`/recruit/account/templates`): press *Create new template*,
  write down the menu, choose the pitch kind, write down the form. Then open
  one existing pitch template on *Edit* to see what the recruiters write.
  Both are closed without saving.
- **A saved search** (`/recruit/source/<id>`): expand each filter section and
  write down the controls inside. Expanding a section selects nothing.

**Nothing is typed, nothing is saved, no candidate is opened.** Every GraphQL
operation is recorded with its variables; a mutation appearing in that list
would mean a click wrote something, and the script prints it loudly.

    python scripts/probe_wellfound_forms.py --headed --search 2869922

Output: `artifacts/wellfound-forms-probe/<timestamp>/`, git-ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402
from app.platforms.browser import BrowserRunner  # noqa: E402
from scripts.probe_wellfound_recruit import _PAGE  # noqa: E402

APP = "https://wellfound.com"
# Reads the page fires on its own; anything else is worth a second look.
_READS = (
    "RecruitProductNavigation", "CompanyCompletenessReactivation", "ResumeSuggestionsStatus",
    "PitchRecipes", "RecruitSourceFrame", "SearchFilters", "RecruitSearchResult",
    "SearchResultTrackAction",
)


class Probe:
    def __init__(self, page, out: Path) -> None:
        self.page, self.out, self.operations, self.n = page, out, [], 0
        page.on("request", self._on_request)
        page.on("response", lambda r: asyncio.ensure_future(self._on_response(r)))
        self.responses: list[dict] = []

    def _on_request(self, request) -> None:
        if "/graphql" not in request.url or request.method != "POST":
            return
        try:
            body = json.loads(request.post_data or "{}")
        except Exception:
            return
        for item in body if isinstance(body, list) else [body]:
            self.operations.append({
                "operation": item.get("operationName"), "variables": item.get("variables"),
                "operationId": (item.get("extensions") or {}).get("operationId"),
            })

    async def _on_response(self, response) -> None:
        if "/graphql" not in response.url:
            return
        try:
            name = response.request.headers.get("x-apollo-operation-name", "")
            body = await response.text()
            self.responses.append({"operation": name, "status": response.status, "body": body[:60_000]})
        except Exception:
            pass

    async def snap(self, name: str) -> dict:
        self.n += 1
        await self.page.wait_for_timeout(1_500)
        found = await self.page.evaluate(_PAGE)
        stem = f"{self.n:02d}-{name}"
        (self.out / f"{stem}.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
        try:
            await self.page.screenshot(path=str(self.out / f"{stem}.png"))
        except Exception:
            pass
        print(f"  [{stem}] controls={len(found['controls'])} buttons={len(found['buttons'])} url={found['url']}")
        return found

    async def click(self, selector: str, what: str) -> bool:
        target = self.page.locator(selector).locator("visible=true").first
        try:
            await target.click(timeout=8_000)
            print(f"  clicked {what}")
            return True
        except Exception as exc:
            print(f"  could not click {what}: {type(exc).__name__}")
            return False


async def templates(probe: Probe) -> None:
    page = probe.page
    await page.goto(APP + "/recruit/account/templates", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector("button:has-text('Create new template')", timeout=45_000)
    if await probe.click("button:has-text('Create new template')", "Create new template"):
        menu = await probe.snap("templates-create-menu")
        # The pitch kind, whatever the menu calls it.
        for label in ("Pitch Template", "Pitch template", "Pitch"):
            if any(label == b["text"] or b["text"].startswith(label) for b in menu["buttons"]) or await page.locator(
                f"text='{label}'"
            ).locator("visible=true").count():
                if await probe.click(f"[role=menuitem]:has-text('{label}'), a:has-text('{label}'), button:has-text('{label}'), li:has-text('{label}')", label):
                    await probe.snap("templates-new-pitch-form")
                break
        # Leave without saving: a fresh load drops whatever the form held.
    await page.goto(APP + "/recruit/account/templates", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector("[data-test^='PitchRecipes-Edit--']", timeout=45_000)
    # The newest pitch template is the best guide to what is written today.
    ids = await page.eval_on_selector_all(
        "[data-test^='PitchRecipes-Edit--']", "els => els.map(e => e.getAttribute('data-test').split('--')[1])"
    )
    newest = max(ids, key=int)
    if await probe.click(f"[data-test='PitchRecipes-Edit--{newest}']", f"Edit on template {newest}"):
        await probe.snap("templates-edit-existing")
    await page.goto(APP + "/recruit/account/templates", wait_until="domcontentloaded", timeout=60_000)


async def search(probe: Probe, search_id: str) -> None:
    page = probe.page
    await page.goto(f"{APP}/recruit/source/{search_id}/active?sort=match", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector("[data-test='toggle control']", timeout=60_000)
    await page.wait_for_timeout(4_000)
    toggles = page.locator("[data-test='toggle control']")
    count = await toggles.count()
    print(f"  {count} filter sections")
    for i in range(count):
        toggle = toggles.nth(i)
        label = ((await toggle.inner_text()) or "").split("\n")[0].strip()[:30] or f"section-{i}"
        try:
            await toggle.scroll_into_view_if_needed()
            await toggle.click(timeout=8_000)
        except Exception as exc:
            print(f"  could not expand {label!r}: {type(exc).__name__}")
            continue
        await probe.snap("search-" + "".join(c if c.isalnum() else "-" for c in label.lower()))
    # The "Linked to <job>" button is deliberately not pressed. On 2026-11-12
    # this probe opened it and pressed Escape, and Wellfound sent
    # SavedSearchUpdate {jobListingId: null} - the search was unlinked from
    # its job. The dropdown clears its selection on Escape and saves at once.


async def run(headed: bool, search_id: str | None) -> int:
    settings = get_settings()
    out = Path(settings.artifact_dir) / "wellfound-forms-probe" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    runner = BrowserRunner(settings, headless=not headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("wellfound", trace_name="wellfound-forms-probe") as (context, page):
            await page.set_viewport_size({"width": 1500, "height": 1000})
            probe = Probe(page, out)
            print("templates")
            await templates(probe)
            if search_id:
                print("saved search")
                await search(probe, search_id)
            await page.wait_for_timeout(1_500)
            (out / "operations.json").write_text(json.dumps(probe.operations, indent=2), encoding="utf-8")
            (out / "responses.json").write_text(json.dumps(probe.responses, indent=2), encoding="utf-8")
            names = sorted({o["operation"] or "?" for o in probe.operations})
            print(f"\ngraphql: {names}")
            unexpected = [n for n in names if n not in _READS]
            if unexpected:
                print(f"NOT IN THE KNOWN-READ LIST - check these wrote nothing: {unexpected}")
            print(f"Saved: {out}")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--search", help="a saved search id whose filter sections to expand")
    args = ap.parse_args()
    configure_logging()
    return asyncio.run(run(args.headed, args.search))


if __name__ == "__main__":
    raise SystemExit(main())
