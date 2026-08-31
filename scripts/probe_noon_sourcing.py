"""Watch noon's sourcing wizard being driven by hand, and write down every call.

`app/platforms/noon_sourcing.py` replays that wizard as API calls. Its payloads
were read out of noon's public portal bundle rather than watched happening, so
one thing is still missing: proof that a real run makes exactly those calls, in
that order, with those fields. This is how that proof gets taken.

It opens the portal with the saved profile — waiting, if the session has
expired, for a person to complete the Microsoft sign-in, which nothing here can
do — and then stays out of the way. **You** click `Start sourcing` and go
through the wizard as usual. Everything noon's front end sends is recorded with
its payload, every screen is dumped as text, DOM and a screenshot, and at the
end the observed call sequence is compared against the one the driver sends.

    python scripts/probe_noon_sourcing.py                      # start at the role list
    python scripts/probe_noon_sourcing.py --role <uuid|url>    # straight to a role

Use a throwaway role: the wizard saves as it goes, and its last step sets the
agent searching.

Output lands in `artifacts/noon-sourcing/<timestamp>/`, which is git-ignored —
keep it that way. noon's responses carry candidate PII, and `get_company_info`
returns the Loxo API key in clear.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402
from app.platforms.browser import BrowserRunner  # noqa: E402
from app.sessions.store import SessionStore  # noqa: E402

PORTAL = "https://www.noon.ai/portal/sourcing"
LOGGED_OUT = "/log-in"
API = "https://noon.fly.dev"

# What app/platforms/noon_sourcing.py sends, in order. The point of the run is
# to find out whether the real wizard agrees.
EXPECTED = [
    "generate_params",
    "set_candidate_source",
    "setup_clarifying_questions",
    "gpt_stream",
    "role_autopilot",
    "rank_non_negotiables",
    "role_autopilot",
    "clarifying_questions",
    "mark_clarifying_question",
    "role_autopilot",
]

# Redacted before anything reaches disk: this file gets read later, elsewhere.
SECRET_KEYS = {
    "token", "api_key", "apikey", "access_token", "refresh_token",
    "password", "secret", "authorization", "id_token",
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
    placeholder: el.getAttribute('placeholder') || '',
    aria: el.getAttribute('aria-label') || '',
    checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : null,
    ariaChecked: el.getAttribute('aria-checked'),
    value: String(el.value || '').slice(0, 200),
    text: (el.innerText || '').trim().slice(0, 160),
    cls: (el.getAttribute('class') || '').slice(0, 160),
  });
  const groups = {
    controls: 'button, [role=button], [role=switch], [role=checkbox], [role=tab]',
    inputs: 'input:not([type=hidden]), textarea, select',
    editors: '[contenteditable=true]',
  };
  const out = {url: location.href, title: document.title};
  out.bodyText = (document.body.innerText || '').slice(0, 40000);
  for (const [key, selector] of Object.entries(groups)) {
    out[key] = [...document.querySelectorAll(selector)].filter(visible).slice(0, 150).map(describe);
  }
  return out;
}
"""


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in SECRET_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + f"… (+{len(value) - 4000} chars)"
    return value


class Recorder:
    """Every call noon's front end makes, and a dump per screen."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []
        self.screens = 0

    def watch(self, page: Any) -> None:
        async def on_response(response: Any) -> None:
            if API not in response.url:
                return
            route = response.url.rsplit("/", 1)[-1].split("?")[0]
            entry: dict[str, Any] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "route": route,
                "status": response.status,
            }
            body = response.request.post_data
            if body:
                try:
                    entry["request"] = redact(json.loads(body))
                except ValueError:
                    entry["request"] = body[:2000]
            try:
                entry["response"] = redact(await response.json())
            except Exception:
                try:
                    entry["response"] = redact((await response.text())[:2000])
                except Exception:
                    entry["response"] = "<unreadable>"
            self.calls.append(entry)
            print(f"    -> {route} ({response.status})")
            self.save()

        page.on("response", on_response)

    async def dump(self, page: Any, label: str) -> None:
        self.screens += 1
        stem = f"{self.screens:02d}-{label}"
        try:
            data = await page.evaluate(PROBE_JS)
        except Exception:
            return
        (self.out / f"{stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            await page.screenshot(path=str(self.out / f"{stem}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.out / f"{stem}.html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            pass
        print(f"  [screen {self.screens}] {label}")

    def save(self) -> None:
        (self.out / "api.json").write_text(
            json.dumps(self.calls, indent=2, default=str), encoding="utf-8"
        )

    def compare(self) -> None:
        """Say whether the driver's sequence matches what actually happened."""
        seen = [call["route"] for call in self.calls if call["route"] in set(EXPECTED)]
        print("\n  Calls the driver sends, against what this run made:\n")
        for route in EXPECTED:
            mark = "ok  " if route in seen else "MISS"
            print(f"    [{mark}] {route}")
        extra = [
            route
            for route in dict.fromkeys(call["route"] for call in self.calls)
            if route not in set(EXPECTED)
        ]
        if extra:
            print("\n  Also called, which the driver does not send:")
            for route in extra:
                print(f"    - {route}")
        print(f"\n  Full sequence: {' -> '.join(call['route'] for call in self.calls)}")


