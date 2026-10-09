"""Map how a Wellfound draft job is deleted, without deleting one.

Wellfound's delete is the last unmapped half of a row's delete
(docs/03-status.md, "Deleting a row"). The recruiter Jobs page, the Drafts
tab, a job's own menu and whatever confirms a delete have never been opened by
the automation. This opens them on a test draft and writes down what is there -
every GraphQL operation the pages send, the job links, the row's menu items,
the confirmation's text and buttons - and greps the app's script chunks for the
mutation the UI calls, so the writer in app/platforms/wellfound_delete.py is
built from a screen and the bundle, not a guess.

**Nothing is deleted.** The menu is opened on a job whose title contains
`ZZ TEST` (or `testzz`) only - a hard guard below; with `--open-confirm` the
Delete item is clicked so the confirmation can be read, and then Cancel is
pressed. The job is looked for again afterwards and must still be there.

    python scripts/probe_wellfound_job_delete.py
    python scripts/probe_wellfound_job_delete.py --name "ZZ TEST" --open-confirm --headed

Output lands in `artifacts/wellfound-job-delete-probe/<timestamp>/`,
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

APP = "https://wellfound.com"
JOBS = f"{APP}/recruit/jobs-beta"
TEST_MARKERS = ("zz test", "testzz")
READY = (
    "a[aria-label='Wellfound'][href^='/recruit/']",
    "a:has-text('Post Job')",
    "a[href='/recruit/jobs/new']",
)

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
    jobLinks: q('a[href*="/recruit/jobs/"]').filter(vis).map(a => ({text: (a.innerText||'').trim().slice(0,140), href: a.getAttribute('href')})),
    buttons: q('button, [role=button], [role=menuitem], [role=tab], [aria-haspopup]').filter(vis).map(describe).slice(0, 250),
    dialogs: q('[role=dialog], [role=alertdialog], [data-test*=modal], [data-test*=Modal], [class*=modal], [class*=Modal], [class*=Dialog]')
      .filter(vis).map(el => ({tag: el.tagName.toLowerCase(), attrs: attrs(el), text: (el.innerText||'').trim().slice(0, 1500)})),
    bodyText: (document.body.innerText || '').slice(0, 5000),
  };
}"""

_ROW_OF = r"""(href) => {
  const link = [...document.querySelectorAll('a[href*="/recruit/jobs/"]')].find(a => a.getAttribute('href') === href);
  if (!link) return null;
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const attrs = (el) => { const o = {}; for (const a of el.attributes) o[a.name] = String(a.value).slice(0, 160); return o; };
  let node = link;
  for (let hop = 0; hop < 10 && node.parentElement; hop++) {
    node = node.parentElement;
    if (node.querySelectorAll('button, [role=button], [aria-haspopup], svg').length) break;
  }
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(), attrs: attrs(el),
    text: (el.innerText || el.textContent || '').trim().slice(0, 120), visible: vis(el),
  });
  return {
    text: (node.innerText || '').trim().slice(0, 600),
    html: node.outerHTML.slice(0, 20000),
    clickables: [...node.querySelectorAll('button, [role=button], [aria-haspopup], a')].filter(vis).map(describe),
  };
}"""

_SCRIPT_URLS = r"""() => performance.getEntriesByType('resource')
  .map(e => e.name).filter(n => /\.js(\?|$)/.test(n))"""

_BUNDLE_PATTERNS = [
    re.compile(r"(mutation|query)\s+[A-Za-z_]*(Delete|Destroy|Archive|Remove|Close|Unpublish)[A-Za-z_]*\s*[({].{0,400}", re.S),
    re.compile(r"(delete|destroy|archive|remove|unpublish)[A-Za-z_]{0,8}Job(Listing|Posting)?[A-Za-z_]{0,12}.{0,300}", re.S),
]


def _is_test(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in TEST_MARKERS)


async def _ready(page: Any, *, attempts: int = 3) -> bool:
    """The recruiter shell, retried past the 'Hand-picked for you' interstitial."""
    for attempt in range(attempts):
        if attempt:
            await page.goto(JOBS, wait_until="domcontentloaded", timeout=60_000)
        for _ in range(20):
            await page.wait_for_timeout(1_500)
            for sel in READY:
                try:
                    if await page.locator(sel).count():
                        return True
                except Exception:
                    pass
            if "/login" in page.url:
                return False
    return False


