"""Map Wellfound's recruiter surfaces beyond the job form: sourcing and outreach.

Opens each recruiter page with the saved profile and writes down what is on
it - every link, button, form control and heading, the visible text, a
screenshot, and the name of every GraphQL operation the page fired while
loading. **Read-only**: nothing is typed, nothing is clicked except the
cookie banner, and no candidate is opened or messaged.

    python scripts/probe_wellfound_recruit.py --headed
    python scripts/probe_wellfound_recruit.py --headed --url /recruit/source --url /recruit/messages

Output lands in `artifacts/wellfound-recruit-probe/<timestamp>/`, git-ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402
from app.platforms.browser import BrowserRunner  # noqa: E402

APP = "https://wellfound.com"
# The recruiter nav as the Jobs page listed it on 2026-08-28
# (artifacts/wellfound-probe-1-jobs.json): Find Talent is the sourcing search,
# Messages the outreach side.
DEFAULT_PAGES = (
    "/recruit/source",
    "/recruit/conversations",
    "/recruit/applicants/jobs",
    "/recruit/track",
    "/recruit/dashboard",
)

_PAGE = r"""() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 160);
  const attrs = (el) => { const o = {}; for (const a of el.attributes) { if (a.name !== 'class' && a.name !== 'style') o[a.name] = String(a.value).slice(0, 200); } return o; };
  const labelFor = (el) => {
    const id = el.getAttribute('id');
    const byFor = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    if (byFor) return text(byFor);
    const wrap = el.closest('label');
    return wrap ? text(wrap) : '';
  };
  const links = [...document.querySelectorAll('a[href]')].map(a => ({
    href: a.getAttribute('href'), text: text(a), aria: a.getAttribute('aria-label') || '', visible: vis(a),
  })).filter(l => l.href && !l.href.startsWith('#'));
  const seen = new Set();
  const uniqLinks = links.filter(l => { const k = l.href + '|' + l.text; if (seen.has(k)) return false; seen.add(k); return true; });
  const buttons = [...document.querySelectorAll('button, [role=button], [role=tab], [role=menuitem]')]
    .filter(vis).map(b => ({ text: text(b), attrs: attrs(b) })).filter(b => b.text || b.attrs['aria-label']);
  const controls = [...document.querySelectorAll('input, select, textarea, [role=combobox], [contenteditable=true]')].map(el => ({
    tag: el.tagName.toLowerCase(), attrs: attrs(el), label: labelFor(el), visible: vis(el),
    value: ('value' in el) ? String(el.value || '').slice(0, 80) : '',
    options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text).slice(0, 60) : null,
  }));
  const headings = [...document.querySelectorAll('h1, h2, h3, h4, legend')].filter(vis).map(text).filter(Boolean);
  return {
    url: location.href, title: document.title, headings, links: uniqLinks, buttons, controls,
    bodyText: (document.body.innerText || '').slice(0, 12000),
  };
}"""


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "root"


async def run(headed: bool, pages: list[str], settle_ms: int) -> int:
    settings = get_settings()
    out = Path(settings.artifact_dir) / "wellfound-recruit-probe" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    runner = BrowserRunner(settings, headless=not headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("wellfound", trace_name="wellfound-recruit-probe") as (context, page):
            operations: list[dict] = []

            def on_request(request) -> None:
                if "/graphql" not in request.url or request.method != "POST":
                    return
                try:
                    body = json.loads(request.post_data or "{}")
                except Exception:
                    return
                for item in body if isinstance(body, list) else [body]:
                    operations.append({
                        "page": page.url,
                        "operation": item.get("operationName"),
                        "variables": item.get("variables"),
                        "operationId": (item.get("extensions") or {}).get("operationId"),
                    })

            page.on("request", on_request)
            summary = []
            for path in pages:
                url = path if path.startswith("http") else APP + path
                start = len(operations)
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    status = response.status if response else None
                except Exception as exc:  # a probe reports, it does not stop
                    print(f"  {path}: navigation failed - {type(exc).__name__}")
                    summary.append({"asked": path, "error": type(exc).__name__})
                    continue
                await page.wait_for_timeout(settle_ms)
                for el in await page.query_selector_all("button:has-text('Agree & Proceed')"):
                    try:
                        await el.click()
                    except Exception:
                        pass
                found = await page.evaluate(_PAGE)
                found["asked"] = path
                found["status"] = status
                found["operations"] = operations[start:]
                slug = _slug(path)
                (out / f"{slug}.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
                try:
                    await page.screenshot(path=str(out / f"{slug}.png"), full_page=True)
                except Exception:
                    await page.screenshot(path=str(out / f"{slug}.png"))
                ops = sorted({o["operation"] for o in found["operations"] if o["operation"]})
                print(f"\n=== {path} -> {found['url']}  [{status}]  {found['title']!r}")
                print(f"  headings: {found['headings'][:12]}")
                print(f"  controls: {len(found['controls'])}  buttons: {len(found['buttons'])}  links: {len(found['links'])}")
                print(f"  graphql: {ops}")
                summary.append({"asked": path, "landed": found["url"], "status": status, "operations": ops})
                if "/login" in found["url"]:
                    print("  The session is signed out - run `python -m app.cli login wellfound`.")
                    break
            (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"\nSaved: {out}")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--url", action="append", help="a path or URL to read; repeatable (default: the recruiter nav)")
    ap.add_argument("--settle-ms", type=int, default=8_000, help="how long to let each page render")
    args = ap.parse_args()
    configure_logging()
    return asyncio.run(run(args.headed, args.url or list(DEFAULT_PAGES), args.settle_ms))


if __name__ == "__main__":
    raise SystemExit(main())
