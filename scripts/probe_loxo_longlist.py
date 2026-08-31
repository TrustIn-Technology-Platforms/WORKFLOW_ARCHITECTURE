"""Read Loxo's Longlist Agent panel, and write down the calls behind it.

The Skill DNA writer ([loxo_sourcing.py](../app/platforms/loxo_sourcing.py))
sets the criteria that *rank* a longlist. What decides which profiles get into
it at all — the similar titles and the skills — lives on a different surface
this automation has never opened, and Loxo seeds it from the job title alone,
which is why the search started on 2026-08-31 came back with too few titles and
no skills (docs/12-sourcing-criteria.md, gap 2).

A job's GraphQL payload carries `agentJobLinkIds: [13305, 13307, 13306]` and
`defaultExpandedAgentTypeKeys: ["job_description", "shortlist", "longlist"]` —
three agent configurations. Only `job_description` has been mapped. This probe
is how the other two get mapped: it opens a real job, records every GraphQL
operation with its variables and response, dumps the panel each time the screen
changes, and prints back which operations carry title or skill lists.

**It writes nothing.** No field is filled, no button is pressed, nothing is
saved — a person drives the browser and this only watches. That is deliberate:
Loxo's Role Title box is a taxonomy autocomplete that discards free text on
blur, and its own generator discards work that is not accepted in the same
session, so guessing at the surface is worse than looking at it first.

    python scripts/probe_loxo_longlist.py --job 3640874
    python scripts/probe_loxo_longlist.py --job <url> --minutes 30

What to do in the browser once it opens:

    1. Open `Manage` on the job.
    2. Expand the **Longlist Agent** section (and Shortlist, while there).
    3. Open the similar-titles field and the skills field. Type one character
       into each to see whether it autocompletes, then press Escape.
    4. Change nothing else. Close the window when done.

Output lands in `artifacts/loxo-longlist/<timestamp>/`, which is git-ignored.
Keep it that way: Loxo responses carry candidate PII.
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
GRAPHQL = "/graphql"
LOGGED_OUT = ("/sign_in", "/login", "/users/sign_in")

# The words that mark an operation as belonging to the surface being mapped.
# Deliberately wide - a miss here means another live session.
INTERESTING = (
    "agent", "longlist", "shortlist", "title", "skill", "taxonomy",
    "criteria", "sourcing", "search",
)

# Keys whose value is a list of titles or skills. Finding one of these inside a
# mutation's variables is the answer the probe exists to get.
TARGET_KEYS = re.compile(
    r"(similar|related|alternate)?_?titles?$|^skills?$|_skills?$|keywords?$",
    re.IGNORECASE,
)

SECRET_KEYS = {
    "token", "api_key", "apikey", "access_token", "refresh_token",
    "password", "secret", "authorization", "id_token", "csrf",
}

PROBE_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    role: el.getAttribute('role') || '',
    testid: el.getAttribute('data-testid') || '',
    name: el.getAttribute('name') || '',
    placeholder: el.getAttribute('placeholder') || '',
    aria: el.getAttribute('aria-label') || '',
    value: String(el.value || '').slice(0, 200),
    text: (el.innerText || '').trim().slice(0, 160),
    cls: (el.getAttribute('class') || '').slice(0, 200),
  });
  const out = {url: location.href, title: document.title};
  out.bodyText = (document.body.innerText || '').slice(0, 40000);
  out.controls = [...document.querySelectorAll('button, [role=button], [role=switch], [role=tab], summary')]
    .filter(visible).slice(0, 250).map(describe);
  out.inputs = [...document.querySelectorAll('input:not([type=hidden]), textarea, select')]
    .filter(visible).slice(0, 150).map(describe);
  out.editors = [...document.querySelectorAll('[contenteditable=true]')]
    .filter(visible).slice(0, 40).map(describe);
  // Whatever renders a removable tag - the shape a titles or skills list takes.
  out.chips = [...document.querySelectorAll('[class*=hip], [class*=Tag], [class*=tag], [class*=Pill]')]
    .filter(visible).slice(0, 200)
    .map(el => ({cls: (el.getAttribute('class') || '').slice(0, 120),
                 text: (el.innerText || '').trim().slice(0, 80)}))
    .filter(c => c.text);
  return out;
}
"""


