"""Map Juicebox's redesigned sequence editor (2026-10-08) against the live app.

    python scripts/probe_juicebox_editor.py                    # read-only: modal, project picker, API traffic
    python scripts/probe_juicebox_editor.py --create           # + one ZZ TEST sequence, filled and read back
    python scripts/probe_juicebox_editor.py --create --archive # + archive it again (only if it reads ZZ TEST)
    python scripts/probe_juicebox_editor.py --archive-id <id>  # archive one earlier probe sequence

Juicebox replaced its sequence editor on 2026-10-08: "Build from scratch"
creates the sequence at once inside a project, and the step editors are
Tiptap/ProseMirror instead of TinyMCE (docs/platforms/juicebox.md, "The
redesigned sequence editor"). The driver was paused at the modal. This records
what remapping it needs, and nothing more:

- every `/api/` call the app makes, request and response, so the create and
  save contract can be read off the wire (the app's own `POST /api/sequence`
  created the 2026-09-23 delete-proof sequence);
- the modal, the project picker, and the editor DOM, with each contenteditable
  asked for its Tiptap instance (`element.editor`), extensions and schema;
- with `--create`, one sequence: renamed to ZZ TEST at once, subject and body
  written through Tiptap, the token chip's node recorded, a second step added,
  then read back through the API.

Headed by default: it runs on the person's own machine, in the automation's
own profile (`.profiles/juicebox`), the session that was pushed to Railway.
Nothing is sent - a sequence with no contacts emails nobody. A sequence is
archived only when its title reads back starting "ZZ TEST".

Everything lands in artifacts/juicebox-editor-probe/<stamp>/: network.jsonl,
numbered .html/.png per stage, and findings.json.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.platforms.browser import BrowserRunner  # noqa: E402

APP = "https://app.juicebox.ai"
TITLE_PREFIX = "ZZ TEST - Juicebox editor probe"

# Every contenteditable, with what Tiptap exposes on it. Tiptap stores its
# Editor instance on the editable element (`dom.editor`), which is the handle a
# driver can fill through instead of synthesising keystrokes.
_EDITABLES = r"""
() => [...document.querySelectorAll('[contenteditable="true"]')].map((el, i) => {
  const data = {};
  for (const a of el.attributes) if (a.name.startsWith('data-') || a.name.startsWith('aria-')) data[a.name] = a.value;
  const info = {i, tag: el.tagName, attrs: data, cls: (el.className || '').toString().slice(0, 160),
                text: (el.innerText || '').slice(0, 300), html: el.innerHTML.slice(0, 1200), hasEditor: !!el.editor};
  const ed = el.editor;
  if (ed) {
    try { info.extensions = ed.extensionManager.extensions.map(e => e.name); } catch (e) { info.extErr = String(e); }
    try { info.nodes = Object.keys(ed.schema.nodes); info.marks = Object.keys(ed.schema.marks); } catch (e) {}
    try { info.json = ed.getJSON(); } catch (e) {}
    try { info.edHtml = ed.getHTML(); } catch (e) {}
    try { info.editable = ed.isEditable; } catch (e) {}
  }
  return info;
})
"""

# Clickable things, labelled, so the next stage can be written against names.
_CONTROLS = r"""
() => {
  const seen = [];
  for (const el of document.querySelectorAll('button, [role=button], [role=menuitem], [role=option], [role=tab], a[aria-label], input, textarea, [role=combobox]')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    seen.push({tag: el.tagName, role: el.getAttribute('role'), aria: el.getAttribute('aria-label'),
               testid: el.getAttribute('data-testid'), name: el.getAttribute('name'),
               placeholder: el.getAttribute('placeholder'), type: el.getAttribute('type'),
               value: el.value ? String(el.value).slice(0, 120) : undefined,
               text: (el.innerText || '').trim().slice(0, 80)});
  }
  return seen;
}
"""

_DIALOGS = r"""
() => [...document.querySelectorAll('[role=dialog], [role=menu], [role=listbox], [data-slot=dialog-content], [data-slot=popover-content]')]
  .map(el => ({role: el.getAttribute('role'), slot: el.getAttribute('data-slot'),
               aria: el.getAttribute('aria-label'), text: (el.innerText || '').slice(0, 1500)}))
