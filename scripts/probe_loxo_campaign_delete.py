"""Map how a Loxo Outreach campaign is deleted, without deleting one.

The Loxo delete is the last unmapped half of a row's delete (docs/03-status.md,
"Deleting a row"): the campaign list, the row's menu, and whatever confirms a
delete have never been opened by the automation. This opens them on a test
campaign and writes down what is there - the list's own JSON or GraphQL
calls, the row's menu items, the confirmation dialog's text and buttons, the
campaign settings flyout - so the writer in app/platforms/loxo_delete.py is
built from a screen, not a guess.

**Nothing is deleted.** The menu is opened on a campaign whose name contains
`testzz` or `ZZ TEST` only (a hard guard below); with `--open-confirm` the
Delete item is clicked so the confirmation can be read, and then Cancel is
pressed. The campaign is searched for again afterwards and must still be
there.

    python scripts/probe_loxo_campaign_delete.py
    python scripts/probe_loxo_campaign_delete.py --name "testzz Abundant" --open-confirm --headed

Output lands in `artifacts/loxo-campaign-delete-probe/<timestamp>/`,
git-ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402
from app.platforms.browser import BrowserRunner  # noqa: E402

APP = "https://app.loxo.co"
AGENCY = "28356"
LOGGED_OUT = ("/sign_in", "/login", "/users/sign_in")
# The only campaigns whose menus this will open. A client's campaign is never
# touched, even read-only: a mis-click on a confirm is a deleted campaign.
TEST_MARKERS = ("testzz", "zz test")

# Every element that could be a row's menu trigger or a menu item, described.
_DESCRIBE_ALL = r"""() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const attrs = (el) => { const o = {}; for (const a of el.attributes) o[a.name] = String(a.value).slice(0, 160); return o; };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(), attrs: attrs(el),
    text: (el.innerText || el.textContent || '').trim().slice(0, 160), visible: vis(el),
  });
  const q = (sel) => [...document.querySelectorAll(sel)];
  return {
    url: location.href,
    campaignLinks: q('a[href*="/campaigns/"]').filter(vis).map(a => ({text: (a.innerText||'').trim().slice(0,120), href: a.getAttribute('href')})),
    buttons: q('button, [role=button], [role=menuitem], [aria-haspopup]').filter(vis).map(describe).slice(0, 200),
    dialogs: q('[role=dialog], [role=alertdialog], [data-testid*=modal], [data-testid*=dialog], [class*=Modal], [class*=modal], [class*=Dialog]')
      .filter(vis).map(el => ({tag: el.tagName.toLowerCase(), attrs: attrs(el), text: (el.innerText||'').trim().slice(0, 1500)})),
    glyphs: q('span, i').filter(el => vis(el) && /^(more_vert|more_horiz|more|delete|settings|archive|close)$/.test((el.textContent||'').trim()))
      .map(describe).slice(0, 80),
    bodyText: (document.body.innerText || '').slice(0, 4000),
  };
}"""

# The row of the campaign list that holds this link: climb until the ancestor
# carries more than the title (a glyph, a button, or a second line), then
# describe everything clickable inside it.
_ROW_OF = r"""(href) => {
  const link = [...document.querySelectorAll('a[href*="/campaigns/"]')].find(a => a.getAttribute('href') === href);
  if (!link) return null;
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const attrs = (el) => { const o = {}; for (const a of el.attributes) o[a.name] = String(a.value).slice(0, 160); return o; };
  let node = link;
  for (let hop = 0; hop < 10 && node.parentElement; hop++) {
    node = node.parentElement;
    const clickables = node.querySelectorAll('button, [role=button], [aria-haspopup], input[type=checkbox]');
    const glyphs = [...node.querySelectorAll('span, i')].filter(el => /^(more_vert|more_horiz|settings|delete)$/.test((el.textContent||'').trim()));
    if (clickables.length || glyphs.length) break;
  }
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(), attrs: attrs(el),
    text: (el.innerText || el.textContent || '').trim().slice(0, 120), visible: vis(el),
  });
  return {
    text: (node.innerText || '').trim().slice(0, 600),
    html: node.outerHTML.slice(0, 20000),
    clickables: [...node.querySelectorAll('button, [role=button], [aria-haspopup], input[type=checkbox], span, i')]
      .filter(el => vis(el) && (el.tagName !== 'SPAN' && el.tagName !== 'I' || /^(more_vert|more_horiz|settings|delete|share)$/.test((el.textContent||'').trim())))
      .map(describe),
  };
}"""

# Scripts the page loaded, so the bundle can be grepped for the delete route
# the UI calls - the one thing a cancelled dialog cannot show.
_SCRIPT_URLS = r"""() => performance.getEntriesByType('resource')
  .map(e => e.name).filter(n => /\.js(\?|$)/.test(n))"""

_BUNDLE_PATTERNS = [
    # REST routes mentioning campaigns next to a delete/destroy verb.
    re.compile(r".{0,160}campaigns?[^\n]{0,80}?(delete|destroy|DELETE|archive).{0,160}", re.I),
    re.compile(r".{0,160}(delete|destroy|archive)[A-Za-z_]{0,12}[Cc]ampaign.{0,200}"),
    # GraphQL operation names.
    re.compile(r"(mutation|query)\s+[A-Za-z_]*[Cc]ampaign[A-Za-z_]*\s*[({].{0,200}"),
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _is_test_campaign(name: str) -> bool:
    low = (name or "").lower()
    return any(marker in low for marker in TEST_MARKERS)


async def _wait_for_text(page: Any, needle: str, *, seconds: int = 90) -> bool:
    """Loxo paints nothing for 10-20s and sometimes shows a 'Try again' card."""
    for _ in range(seconds // 2):
        await page.wait_for_timeout(2_000)
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        if needle in text:
            return True
        retry = page.get_by_text("Try again", exact=True)
        if await retry.count():
            await retry.first.click()
    return False


async def _grep_bundle(page: Any, out_dir: Path) -> list[dict[str, str]]:
    """Where the UI's delete goes - the one thing a cancelled dialog cannot show."""
    hits: list[dict[str, str]] = []
    try:
        urls = await page.evaluate(_SCRIPT_URLS)
    except Exception:
        urls = []
    print(f"\n  Grepping {len(urls)} script(s) for the campaign delete route ...")
    for url in urls[:40]:
        try:
            response = await page.request.get(url, timeout=60_000)
            text = await response.text()
        except Exception:
            continue
        if len(text) > 25_000_000:
            continue
        for pattern in _BUNDLE_PATTERNS:
            for m in pattern.finditer(text):
                snippet = m.group(0).replace("\n", " ")
                if "campaign" not in snippet.lower():
                    continue
                hits.append({"script": url[-80:], "snippet": snippet[:400]})
                if len(hits) > 150:
                    break
    (out_dir / "bundle-hits.json").write_text(json.dumps(hits, indent=2), encoding="utf-8")
    print(f"  {len(hits)} bundle hit(s) written to bundle-hits.json")
    for h in hits[:12]:
        print("     " + h["snippet"][:200])
    return hits


