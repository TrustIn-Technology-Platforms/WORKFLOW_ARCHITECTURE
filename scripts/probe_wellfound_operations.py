"""List Wellfound's persisted GraphQL operations for sourcing and pitches.

Opens the Find Talent page, reads the operation registry the running app
holds (the same webpack module the delete uses), and searches the page's own
JavaScript for how the saved-search and pitch-template operations are
called - the variable names around each one. **Read-only: no operation is
sent**; the scripts are fetched the way the browser already fetched them.

    python scripts/probe_wellfound_operations.py --headed

Output: `artifacts/wellfound-operations-probe/<timestamp>/`, git-ignored.
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

WANTED = re.compile(r"SavedSearch|PitchRecipe|SearchFilter|Criteri|Autocomplete|RecruitSearch|Pitch", re.I)

_REGISTRY = r"""() => {
  const chunks = window.webpackChunkangellist;
  if (!chunks || !chunks.push) return {error: 'no webpack chunk array'};
  let req = null;
  chunks.push([[Symbol('probe')], {}, (r) => { req = r; }]);
  if (!req || !req.c) return {error: 'no module cache'};
  for (const key of Object.keys(req.c)) {
    const exp = req.c[key] && req.c[key].exports;
    if (exp && typeof exp.getPersistedQueryAlias === 'function') {
      const out = {module: key, exports: Object.keys(exp), maps: {}};
      for (const name of Object.keys(exp)) {
        const v = exp[name];
        if (v && typeof v === 'object') {
          const keys = Object.keys(v);
          if (keys.length > 50) out.maps[name] = v;
        }
      }
      out.source = String(exp.getPersistedQueryAlias).slice(0, 600);
      // The registry may live in the defining module's closure, not on exports.
      try { out.moduleSource = String(req.m[key]).slice(0, 400); } catch (e) {}
      return out;
    }
  }
  return {error: 'no module exports getPersistedQueryAlias'};
}"""

_SCRIPTS = "() => [...document.querySelectorAll('script[src]')].map(s => s.src).filter(s => s.includes('/webpack/') || s.includes('wellfound'))"
_CHUNK_URLS = r"""() => {
  // Every chunk webpack has loaded so far, from the performance timeline.
  return [...new Set(performance.getEntriesByType('resource').map(e => e.name).filter(n => /\.js(\?|$)/.test(n)))];
}"""
_FETCH = "async (url) => { try { const r = await fetch(url, {credentials: 'omit'}); return r.ok ? await r.text() : ''; } catch (e) { return ''; } }"


async def run(headed: bool) -> int:
    settings = get_settings()
    out = Path(settings.artifact_dir) / "wellfound-operations-probe" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    runner = BrowserRunner(settings, headless=not headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("wellfound", trace_name="wellfound-operations-probe") as (context, page):
            await page.goto("https://wellfound.com/recruit/source", wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_selector("[data-test='toggle control']", timeout=60_000)
            await page.wait_for_timeout(5_000)
            registry = await page.evaluate(_REGISTRY)
            (out / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")
            names: dict[str, str] = {}
            for table in (registry.get("maps") or {}).values():
                for name, value in table.items():
                    if isinstance(value, str):
                        names[name] = value
            wanted = {n: h for n, h in sorted(names.items()) if WANTED.search(n)}
            print(f"registry: {len(names)} operations, {len(wanted)} of interest (exports: {registry.get('exports')}, error: {registry.get('error')})")
            for name, value in wanted.items():
                print(f"  {name}  {value}")
            (out / "wanted.json").write_text(json.dumps(wanted, indent=2), encoding="utf-8")

            found: set[str] = set(await page.evaluate(_SCRIPTS)) | set(await page.evaluate(_CHUNK_URLS))
            # Each route ships its own chunk; opening these two creates nothing
            # (the New Search form saves only on Create search).
            for path in ("/recruit/search/new", "/recruit/account/templates"):
                await page.goto("https://wellfound.com" + path, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(6_000)
                found |= set(await page.evaluate(_SCRIPTS)) | set(await page.evaluate(_CHUNK_URLS))
            urls = sorted(u for u in found if "wellfound.com" in u)
            print(f"\n{len(urls)} scripts")
            hits: dict[str, list[str]] = {}
            # The registry is no longer reachable through a webpack global
            # (the app moved to Next.js chunks by 2026-11-12), so the names
            # are read out of the chunk that defines it instead.
            needles = [n for n in wanted] or [
                "SavedSearchUpdate", "SavedSearchCreate", "SavedSearchLinkWithJobListing",
                "SavedSearchPreferredPitchRecipeUpdate", "SaveSavedSearch", "PitchRecipeCreate",
                "PitchRecipeUpdate", "PitchRecipeDelete", "SkillTagAutocompleteField",
                "LocationTagAutocompleteField", "RoleKeywordAutocomplete", "SearchFilters",
            ]
            for url in urls:
                text = await page.evaluate(_FETCH, url)
                if not text:
                    continue
                if not names and "getPersistedQueryAlias" in text:
                    (out / "registry-chunk.js").write_text(text, encoding="utf-8")
                    print(f"  registry chunk saved from {url.rsplit('/', 1)[-1]}")
                # The bundle carries each operation's GraphQL text and the code
                # that builds its variables; keep the whole file to read both.
                if "getPersistedQueryAlias" not in text and any(n in text for n in needles):
                    (out / "chunks").mkdir(exist_ok=True)
                    (out / "chunks" / url.rsplit("/", 1)[-1].split("?")[0]).write_text(text, encoding="utf-8")
                for needle in needles:
                    for match in re.finditer(re.escape(needle) + r"\b", text):
                        lo, hi = max(0, match.start() - 500), min(len(text), match.end() + 900)
                        hits.setdefault(needle, []).append(f"[{url.rsplit('/', 1)[-1]}] " + text[lo:hi])
            (out / "usage.json").write_text(json.dumps(hits, indent=2), encoding="utf-8")
            print("usage found for: " + ", ".join(f"{k}({len(v)})" for k, v in sorted(hits.items())))
            print(f"Saved: {out}")
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
