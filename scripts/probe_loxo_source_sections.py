"""Map the filter sections of Loxo's Source screen that the writer does not fill yet.

[loxo_source.py](../app/platforms/loxo_source.py) writes Title and Skills. The
other sections in the same panel - Years of Experience, Company, Tenure,
Company Size, Industry - have never been expanded by the automation, so their
controls are unknown: a chip box bound to a taxonomy, a min/max pair, a slider,
or a checklist. Each shape needs a different writer, and guessing has already
cost three broken live runs on this screen.

This opens the Source screen with the saved profile, expands each section in
turn, and writes down what is inside: text, inputs with every attribute, the
outer HTML, a screenshot. For a section whose input takes text it types one
probe word and records the suggestions, then clears the box without pressing
Enter. **Nothing is committed and nothing is saved** - filters on this screen
do not survive a reload unless the bookmark Save control is used, and this
never touches it.

    python scripts/probe_loxo_source_sections.py --job 3658508
    python scripts/probe_loxo_source_sections.py --job 3658508 --headed
    python scripts/probe_loxo_source_sections.py --job 3658508 --sections "Company"

Output lands in `artifacts/loxo-source-sections/<timestamp>/`, git-ignored.
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
from app.platforms.loxo_source import (  # noqa: E402
    _CLICK_HEADER,
    _HELPERS,
    _SECTION_TEXT,
    _SUGGESTIONS,
)

APP = "https://app.loxo.co"
AGENCY = "28356"
LOGGED_OUT = ("/sign_in", "/login", "/users/sign_in")

DEFAULT_SECTIONS = (
    "Years of Experience",
    "Company",
    "Tenure",
    "Company Size",
    "Industry",
)

# One harmless word per section, typed to see whether the box autocompletes
# and against which vocabulary. Never followed by Enter.
PROBE_WORDS = {
    "Company": "Stripe",
    "Industry": "Insur",
}

# Everything inside a section, described element by element. The section's
# own boundary logic is the writer's, imported above, so what this records is
# exactly what the writer would see.
_DESCRIBE = ("(label) => {" + _HELPERS + r"""
  const nodes = sectionNodes(label);
  if (!nodes) return null;
  const attrs = (el) => {
    const out = {};
    for (const a of el.attributes) out[a.name] = String(a.value).slice(0, 200);
    return out;
  };
  const rect = (el) => { const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    attrs: attrs(el),
    value: el.value !== undefined ? String(el.value).slice(0, 200) : undefined,
    text: (el.innerText || '').trim().slice(0, 200),
    rect: rect(el),
  });
  const all = nodes.flatMap(n => [n, ...n.querySelectorAll('*')]);
  return {
    text: nodes.map(n => n.innerText || '').join(String.fromCharCode(10)),
    html: nodes.map(n => n.outerHTML).join(String.fromCharCode(10)).slice(0, 60000),
    inputs: all.filter(el => ['INPUT','TEXTAREA','SELECT'].includes(el.tagName)).map(describe),
    sliders: all.filter(el => el.getAttribute('role') === 'slider'
                           || (el.getAttribute('class') || '').match(/slider|range/i)).map(describe),
    buttons: all.filter(el => el.tagName === 'BUTTON' || el.getAttribute('role') === 'button').map(describe),
    options: all.filter(el => el.tagName === 'OPTION' || el.getAttribute('role') === 'option'
                           || el.getAttribute('role') === 'checkbox'
                           || el.getAttribute('role') === 'radio').map(describe),
    labels: all.filter(el => ['LABEL','LEGEND','H1','H2','H3','H4','H5','H6'].includes(el.tagName)
                          || (el.children.length === 0 && (el.innerText || '').trim().length
                              && (el.innerText || '').trim().length < 60))
               .map(el => (el.innerText || '').trim()).filter(Boolean),
  };
}""")

# Focus the nth visible input inside a section and report which it was.
_FOCUS_NTH = ("([label, n]) => {" + _HELPERS + r"""
  const nodes = sectionNodes(label) || [];
  const inputs = nodes.flatMap(node => [...node.querySelectorAll('input, textarea')])
    .filter(i => i.getBoundingClientRect().width);
  const inp = inputs[n];
  if (!inp) return null;
  inp.focus();
  return {type: inp.getAttribute('type') || '', placeholder: inp.getAttribute('placeholder') || '',
          value: inp.value || '', count: inputs.length};
}""")

_INPUT_VALUES = ("(label) => {" + _HELPERS + r"""
  const nodes = sectionNodes(label) || [];
  return nodes.flatMap(node => [...node.querySelectorAll('input, textarea')])
    .filter(i => i.getBoundingClientRect().width).map(i => i.value || '');
}""")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def expand(page: Any, label: str) -> bool:
    for _ in range(3):
        described = await page.evaluate(_DESCRIBE, label)
        if described and (described["inputs"] or described["sliders"] or described["options"]):
            return True
        if not await page.evaluate(_CLICK_HEADER, label):
            return False
        await page.wait_for_timeout(2_200)
    described = await page.evaluate(_DESCRIBE, label)
    return bool(described and described["text"].strip())


async def probe_text_input(page: Any, label: str, index: int, word: str, out: dict[str, Any]) -> None:
    """Type one word, record the suggestions, clear the box. No Enter."""
    focused = await page.evaluate(_FOCUS_NTH, [label, index])
    if not focused:
        return
    await page.keyboard.type(word, delay=30)
    await page.wait_for_timeout(2_000)
    suggestions = await page.evaluate(_SUGGESTIONS)
    out.setdefault("typed", []).append(
        {"input": index, "focused": focused, "word": word, "suggestions": suggestions}
    )
    await page.keyboard.press("Escape")
    await page.evaluate(_FOCUS_NTH, [label, index])
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.wait_for_timeout(600)
    out["values_after_clear"] = await page.evaluate(_INPUT_VALUES, label)


async def probe_number_input(page: Any, label: str, index: int, out: dict[str, Any]) -> None:
    """Type a number, Tab away, read whether the section shows it committed,
    then clear it. Unsaved, so harmless - but it tells the writer whether a
    min/max pair commits on blur or needs Enter."""
    focused = await page.evaluate(_FOCUS_NTH, [label, index])
    if not focused:
        return
    await page.keyboard.type("5", delay=40)
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(1_500)
    out.setdefault("typed", []).append({
        "input": index, "focused": focused, "word": "5",
        "section_text_after_tab": await page.evaluate(_SECTION_TEXT, label),
        "values_after_tab": await page.evaluate(_INPUT_VALUES, label),
    })
    await page.evaluate(_FOCUS_NTH, [label, index])
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(800)
    out["values_after_clear"] = await page.evaluate(_INPUT_VALUES, label)


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(settings.artifact_dir) / "loxo-source-sections" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nRecording to {out_dir}\n")

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    runner = BrowserRunner(settings, headless=not args.headed, slow_mo_ms=0)
    await runner.start()
    findings: dict[str, Any] = {"job": args.job, "sections": {}}
    try:
        async with runner.profile_context("loxo", trace_name="loxo-source-probe") as (
            _context,
            page,
        ):
            url = f"{APP}/agencies/{args.agency}/jobs/{args.job}/source"
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(16_000)
            if any(marker in page.url for marker in LOGGED_OUT):
                if not args.headed:
                    print(f"  Logged out - landed on {page.url}. Run: python -m app.cli login loxo\n")
                    return 2
                # Headed: the person at the keyboard signs in (SSO), and the
                # profile keeps the session. Wait until the app itself renders.
                print(
                    f"\n  Not logged in. Sign in to Loxo in the open browser window.\n"
                    f"  Waiting up to {args.login_timeout // 60} minutes.\n"
                )
                for _ in range(args.login_timeout // 2):
                    await page.wait_for_timeout(2_000)
                    if page.is_closed():
                        print("  Browser closed before sign-in.\n")
                        return 2
                    if any(marker in page.url for marker in LOGGED_OUT):
                        continue
                    text = await page.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    )
                    if "Years of Experience" in text or "Outreach" in text:
                        break
                else:
                    print("  Still logged out - nothing recorded.\n")
                    return 2
                print(f"  Signed in - {page.url}\n")
                if "/source" not in page.url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                await page.wait_for_timeout(16_000)
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            if "Years of Experience" not in body:
                print(f"  The Source panel did not render (url {page.url}). Body starts:\n")
                print("  " + body[:600].replace("\n", "\n  "))
                await page.screenshot(path=str(out_dir / "00-not-rendered.png"))
                return 2
            await page.screenshot(path=str(out_dir / "00-source-screen.png"))

            for label in sections:
                slug = _slug(label)
                print(f"  -- {label}")
                opened = await expand(page, label)
                described = await page.evaluate(_DESCRIBE, label) or {}
                described["opened"] = opened
                try:
                    await page.screenshot(path=str(out_dir / f"{slug}.png"))
                except Exception:
                    pass

                text_inputs = [
                    i for i, inp in enumerate(described.get("inputs", []))
                    if inp["tag"] == "input"
                    and inp["attrs"].get("type", "text") in ("text", "search", "")
                ]
                number_inputs = [
                    i for i, inp in enumerate(described.get("inputs", []))
                    if inp["attrs"].get("type") == "number"
                ]
                if args.type_probes:
                    word = PROBE_WORDS.get(label)
                    if word:
                        for index in text_inputs[:2]:
                            await probe_text_input(page, label, index, word, described)
                    if label == "Years of Experience":
                        for index in number_inputs[:1]:
                            await probe_number_input(page, label, index, described)
                        if not number_inputs and text_inputs:
                            await probe_number_input(page, label, text_inputs[0], described)

                findings["sections"][label] = described
                (out_dir / f"{slug}.json").write_text(
                    json.dumps(described, indent=2), encoding="utf-8"
                )
                print(f"     opened={opened} inputs={len(described.get('inputs', []))} "
                      f"sliders={len(described.get('sliders', []))} "
                      f"options={len(described.get('options', []))}")
                print("     text: " + described.get("text", "").strip().replace("\n", " | ")[:300])
                for typed in described.get("typed", []):
                    if "suggestions" in typed:
                        print(f"     typed {typed['word']!r} -> {typed['suggestions'][:8]}")
                    else:
                        print(f"     typed {typed['word']!r} + Tab -> values {typed['values_after_tab']}")
                # Collapse again so the next section's screenshot is readable.
                await page.evaluate(_CLICK_HEADER, label)
                await page.wait_for_timeout(1_000)

            (out_dir / "findings.json").write_text(
                json.dumps(findings, indent=2), encoding="utf-8"
            )
            print(f"\n  Written to {out_dir}\n")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="Loxo job id.")
    parser.add_argument("--agency", default=AGENCY, help="Loxo agency id.")
    parser.add_argument("--sections", default=",".join(DEFAULT_SECTIONS),
                        help="Comma-separated section labels to expand.")
    parser.add_argument("--headed", action="store_true", help="Show the browser.")
    parser.add_argument("--no-type", dest="type_probes", action="store_false",
                        help="Only read; never type a probe word.")
    parser.add_argument("--login-timeout", type=int, default=900,
                        help="Headed only: seconds to wait for a sign-in.")
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