async def _snap(page: Any, out_dir: Path, name: str) -> dict[str, Any]:
    described = await page.evaluate(_DESCRIBE_ALL)
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"), full_page=False)
    except Exception:
        pass
    (out_dir / f"{name}.json").write_text(json.dumps(described, indent=2), encoding="utf-8")
    return described


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(settings.artifact_dir) / "loxo-campaign-delete-probe" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nRecording to {out_dir}\n")

    network: list[dict[str, Any]] = []
    findings: dict[str, Any] = {"name": args.name, "steps": {}}

    runner = BrowserRunner(settings, headless=not args.headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("loxo", trace_name="loxo-campaign-delete-probe") as (
            _context,
            page,
        ):
            async def on_response(response: Any) -> None:
                request = response.request
                if "loxo.co" not in request.url or request.resource_type not in ("xhr", "fetch"):
                    return
                entry: dict[str, Any] = {
                    "method": request.method,
                    "url": request.url[:300],
                    "status": response.status,
                    "post": (request.post_data or "")[:4000],
                }
                try:
                    if "json" in (response.headers.get("content-type") or ""):
                        entry["body"] = (await response.text())[:1500]
                except Exception:
                    pass
                network.append(entry)

            page.on("response", on_response)

            list_url = f"{APP}/agencies/{args.agency}/campaigns"
            await page.goto(list_url, wait_until="domcontentloaded", timeout=90_000)
            if not await _wait_for_text(page, "Add Campaign"):
                if any(marker in page.url for marker in LOGGED_OUT):
                    print(f"  Logged out - landed on {page.url}. Run: python -m app.cli login loxo\n")
                else:
                    print(f"  The campaign list never rendered (url {page.url}).\n")
                await _snap(page, out_dir, "00-not-rendered")
                return 2
            await page.wait_for_timeout(2_000)
            findings["steps"]["list"] = await _snap(page, out_dir, "01-list")

            # -- search for the test campaign ------------------------------
            search = page.locator("input[placeholder*='Search' i]").first
            await search.fill(args.name)
            await page.wait_for_timeout(4_000)
            listed = await _snap(page, out_dir, "02-search")
            findings["steps"]["search"] = listed
            matches = [
                link for link in listed["campaignLinks"]
                if args.name.lower() in link["text"].lower()
            ]
            print(f"  {len(matches)} campaign link(s) match {args.name!r}:")
            for m in matches:
                print(f"     {m['text']!r} -> {m['href']}")
            if not matches:
                print("\n  Nothing to open. Create a `testzz` campaign first, or pass --name.\n")
                await _grep_bundle(page, out_dir)
                return 1
            target = matches[0]
            if not _is_test_campaign(target["text"]):
                print(
                    f"\n  Refusing to open the menu of {target['text']!r}: only a campaign "
                    f"named with {TEST_MARKERS} is probed.\n"
                )
                return 1

            # -- the row and its menu ---------------------------------------
            row = await page.evaluate(_ROW_OF, target["href"])
            findings["steps"]["row"] = row
            (out_dir / "03-row.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
            print("\n  Row text: " + (row or {}).get("text", "").replace("\n", " | ")[:300])
            print("  Row clickables:")
            for c in (row or {}).get("clickables", []):
                print(f"     <{c['tag']}> {c['text']!r} {c['attrs'].get('aria-label','')} {c['attrs'].get('class','')[:60]}")

            menu_opened = False
            for trigger in (
                f"xpath=//a[@href='{target['href']}']/ancestor::*[self::tr or self::li or self::div][.//*[normalize-space(text())='more_vert' or normalize-space(text())='more_horiz']][1]//*[normalize-space(text())='more_vert' or normalize-space(text())='more_horiz']",
                f"xpath=//a[@href='{target['href']}']/ancestor::*[self::tr or self::li or self::div][.//button[@aria-haspopup or contains(@aria-label,'ore') or contains(@aria-label,'ption') or contains(@aria-label,'enu')]][1]//button[@aria-haspopup or contains(@aria-label,'ore') or contains(@aria-label,'ption') or contains(@aria-label,'enu')]",
            ):
                loc = page.locator(trigger)
                if await loc.count():
                    try:
                        await loc.first.scroll_into_view_if_needed()
                        await loc.first.click(timeout=8_000)
                        menu_opened = True
                        findings["steps"]["menu_trigger"] = trigger
                        break
                    except Exception as exc:  # noqa: BLE001 - try the next shape
                        print(f"     trigger failed: {str(exc)[:100]}")
            if not menu_opened:
                # Hovering a row sometimes reveals the menu; try that once.
                await page.locator(f"a[href='{target['href']}']").first.hover()
                await page.wait_for_timeout(1_000)
                findings["steps"]["row_after_hover"] = await page.evaluate(_ROW_OF, target["href"])
                await _snap(page, out_dir, "04-row-hovered")
                print("\n  No row menu trigger found (see 03-row.json / 04-row-hovered.json).")
            else:
                await page.wait_for_timeout(1_500)
                menu = await _snap(page, out_dir, "05-row-menu")
                items = [
                    b for b in menu["buttons"]
                    if b["visible"] and b["text"] and len(b["text"]) < 40
                ]
                findings["steps"]["menu_items"] = items
                print("\n  Menu items visible after the click:")
                for b in items:
                    print(f"     <{b['tag']}> {b['text']!r} role={b['attrs'].get('role','')}")

                delete_item = next(
                    (b for b in items if re.search(r"delete|remove|archive", b["text"], re.I)), None
                )
                if delete_item and args.open_confirm:
                    label = delete_item["text"]
                    print(f"\n  Opening {label!r} to read the confirmation (will cancel) ...")
                    await page.get_by_text(re.compile(rf"^\s*(delete)?\s*{re.escape(label)}\s*$", re.I)).last.click()
                    await page.wait_for_timeout(1_800)
                    confirm = await _snap(page, out_dir, "06-confirm")
                    findings["steps"]["confirm"] = confirm
                    print("  Dialogs:")
                    for d in confirm["dialogs"]:
                        print("     " + d["text"].replace("\n", " | ")[:400])
                    print("  Buttons visible now:")
                    for b in confirm["buttons"]:
                        if b["text"] and len(b["text"]) < 40:
                            print(f"     <{b['tag']}> {b['text']!r}")
                    # Cancel, never confirm. Escape as the fallback.
                    cancelled = False
                    for word in ("Cancel", "No", "close", "Close"):
                        loc = page.get_by_text(word, exact=True)
                        if await loc.count():
                            try:
                                await loc.last.click(timeout=5_000)
                                cancelled = True
                                break
                            except Exception:
                                continue
                    if not cancelled:
                        await page.keyboard.press("Escape")
                    await page.wait_for_timeout(1_500)
                    await _snap(page, out_dir, "07-after-cancel")
                else:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(800)

            # -- the campaign page: header controls and the settings flyout --
            href = target["href"]
            stages_url = (href if href.startswith("http") else f"{APP}{href}").rstrip("/")
            if not stages_url.endswith("/stages"):
                stages_url += "/stages"
            await page.goto(stages_url, wait_until="domcontentloaded", timeout=90_000)
            if await _wait_for_text(page, "Stages"):
                await page.wait_for_timeout(2_000)
                findings["steps"]["stages_page"] = await _snap(page, out_dir, "08-stages")
                gear = page.get_by_text("settings", exact=True)
                if await gear.count():
                    await gear.first.click()
                    try:
                        await page.wait_for_selector("text=Campaign name", timeout=20_000)
                        await page.wait_for_timeout(1_200)
                        flyout = await _snap(page, out_dir, "09-settings-flyout")
                        findings["steps"]["flyout"] = flyout
                        fly = page.locator("[data-testid=flyout_container]")
                        if await fly.count():
                            findings["steps"]["flyout_text"] = await fly.first.inner_text()
                            print("\n  Settings flyout: " + findings["steps"]["flyout_text"].replace("\n", " | ")[:600])
                            close = fly.get_by_text("close", exact=True)
                            if await close.count():
                                await close.first.click()
                    except Exception as exc:  # noqa: BLE001
                        print(f"  settings flyout did not open: {str(exc)[:120]}")
                        await page.keyboard.press("Escape")
            else:
                print(f"  The stages page never rendered ({page.url}).")

            # -- the bundle: where the UI's delete goes ----------------------
            await _grep_bundle(page, out_dir)

            # -- after everything: the test campaign must still be listed ----
            await page.goto(list_url, wait_until="domcontentloaded", timeout=90_000)
            if await _wait_for_text(page, "Add Campaign"):
                await page.locator("input[placeholder*='Search' i]").first.fill(args.name)
                await page.wait_for_timeout(4_000)
                after = await _snap(page, out_dir, "10-list-after")
                still = [l for l in after["campaignLinks"] if l["href"] == target["href"]]
                findings["still_listed"] = bool(still)
                print(f"\n  Campaign still listed afterwards: {bool(still)}")
    finally:
        (out_dir / "network.json").write_text(json.dumps(network, indent=2), encoding="utf-8")
        (out_dir / "findings.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        await runner.stop()
        print(f"\n  {len(network)} API call(s) recorded; written to {out_dir}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="testzz", help="Campaign name (or prefix) to search for.")
    parser.add_argument("--agency", default=AGENCY, help="Loxo agency id.")
    parser.add_argument("--headed", action="store_true", help="Show the browser.")
    parser.add_argument(
        "--open-confirm", action="store_true",
        help="Click the row menu's Delete item to read the confirmation, then Cancel.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, as_json=settings.log_json)
    settings.ensure_dirs()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