def redact(value: Any, *, cap: int = 4000) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in SECRET_KEYS else redact(item, cap=cap))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, cap=cap) for item in value]
    if isinstance(value, str) and len(value) > cap:
        return value[:cap] + f"… (+{len(value) - cap} chars)"
    return value


def find_lists(value: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every `titles`/`skills`-shaped key in a payload, with where it was found.

    This is the whole point of the run: the operation whose variables carry one
    of these is the mutation that writes the Longlist Agent's filters.
    """
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else key
            if TARGET_KEYS.search(key):
                found.append((here, item))
            found.extend(find_lists(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            found.extend(find_lists(item, f"{path}[{index}]"))
    return found


class Recorder:
    """Every GraphQL operation the app makes, and a dump per screen."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []
        self.screens = 0

    def watch(self, page: Any) -> None:
        async def on_response(response: Any) -> None:
            if GRAPHQL not in response.url:
                return
            request = response.request
            body = request.post_data
            payload: Any = None
            if body:
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = body[:2000]

            operations = payload if isinstance(payload, list) else [payload]
            names = [
                str(op.get("operationName") or "?")
                for op in operations
                if isinstance(op, dict)
            ]
            name = ", ".join(names) or "?"

            entry: dict[str, Any] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "operation": name,
                "status": response.status,
                "method": request.method,
                "is_mutation": any(
                    "mutation" in str(op.get("query", "")).lower()[:40]
                    for op in operations
                    if isinstance(op, dict)
                ),
                "request": redact(payload),
            }
            try:
                entry["response"] = redact(await response.json(), cap=2000)
            except Exception:
                entry["response"] = "<unreadable>"

            # A mutation carrying a titles or skills list is the find.
            hits = find_lists(entry["request"])
            if hits:
                entry["title_or_skill_keys"] = [
                    {"at": where, "value": redact(value, cap=600)} for where, value in hits
                ]

            self.calls.append(entry)
            mark = "MUTATION" if entry["is_mutation"] else "query"
            note = f"  <- {len(hits)} title/skill key(s)" if hits else ""
            if any(word in name.lower() for word in INTERESTING) or hits or entry["is_mutation"]:
                print(f"    -> [{mark}] {name} ({response.status}){note}")
            self.save()

        page.on("response", on_response)

    async def dump(self, page: Any, label: str) -> None:
        self.screens += 1
        stem = f"{self.screens:02d}-{label}"
        try:
            data = await page.evaluate(PROBE_JS)
        except Exception:
            return
        (self.out / f"{stem}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        for writer in (
            lambda: page.screenshot(path=str(self.out / f"{stem}.png"), full_page=True),
            lambda: self._write_html(page, stem),
        ):
            try:
                await writer()
            except Exception:
                pass
        print(f"  [screen {self.screens}] {label}")

    async def _write_html(self, page: Any, stem: str) -> None:
        (self.out / f"{stem}.html").write_text(await page.content(), encoding="utf-8")

    def save(self) -> None:
        (self.out / "graphql.json").write_text(
            json.dumps(self.calls, indent=2, default=str), encoding="utf-8"
        )

    def summarise(self) -> None:
        """What was learned, in the order the next change needs it."""
        print("\n  ---- what this run found ----\n")
        if not self.calls:
            print("    Nothing. No GraphQL traffic was seen - was the job opened?\n")
            return

        seen: dict[str, int] = {}
        for call in self.calls:
            seen[call["operation"]] = seen.get(call["operation"], 0) + 1
        print("  Operations, by name:")
        for name, count in sorted(seen.items(), key=lambda kv: -kv[1]):
            relevant = any(word in name.lower() for word in INTERESTING)
            print(f"    {'*' if relevant else ' '} {name} x{count}")

        mutations = [c for c in self.calls if c["is_mutation"]]
        print(f"\n  Mutations seen: {len(mutations)}")
        for call in mutations:
            print(f"    - {call['operation']} ({call['status']})")

        carriers = [c for c in self.calls if c.get("title_or_skill_keys")]
        print(f"\n  Calls carrying a titles/skills list: {len(carriers)}")
        for call in carriers:
            kind = "MUTATION" if call["is_mutation"] else "query"
            print(f"    - [{kind}] {call['operation']}")
            for hit in call["title_or_skill_keys"][:8]:
                value = hit["value"]
                shown = value if not isinstance(value, list) else f"{len(value)} item(s)"
                print(f"        {hit['at']} = {shown}")

        agents = [
            c for c in self.calls
            if "agentJobLink" in json.dumps(c.get("response", ""))[:200_000]
        ]
        if agents:
            print(f"\n  Responses mentioning agentJobLinkIds: {len(agents)}")
            print("    Read graphql.json for the ids and their agent type keys.")

        if not carriers:
            print(
                "\n    No titles or skills list was seen. Either the panel was not\n"
                "    expanded, or Loxo loads it from a REST endpoint rather than\n"
                "    GraphQL - check the dumped screens for the field names.\n"
            )
        print(
            "\n  Next: record the mutation name, its variables and the field\n"
            "  selectors in docs/platforms/loxo.md, then write the writer.\n"
        )


