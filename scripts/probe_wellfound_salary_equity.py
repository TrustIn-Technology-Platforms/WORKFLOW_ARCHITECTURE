"""Read the salary and equity controls off Wellfound's New Job form.

Opens `/recruit/jobs/new` with the saved profile and writes down every form
control whose id, name, label or placeholder mentions salary, equity,
currency or compensation - tag, id, type, label text, placeholder, options,
and the checkboxes next to them. **Nothing is typed and nothing is saved**:
opening the form creates nothing (verified 2026-08-28) and the page is closed
without touching Save draft or Publish.

    python scripts/probe_wellfound_salary_equity.py --headed

Output lands in `artifacts/wellfound-salary-equity-probe/<timestamp>/`,
git-ignored.
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

FORM = "https://wellfound.com/recruit/jobs/new"
WORDS = ("salary", "equity", "currency", "compensation", "pay", "noEquity")

_CONTROLS = r"""(words) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const attrs = (el) => { const o = {}; for (const a of el.attributes) o[a.name] = String(a.value).slice(0, 200); return o; };
  const low = (s) => String(s || '').toLowerCase();
  const labelFor = (el) => {
    const id = el.getAttribute('id');
    const byFor = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    if (byFor) return (byFor.innerText || '').trim().slice(0, 200);
    const wrap = el.closest('label');
    if (wrap) return (wrap.innerText || '').trim().slice(0, 200);
    const labelled = el.getAttribute('aria-labelledby');
    if (labelled) { const n = document.getElementById(labelled); if (n) return (n.innerText || '').trim().slice(0, 200); }
    return '';
  };
  const section = (el) => {
    let node = el;
    for (let hop = 0; hop < 8 && node.parentElement; hop++) {
      node = node.parentElement;
      const h = node.querySelector('h1, h2, h3, h4, legend');
      if (h) return (h.innerText || '').trim().slice(0, 120);
    }
    return '';
  };
  const all = [...document.querySelectorAll('input, select, textarea, [role=combobox], [role=checkbox], [role=radio], [contenteditable]')];
  const hit = (el) => {
    const hay = low(JSON.stringify(attrs(el)) + ' ' + labelFor(el) + ' ' + section(el));
    return words.some(w => hay.includes(low(w)));
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', attrs: attrs(el),
    label: labelFor(el), section: section(el), visible: vis(el),
    value: ('value' in el) ? String(el.value || '').slice(0, 80) : '',
    checked: ('checked' in el) ? !!el.checked : null,
    options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text).slice(0, 40) : null,
  });
  const matched = all.filter(hit).map(describe);
  const salaryBlock = (() => {
    const anchor = document.querySelector('#form-input--salaryMin');
    if (!anchor) return '';
    let node = anchor;
    for (let hop = 0; hop < 8 && node.parentElement; hop++) {
      node = node.parentElement;
      if ((node.innerText || '').toLowerCase().includes('equity')) break;
    }
    return (node.innerText || '').trim().slice(0, 3000);
  })();
  return {
    url: location.href,
    matched,
    allIds: all.map(el => el.getAttribute('id')).filter(Boolean),
    salaryBlockText: salaryBlock,
    bodyText: (document.body.innerText || '').slice(0, 6000),
  };
}"""


async def run(headed: bool) -> int:
    settings = get_settings()
    out = Path(settings.artifact_dir) / "wellfound-salary-equity-probe" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    runner = BrowserRunner(settings, headless=not headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("wellfound", trace_name="wellfound-salary-equity-probe") as (context, page):
            for attempt in range(3):
                await page.goto(FORM, wait_until="domcontentloaded", timeout=60_000)
                try:
                    await page.wait_for_selector("#form-input--title", timeout=45_000)
                    break
                except Exception:
                    print(f"  attempt {attempt + 1}: the form did not render at {page.url}")
            else:
                await page.screenshot(path=str(out / "no-form.png"), full_page=True)
                print(f"The New Job form never rendered (last URL {page.url}). Session dead? "
                      f"Screenshot: {out / 'no-form.png'}")
                return 1
            await page.wait_for_timeout(3_000)
            for el in await page.query_selector_all("button:has-text('Agree & Proceed')"):
                try:
                    await el.click()
                except Exception:
                    pass
            found = await page.evaluate(_CONTROLS, list(WORDS))
            (out / "controls.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
            await page.screenshot(path=str(out / "form.png"), full_page=True)
            # Scroll the salary block into view for a close-up.
            try:
                anchor = await page.query_selector("#form-input--salaryMin")
                if anchor:
                    await anchor.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    await page.screenshot(path=str(out / "salary-equity.png"))
            except Exception:
                pass
            print(f"\n{page.url}\n")
            for c in found["matched"]:
                a = c["attrs"]
                print(f"  <{c['tag']} type={c['type']!r} id={a.get('id')!r} name={a.get('name')!r} "
                      f"placeholder={a.get('placeholder')!r}> label={c['label']!r} section={c['section']!r} "
                      f"visible={c['visible']} value={c['value']!r} checked={c['checked']}"
                      + (f" options={c['options']}" if c['options'] else ""))
            print("\n--- text around the salary block ---")
            # A Windows console is cp1252; the form carries zero-width spaces.
            print(found["salaryBlockText"].encode("ascii", "replace").decode())
            print(f"\nSaved: {out}")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    configure_logging()
    return asyncio.run(run(args.headed))


if __name__ == "__main__":
    raise SystemExit(main())