async def _snap(page: Any, out_dir: Path, name: str) -> dict[str, Any]:
    described = await page.evaluate(_DESCRIBE_ALL)
    try:
        await page.screenshot(path=str(out_dir / f"{name}.png"))
    except Exception:
        pass
    (out_dir / f"{name}.json").write_text(json.dumps(described, indent=2), encoding="utf-8")
    return described


async def _grep_bundle(page: Any, out_dir: Path) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    try:
        urls = await page.evaluate(_SCRIPT_URLS)
    except Exception:
        urls = []
    print(f"\n  Grepping {len(urls)} script(s) for job delete mutations ...")
    for url in urls[:80]:
        try:
            response = await page.request.get(url, timeout=60_000)
            text = await response.text()
        except Exception:
            continue
        for pattern in _BUNDLE_PATTERNS:
            for m in pattern.finditer(text):
                snippet = m.group(0).replace("\n", " ")
                if "job" not in snippet.lower():
                    continue
                hits.append({"script": url[-90:], "snippet": snippet[:500]})
                if len(hits) > 200:
                    break
    (out_dir / "bundle-hits.json").write_text(json.dumps(hits, indent=2), encoding="utf-8")
    print(f"  {len(hits)} bundle hit(s) written to bundle-hits.json")
    for h in hits[:15]:
        print("     " + h["snippet"][:220])
    return hits


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(settings.artifact_dir) / "wellfound-job-delete-probe" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nRecording to {out_dir}\n")

    network: list[dict[str, Any]] = []
    findings: dict[str, Any] = {"name": args.name, "steps": {}}

    runner = BrowserRunner(settings, headless=not args.headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("wellfound", trace_name="wellfound-job-delete-probe") as (
            _context,
            page,
        ):
            async def on_response(response: Any) -> None:
                request = response.request
                if "wellfound.com" not in request.url or request.resource_type not in ("xhr", "fetch"):
                    return
                entry: dict[str, Any] = {
                    "method": request.method,
                    "url": request.url[:200],
                    "status": response.status,
                    "post": (request.post_data or "")[:3000],
                }
                try:
                    if "json" in (response.headers.get("content-type") or ""):
                        entry["body"] = (await response.text())[:1500]
                except Exception:
                    pass
                network.append(entry)

            page.on("response", on_response)

            await page.goto(JOBS, wait_until="domcontentloaded", timeout=60_000)
            if not await _ready(page):
                print(f"  Not on the recruiter shell (url {page.url}). Run: python -m app.cli login wellfound\n")
                await _snap(page, out_dir, "00-not-ready")
                return 2
            await page.wait_for_timeout(3_000)
            listed = await _snap(page, out_dir, "01-jobs")
            findings["steps"]["jobs"] = listed
            print(f"  Landed on {listed['url']}")
            print(f"  {len(listed['jobLinks'])} job link(s); tabs/buttons: " + ", ".join(
                sorted({b['text'] for b in listed['buttons'] if b['text'] and len(b['text']) < 30})[:40]
            ))

            # Drafts tab, when there is one.
            for label in ("Drafts", "Draft"):
                tab = page.get_by_role("tab", name=re.compile(rf"^{label}", re.I))
                if not await tab.count():
                    tab = page.get_by_text(re.compile(rf"^{label}\b", re.I))
                if await tab.count():
                    try:
                        await tab.first.click(timeout=5_000)
                        await page.wait_for_timeout(4_000)
                        findings["steps"]["drafts"] = await _snap(page, out_dir, "02-drafts")
                        print(f"  Opened the {label} tab: {len(findings['steps']['drafts']['jobLinks'])} job link(s)")
                        break
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {label} tab did not open: {str(exc)[:100]}")

            current = findings["steps"].get("drafts") or listed
            links = current["jobLinks"]
            matches = [l for l in links if args.name.lower() in l["text"].lower()]
            titles = sorted({l["text"][:80] for l in links if l["text"]})
            print(f"\n  Titles seen ({len(titles)}):")
            for t in titles[:60]:
                print(f"     {t!r}")
            print(f"\n  {len(matches)} job link(s) match {args.name!r}")
            for m in matches:
                print(f"     {m['text']!r} -> {m['href']}")
            if not matches:
                print("\n  Nothing to open. Post the ZZ TEST document first, or pass --name.\n")
                await _grep_bundle(page, out_dir)
                return 1
            target = matches[0]
            if not _is_test(target["text"]):
                print(f"\n  Refusing to open the menu of {target['text']!r}: only {TEST_MARKERS} jobs are probed.\n")
                return 1

            row = await page.evaluate(_ROW_OF, target["href"])
            findings["steps"]["row"] = row
            (out_dir / "03-row.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
            print("\n  Row text: " + (row or {}).get("text", "").replace("\n", " | ")[:300])
            for c in (row or {}).get("clickables", []):
                print(f"     <{c['tag']}> {c['text']!r} aria={c['attrs'].get('aria-label','')} href={c['attrs'].get('href','')}")

            # Open the job's own page: the menu may live there rather than on the list.
            href = target["href"]
            job_url = href if href.startswith("http") else f"{APP}{href}"
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(5_000)
            job_page = await _snap(page, out_dir, "04-job-page")
            findings["steps"]["job_page"] = job_page
            print("\n  Job page buttons: " + ", ".join(
                sorted({b['text'] or b['attrs'].get('aria-label', '') for b in job_page['buttons']
                        if (b['text'] or b['attrs'].get('aria-label')) and len(b['text']) < 40})[:40]
            ))

            # Any control that reads like a menu or a delete, on the job page.
            opened = False
            for trigger in (
                "button[aria-label*='more' i]", "button[aria-label*='option' i]", "button[aria-label*='menu' i]",
                "button[aria-label*='action' i]", "button:has-text('…')", "button:has-text('...')",
                "[aria-haspopup='menu']", "button:has-text('More')", "button:has-text('Actions')",
            ):
                loc = page.locator(trigger)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=5_000)
                        await page.wait_for_timeout(1_500)
                        opened = True
                        findings["steps"]["menu_trigger"] = trigger
                        break
                    except Exception:
                        continue
            menu = await _snap(page, out_dir, "05-menu")
            items = [b for b in menu["buttons"] if b["visible"] and b["text"] and len(b["text"]) < 40]
            findings["steps"]["menu_items"] = items
            print(f"\n  Menu trigger {'clicked' if opened else 'not found'}; visible items now:")
            for b in items[:40]:
                print(f"     <{b['tag']}> {b['text']!r} role={b['attrs'].get('role','')}")

            delete_item = next((b for b in items if re.search(r"^\s*(delete|remove|archive)\b", b["text"], re.I)), None)
            if delete_item and args.open_confirm:
                label = delete_item["text"]
                print(f"\n  Opening {label!r} to read the confirmation (will cancel) ...")
                await page.get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)).last.click()
                await page.wait_for_timeout(2_000)
                confirm = await _snap(page, out_dir, "06-confirm")
                findings["steps"]["confirm"] = confirm
                for d in confirm["dialogs"]:
                    print("     dialog: " + d["text"].replace("\n", " | ")[:400])
                print("  Buttons visible now: " + ", ".join(
                    sorted({b['text'] for b in confirm['buttons'] if b['text'] and len(b['text']) < 40})
                ))
                cancelled = False
                for word in ("Cancel", "No", "Close", "Nevermind", "Never mind"):
                    loc = page.get_by_role("button", name=re.compile(rf"^{word}$", re.I))
                    if not await loc.count():
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

            await _grep_bundle(page, out_dir)

            # The test job must still be there afterwards.
            await page.goto(job_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(5_000)
            after = await _snap(page, out_dir, "08-job-after")
            findings["still_there"] = args.name.lower() in (after["bodyText"] or "").lower()
            print(f"\n  Job still there afterwards: {findings['still_there']}")
    finally:
        (out_dir / "network.json").write_text(json.dumps(network, indent=2), encoding="utf-8")
        (out_dir / "findings.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        await runner.stop()
        ops = sorted({
            (json.loads(e["post"]).get("operationName") or "?") if e["post"].startswith("{") else "?"
            for e in network if "/graphql" in e["url"]
        } - {"?"})
        print(f"\n  {len(network)} API call(s) recorded; GraphQL operations: {', '.join(ops)[:600]}")
        print(f"  written to {out_dir}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="ZZ TEST", help="Job title (or part) to look for.")
    parser.add_argument("--headed", action="store_true", help="Show the browser.")
    parser.add_argument("--open-confirm", action="store_true",
                        help="Click the Delete item to read the confirmation, then Cancel.")
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