async def wait_for_login(page: Any, seconds: int) -> bool:
    print(
        f"\n  Not logged in. Sign in to Loxo in the open browser window.\n"
        f"  Waiting up to {seconds // 60} minutes.\n"
    )
    for _ in range(seconds):
        if page.is_closed():
            return False
        await page.wait_for_timeout(1000)
        if not any(marker in page.url for marker in LOGGED_OUT):
            await page.wait_for_timeout(4000)
            print(f"  Signed in - {page.url}\n")
            return True
    return False


async def heading(page: Any) -> str:
    try:
        text = await page.evaluate(
            "() => (document.body.innerText || '').split('\\n')"
            ".map(s => s.trim()).filter(Boolean).slice(0, 14).join(' | ')"
        )
    except Exception:
        return ""
    return str(text)[:180]


def job_id_of(value: str) -> str:
    digits = re.findall(r"\d{4,}", value or "")
    return digits[-1] if digits else ""


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    job_id = job_id_of(args.job)
    if not job_id:
        print(f"\n  {args.job!r} holds no Loxo job id. Pass the id or the job URL.\n")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    recorder = Recorder(Path(settings.artifact_dir) / "loxo-longlist" / stamp)
    print(f"\nRecording to {recorder.out}\n")

    runner = BrowserRunner(settings, headless=False, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("loxo", trace_name="loxo-longlist-probe") as (
            _context,
            page,
        ):
            recorder.watch(page)
            url = f"{APP}/agencies/{args.agency}/jobs/{job_id}/overview"
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            # Loxo renders nothing for 10-20s on a cold load. Not a failure.
            await page.wait_for_timeout(18_000)

            if any(marker in page.url for marker in LOGGED_OUT):
                if not await wait_for_login(page, args.login_timeout):
                    print("\n  Still logged out - nothing recorded.\n")
                    return 2
                await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                await page.wait_for_timeout(18_000)

            await recorder.dump(page, "job-overview")

            print(
                "\n  Now, by hand, in the browser:\n"
                "    1. Click `Manage`.\n"
                "    2. Expand `Longlist Agent` (and `Shortlist Agent`).\n"
                "    3. Open the similar-titles field and the skills field. Type\n"
                "       one character in each to see whether it autocompletes,\n"
                "       then press Escape.\n"
                "    4. Change nothing. Save nothing.\n\n"
                "  Every GraphQL call is being recorded. Close the window when\n"
                f"  done, or leave it and this stops after {args.minutes} minutes.\n"
            )

            last = await heading(page)
            waited, deadline = 0, args.minutes * 60
            try:
                while waited < deadline and not page.is_closed():
                    await page.wait_for_timeout(2500)
                    waited += 2.5
                    current = await heading(page)
                    if current and current != last:
                        last = current
                        await recorder.dump(page, "screen")
            except KeyboardInterrupt:
                print("\n  Stopped.")
            except Exception as exc:  # the window being closed mid-poll
                print(f"\n  Browser closed ({type(exc).__name__}).")

            recorder.save()
            recorder.summarise()
            print(f"  Written to {recorder.out}\n")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="Loxo job id or job URL.")
    parser.add_argument("--agency", default=AGENCY, help="Loxo agency id.")
    parser.add_argument("--minutes", type=int, default=25, help="How long to record.")
    parser.add_argument(
        "--login-timeout", type=int, default=420, help="Seconds to wait for a sign-in."
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