"""

_FETCH = r"""
async ([url, method, token, body]) => {
  const r = await fetch(url, {method, headers: {"Content-Type": "application/json", "fbauthorization": token},
                              body: body === null ? undefined : JSON.stringify(body)});
  const text = await r.text();
  let data = null; try { data = JSON.parse(text); } catch (e) { data = text.slice(0, 2000); }
  return {status: r.status, data};
}
"""


class Probe:
    def __init__(self, page: Any, out: Path) -> None:
        self.page = page
        self.out = out
        self.n = 0
        self.token: str | None = None
        self.findings: dict[str, Any] = {"stages": {}, "errors": []}
        self.calls: list[dict[str, Any]] = []
        self._net = (out / "network.jsonl").open("a", encoding="utf-8")

    # -- network -----------------------------------------------------------

    def attach(self) -> None:
        self.page.on("request", self._on_request)
        self.page.on("response", lambda r: asyncio.ensure_future(self._on_response(r)))

    def _on_request(self, request: Any) -> None:
        if "/api/" not in request.url:
            return
        try:
            token = request.headers.get("fbauthorization")
        except Exception:
            token = None
        if token and not self.token:
            self.token = token

    async def _on_response(self, response: Any) -> None:
        request = response.request
        if "/api/" not in request.url:
            return
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "stage": self.n,
            "method": request.method,
            "url": request.url,
            "status": response.status,
        }
        try:
            body = request.post_data
            if body:
                entry["request"] = body[:20000]
        except Exception:
            pass
        # Reads of the list are big and known; everything else is the contract.
        if request.method != "GET" or "sequence" in request.url:
            try:
                entry["response"] = (await response.text())[:20000]
            except Exception:
                pass
        self.calls.append(entry)
        self._net.write(json.dumps(entry) + "\n")
        self._net.flush()

    async def api(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        if not self.token:
            return {"status": 0, "data": "no token captured yet"}
        return await self.page.evaluate(_FETCH, [f"{APP}{path}", method, self.token, body])

    # -- recording -----------------------------------------------------------

    async def snap(self, stage: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        self.n += 1
        tag = f"{self.n:02d}-{stage}"
        record: dict[str, Any] = {"url": self.page.url}
        try:
            (self.out / f"{tag}.html").write_text(await self.page.content(), encoding="utf-8")
        except Exception as exc:
            record["htmlErr"] = str(exc)[:200]
        try:
            await self.page.screenshot(path=str(self.out / f"{tag}.png"), full_page=False)
        except Exception as exc:
            record["pngErr"] = str(exc)[:200]
        for key, js in (("editables", _EDITABLES), ("controls", _CONTROLS), ("dialogs", _DIALOGS)):
            try:
                record[key] = await self.page.evaluate(js)
            except Exception as exc:
                record[f"{key}Err"] = str(exc)[:200]
        if extra:
            record.update(extra)
        self.findings["stages"][tag] = record
        self.save()
        print(f"  [{tag}] {self.page.url}")
        return record

    def error(self, stage: str, exc: Exception) -> None:
        self.findings["errors"].append({"stage": stage, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"})
        self.save()
        print(f"  ! {stage}: {str(exc).splitlines()[0][:160] if str(exc) else exc.__class__.__name__}")

    def save(self) -> None:
        (self.out / "findings.json").write_text(
            json.dumps(self.findings, indent=2, default=str), encoding="utf-8"
        )

    def close(self) -> None:
        self._net.close()


async def wait_for(page: Any, js: str, seconds: int) -> bool:
    for _ in range(seconds):
        try:
            if await page.evaluate(js):
                return True
        except Exception:
            pass
        await page.wait_for_timeout(1_000)
    return False


async def click(page: Any, *locators: Any, timeout: int = 8_000) -> bool:
    for locator in locators:
        try:
            await locator.first.wait_for(state="visible", timeout=timeout)
            await locator.first.click(timeout=timeout, no_wait_after=True, force=True)
            return True
        except Exception:
            continue
    return False


async def open_new_sequence_modal(probe: Probe) -> bool:
    page = probe.page
    await page.goto(f"{APP}/", wait_until="commit", timeout=60_000)
    if not await wait_for(page, "!!document.querySelector(\"a[aria-label='Sequences']\") || document.body.innerText.includes('Sequences')", 60):
        probe.error("load", RuntimeError("the signed-in shell never appeared - run: python -m app.cli login juicebox"))
        return False
    await page.wait_for_timeout(3_000)
    await click(page, page.locator("a[aria-label='Sequences']"), page.get_by_text("Sequences", exact=True))
    await wait_for(page, "document.body.innerText.includes('New sequence')", 40)
    await page.wait_for_timeout(2_000)
    await probe.snap("sequence-list")
    if not await click(page, page.get_by_role("button", name="New sequence", exact=True),
                       page.get_by_text("New sequence", exact=True)):
        probe.error("new-sequence", RuntimeError("no 'New sequence' control"))
        return False
    await wait_for(page, "document.body.innerText.includes('from scratch')", 20)
    await page.wait_for_timeout(1_500)
    await probe.snap("new-sequence-modal")
    return True


async def read_only(probe: Probe) -> None:
    page = probe.page
    if not await open_new_sequence_modal(probe):
        return
    # The project picker: opened, recorded, closed. Choosing a project is what
    # starts AI generation, so nothing in it is clicked.
    if await click(page, page.get_by_text("Choose project", exact=True), timeout=5_000):
        await page.wait_for_timeout(2_000)
        await probe.snap("choose-project-open")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    if await click(page, page.get_by_text("Clone existing sequence", exact=True), timeout=5_000):
        await page.wait_for_timeout(2_000)
        await probe.snap("clone-open")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    await probe.snap("modal-after-pickers")
    await click(page, page.get_by_role("button", name="Close", exact=True), timeout=4_000)

    # Read-only API context: ZZ TEST leftovers, and the project list the modal draws from.
    sequences = await probe.api("GET", "/api/sequence/list")
    projects = await probe.api("GET", "/api/projects")
    items = sequences.get("data", {}).get("result", []) if isinstance(sequences.get("data"), dict) else []
    probe.findings["sequence_list"] = {
        "status": sequences.get("status"), "count": len(items),
        "zz_test": [s for s in items if str(s.get("title", "")).startswith("ZZ TEST")],
        "sample_keys": sorted(items[0].keys()) if items else [],
        "newest": sorted(items, key=lambda s: str(s.get("createdAt") or s.get("dateAdded") or ""), reverse=True)[:5],
        "titled_like_build_from_scratch": [s for s in items if re.search(r" - \d{2}/\d{2}/\d{4}$", str(s.get("title", "")))],
    }
    probe.findings["projects"] = {"status": projects.get("status"),
                                  "keys": sorted(projects["data"]["result"].keys()) if isinstance(projects.get("data"), dict) and isinstance(projects["data"].get("result"), dict) else None}
    if isinstance(projects.get("data"), dict) and isinstance(projects["data"].get("result"), dict):
        flat = [p for group in projects["data"]["result"].values() if isinstance(group, list) for p in group]
        probe.findings["projects"]["zz_test"] = [p for p in flat if "ZZ TEST" in str(p.get("title", ""))]
        probe.findings["projects"]["count"] = len(flat)
    probe.save()


async def create_and_fill(probe: Probe, stamp: str) -> str | None:
    """Build from scratch, rename to ZZ TEST at once, fill through Tiptap, add a step."""
    page = probe.page
    if not await open_new_sequence_modal(probe):
        return None
    before = len(probe.calls)
    if not await click(page, page.get_by_role("button", name="Build from scratch", exact=True),
                       page.get_by_text("Build from scratch", exact=True)):
        probe.error("build-from-scratch", RuntimeError("no 'Build from scratch' control"))
        return None
    ready = await wait_for(page, "!!document.querySelector('[data-step-subject-input]') || !!document.querySelector('[aria-label=\"Message body\"]')", 90)
    await page.wait_for_timeout(3_000)
    created = [c for c in probe.calls[before:] if c["method"] in ("POST", "PUT", "PATCH")]
    sequence_id = None
    for call in created:
        found = re.search(r'"(?:id|sequenceId)"\s*:\s*"([A-Za-z0-9]{12,})"', call.get("response", "") or "")
        if found:
            sequence_id = found.group(1)
            break
    if not sequence_id:
        found = re.search(r"(?:createdSequenceId|sequenceId)=([A-Za-z0-9]{12,})|/sequences/([A-Za-z0-9]{12,})", page.url)
        if found:
            sequence_id = found.group(1) or found.group(2)
    probe.findings["created"] = {"editor_ready": ready, "sequence_id": sequence_id, "create_calls": created}
    await probe.snap("editor-open")
    print(f"  created sequence id: {sequence_id}")

    title = f"{TITLE_PREFIX} {stamp}"
    await rename(probe, title)

    # Subject: through Tiptap, the way a driver would.
    subject_js = """(t) => { const el = document.querySelector('[data-step-subject-input]');
        if (!el || !el.editor) return {ok: false, why: el ? 'no editor on element' : 'no subject element'};
        el.editor.commands.setContent(t); el.dispatchEvent(new Event('input', {bubbles: true}));
        return {ok: true, text: el.editor.getText(), json: el.editor.getJSON()}; }"""
    probe.findings["subject_fill"] = await safe_eval(page, subject_js, "ZZ TEST subject for {{First Name}} at {{Current Company}}")
    await page.wait_for_timeout(2_500)

    body_html = ("<p>Hi {{First Name}},</p><p></p><p>ZZ TEST body paragraph one, written by the "
                 "probe through Tiptap. Nothing here is sent.</p><p></p><p>Best,<br>Probe</p>")
    body_js = """(h) => { const el = document.querySelector('[aria-label="Message body"]');
        if (!el || !el.editor) return {ok: false, why: el ? 'no editor on element' : 'no body element'};
        el.editor.commands.setContent(h, true); el.dispatchEvent(new Event('input', {bubbles: true}));
        return {ok: true, html: el.editor.getHTML(), json: el.editor.getJSON()}; }"""
    probe.findings["body_fill"] = await safe_eval(page, body_js, body_html)
    await page.wait_for_timeout(3_000)
    await probe.snap("step1-filled")

    # What the app's own token chip inserts: the node a driver must reproduce.
    chip_before = len(probe.calls)
    focus_end = """() => { const el = document.querySelector('[aria-label="Message body"]');
        if (el && el.editor) { el.editor.commands.focus('end'); return true; } return false; }"""
    await safe_eval(page, focus_end)
    if await click(page, page.get_by_text("{First Name}", exact=True), timeout=4_000):
        await page.wait_for_timeout(2_000)
        read_body = """() => { const el = document.querySelector('[aria-label="Message body"]');
            return el && el.editor ? {html: el.editor.getHTML(), json: el.editor.getJSON()} : null; }"""
        probe.findings["after_token_chip"] = await safe_eval(page, read_body)
        probe.findings["token_chip_calls"] = probe.calls[chip_before:]
    await page.wait_for_timeout(3_000)
    await probe.snap("after-token-chip")

    # A second step: the left rail's Add step, then whatever it offers.
    add_before = len(probe.calls)
    if await click(page, page.locator("[aria-label='Add step']"), page.get_by_role("button", name="Add step")):
        await page.wait_for_timeout(2_500)
        await probe.snap("add-step-clicked")
        if await click(page, page.get_by_role("menuitem", name="Email"), page.get_by_role("option", name="Email"),
                       page.get_by_text("Email", exact=True), timeout=3_000):
            await page.wait_for_timeout(3_000)
        await probe.snap("step2-open")
        probe.findings["step2_body_fill"] = await safe_eval(page, """(h) => {
            const els = [...document.querySelectorAll('[aria-label="Message body"]')];
            const el = els[els.length - 1];
            if (!el || !el.editor) return {ok: false, count: els.length};
            el.editor.commands.setContent(h, true);
            return {ok: true, count: els.length, html: el.editor.getHTML()}; }""",
            "<p>Hi {{First Name}},</p><p></p><p>ZZ TEST step two. Nothing here is sent.</p>")
        await page.wait_for_timeout(3_000)
        probe.findings["add_step_calls"] = probe.calls[add_before:]
        await probe.snap("step2-filled")

    # Done, then read the sequence back through the API.
    done_before = len(probe.calls)
    if await click(page, page.get_by_role("button", name="Done", exact=True), timeout=5_000):
        await page.wait_for_timeout(4_000)
        probe.findings["done_calls"] = probe.calls[done_before:]
        await probe.snap("after-done")
    if sequence_id:
        for path in (f"/api/sequence?sequenceId={sequence_id}", f"/api/sequence/{sequence_id}",
                     f"/api/sequence/steps?sequenceId={sequence_id}"):
            probe.findings.setdefault("read_back", {})[path] = await probe.api("GET", path)
        listed = await probe.api("GET", "/api/sequence/list")
        items = listed.get("data", {}).get("result", []) if isinstance(listed.get("data"), dict) else []
        probe.findings["listed"] = [s for s in items if s.get("id") == sequence_id]
    probe.save()
    return sequence_id


async def rename(probe: Probe, title: str) -> None:
    """The auto-title is '<project> - <date>'; find it, make it editable, replace it."""
    page = probe.page
    found = await safe_eval(page, r"""() => {
        const re = / - \d{2}\/\d{2}\/\d{4}$/;
        const cands = [...document.querySelectorAll('[role=dialog] *, body *')].filter(el =>
            el.children.length === 0 && re.test((el.innerText || el.value || '').trim()));
        const inputs = [...document.querySelectorAll('input, [contenteditable="true"]')].filter(el =>
            re.test((el.value || el.innerText || '').trim()));
        return {text: cands.slice(0, 3).map(el => ({tag: el.tagName, text: el.innerText, cls: (el.className||'').toString().slice(0,120),
                                                  aria: el.getAttribute('aria-label')})),
                inputs: inputs.slice(0, 3).map(el => ({tag: el.tagName, value: el.value || el.innerText,
                                                      aria: el.getAttribute('aria-label'), name: el.getAttribute('name')}))};
    }""")
    probe.findings["title_candidates"] = found
    target = page.locator("input").filter(has_text="").and_(page.locator("input[value$='/2026']"))
    renamed = False
    try:
        if await target.count():
            await target.first.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(title, delay=15)
            await page.keyboard.press("Enter")
            renamed = True
    except Exception as exc:
        probe.error("rename-input", exc)
    if not renamed:
        try:
            heading = page.get_by_text(re.compile(r" - \d{2}/\d{2}/\d{4}$")).first
            await heading.click(timeout=4_000)
            await page.wait_for_timeout(800)
            await page.keyboard.press("Control+A")
            await page.keyboard.type(title, delay=15)
            await page.keyboard.press("Enter")
            renamed = True
        except Exception as exc:
            probe.error("rename-heading", exc)
    await page.wait_for_timeout(2_000)
    probe.findings["rename"] = {"tried": renamed, "title": title}
    await probe.snap("after-rename")


async def safe_eval(page: Any, js: str, arg: Any = None) -> Any:
    try:
        return await page.evaluate(js, arg) if arg is not None else await page.evaluate(js)
    except Exception as exc:
        return {"error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}


async def archive(probe: Probe, sequence_id: str) -> None:
    """Archive one sequence, only when its title reads back as a ZZ TEST."""
    listed = await probe.api("GET", "/api/sequence/list")
    items = listed.get("data", {}).get("result", []) if isinstance(listed.get("data"), dict) else []
    match = [s for s in items if s.get("id") == sequence_id]
    title = str(match[0].get("title", "")) if match else ""
    probe.findings["archive"] = {"id": sequence_id, "title": title}
    if not title.startswith("ZZ TEST"):
        print(f"  NOT archived: {sequence_id} reads {title!r}, not a ZZ TEST title. Delete it by hand.")
        probe.save()
        return
    result = await probe.api("DELETE", f"/api/sequence?sequenceId={sequence_id}")
    after = await probe.api("GET", "/api/sequence/list")
    items = after.get("data", {}).get("result", []) if isinstance(after.get("data"), dict) else []
    probe.findings["archive"].update({"status": result.get("status"),
                                      "archived_after": [s.get("archived") for s in items if s.get("id") == sequence_id]})
    probe.save()
    print(f"  archived {sequence_id} ({title}): {probe.findings['archive']}")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(settings.artifact_dir) / "juicebox-editor-probe" / stamp
    out.mkdir(parents=True, exist_ok=True)
    print(f"\nRecording to {out}\n")

    runner = BrowserRunner(settings, headless=not args.headed, slow_mo_ms=0)
    await runner.start()
    try:
        async with runner.profile_context("juicebox", trace_name="juicebox-editor-probe",
                                          channel="chrome") as (_context, page):
            probe = Probe(page, out)
            probe.attach()
            try:
                if args.archive_id:
                    await page.goto(f"{APP}/", wait_until="commit", timeout=60_000)
                    await wait_for(page, "false", 12)
                    await archive(probe, args.archive_id)
                    return 0
                await read_only(probe)
                if args.create:
                    sequence_id = await create_and_fill(probe, stamp)
                    if sequence_id and args.archive:
                        await archive(probe, sequence_id)
                    elif sequence_id:
                        print(f"\n  Left in place for inspection: {sequence_id}. Archive it with\n"
                              f"  python scripts/probe_juicebox_editor.py --archive-id {sequence_id}\n")
                if args.hold:
                    print(f"  Holding the browser open {args.hold}s for a look...")
                    await page.wait_for_timeout(args.hold * 1000)
            finally:
                probe.save()
                probe.close()
    finally:
        await runner.stop()
    print(f"\nDone. {len(list(out.glob('*.png')))} screenshots, findings in {out / 'findings.json'}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", dest="headed", action="store_false", help="run without a window")
    ap.add_argument("--create", action="store_true", help="create, fill and read back one ZZ TEST sequence")
    ap.add_argument("--archive", action="store_true", help="archive the sequence --create made (ZZ TEST only)")
    ap.add_argument("--archive-id", help="archive one sequence by id (ZZ TEST titles only)")
    ap.add_argument("--hold", type=int, default=0, help="seconds to keep the window open at the end")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