async def dismiss_banners(page: Any) -> None:
    for text in ("Accept all", "Dismiss"):
        try:
            element = page.locator(f"text='{text}'").first
            if await element.is_visible(timeout=1200):
                await element.click(timeout=2500)
                await page.wait_for_timeout(400)
        except Exception:
            pass


async def wait_for_login(page: Any, seconds: int) -> bool:
    """noon signs in through Microsoft SSO, so a person has to do this part."""
    print(
        f"\n  Not logged in. Sign in to noon in the open browser window.\n"
        f"  Waiting up to {seconds // 60} minutes, then carrying on by itself.\n"
    )
    for _ in range(seconds):
        if page.is_closed():
            return False
        await page.wait_for_timeout(1000)
        if LOGGED_OUT not in page.url:
            await page.wait_for_timeout(4000)
            print(f"  Signed in - {page.url}\n")
            return True
    return False


async def heading(page: Any) -> str:
    """A rough name for the screen on show, to notice when it changes."""
    try:
        text = await page.evaluate(
            "() => (document.body.innerText || '').split('\\n')"
            ".map(s => s.trim()).filter(Boolean).slice(0, 12).join(' | ')"
        )
    except Exception:
        return ""
    return str(text)[:160]


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    recorder = Recorder(Path(settings.artifact_dir) / "noon-sourcing" / stamp)
    print(f"\nRecording to {recorder.out}\n")

    runner = BrowserRunner(settings, headless=False, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("noon", trace_name="noon-sourcing-probe") as (
            context,
            page,
        ):
            recorder.watch(page)
            target = PORTAL
            if args.role:
                uuid = args.role.rsplit("role=", 1)[-1]
                target = f"{PORTAL}?role={uuid}"
            await page.goto(target, wait_until="domcontentloaded")
            await page.wait_for_timeout(7000)

            if LOGGED_OUT in page.url:
                if not await wait_for_login(page, args.login_timeout):
                    print("\n  Still logged out - nothing recorded.\n")
                    return 2
                sessions = SessionStore(settings)
                sessions.save_state(
                    "noon", await context.storage_state(), "noon.storage_state.json"
                )
                sessions.mark_profile_verified("noon")
                await page.goto(target, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

            await dismiss_banners(page)
            await recorder.dump(page, "start")

            print(
                "\n  Go through the sourcing wizard by hand now:\n"
                "    Start sourcing -> paste the JD -> Submit -> pick the pool ->\n"
                "    confirm the criteria -> select non-negotiables -> rank ->\n"
                "    answer the clarifying questions.\n\n"
                "  Every call is being recorded. Close the browser when you are\n"
                f"  done, or leave it and this stops after {args.minutes} minutes.\n"
            )

            last = await heading(page)
            deadline = args.minutes * 60
            waited = 0
            try:
                while waited < deadline and not page.is_closed():
                    await page.wait_for_timeout(2000)
                    waited += 2
                    current = await heading(page)
                    if current and current != last:
                        last = current
                        await recorder.dump(page, "screen")
            except KeyboardInterrupt:
                print("\n  Stopped.")
            except Exception as exc:  # the window being closed mid-poll
                print(f"\n  Browser closed ({type(exc).__name__}).")

            recorder.save()
            recorder.compare()
            print(f"\n  Written to {recorder.out}\n")
            return 0
    finally:
        await runner.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", help="Role uuid or URL to open. Defaults to the role list.")
    parser.add_argument("--minutes", type=int, default=20, help="How long to keep recording.")
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
