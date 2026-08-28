"""Record a human doing the job once, and write the recipe from it.

Guessing selectors for a platform nobody has seen is slow and wrong. Instead the
operator performs the flow themselves - create the role, add each email, save -
and every interaction is captured with the most stable selector available for
the element they touched.

When a parsed document is supplied, values typed during the recording are
matched back to where they came from, so `Senior Recruitment Consultant` is
written into the recipe as `{{ advert.title }}` rather than as a literal.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import ParsedDocument
from app.utils.templating import html_to_text

log = get_logger(__name__)

# Last line of defence. The browser side already refuses to read secret fields
# and skips identity-provider pages entirely, but a recipe file is written to
# disk and read by people, so a credential must never reach it.
SECRET_HINT = re.compile(
    r"pass|pwd|secret|otp|mfa|totp|token|cvv|card|iban|ssn|security|credential",
    re.IGNORECASE,
)
AUTH_URL = re.compile(
    r"login\.microsoftonline\.com|login\.live\.com|accounts\.google\.com"
    r"|okta\.com|auth0\.com|onelogin\.com|pingidentity\.com|duosecurity\.com"
    r"|/log[-_]?in|/sign[-_]?in|/sso|/oauth|/mfa|/2fa",
    re.IGNORECASE,
)

# Injected into every page. Picks the most stable selector it can find, in the
# same order of preference the recipe docs recommend, and reports each
# interaction back to Python through an exposed binding.
RECORDER_JS = r"""
(() => {
  if (window.__recorderInstalled) { return; }
  window.__recorderInstalled = true;

  const GENERATED = /^(:r[0-9a-z]+:|[a-z]*[-_]?[0-9a-f]{6,}$|mui-\d+|radix-|headlessui-)/i;
  const esc = (v) => (window.CSS && CSS.escape ? CSS.escape(v) : v);

  const accessibleName = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) { return aria.trim(); }
    const text = (el.innerText || el.value || '').trim();
    return text.length && text.length <= 50 ? text : '';
  };

  const labelText = (el) => {
    const id = el.getAttribute('id');
    if (id) {
      const l = document.querySelector(`label[for="${esc(id)}"]`);
      if (l && l.innerText.trim()) { return l.innerText.trim(); }
    }
    const wrap = el.closest('label');
    if (wrap) {
      const clone = wrap.cloneNode(true);
      clone.querySelectorAll('input, textarea, select').forEach((n) => n.remove());
      const t = clone.innerText.trim();
      if (t) { return t; }
    }
    return '';
  };

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      let part = node.tagName.toLowerCase();
      const id = node.getAttribute('id');
      if (id && !GENERATED.test(id)) { parts.unshift(`#${esc(id)}`); break; }
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (same.length > 1) { part += `:nth-of-type(${same.indexOf(node) + 1})`; }
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };

  // Ordered best-first. The recipe takes a list, so more than one is useful.
  const selectorsFor = (el) => {
    const out = [];
    const push = (s) => { if (s && !out.includes(s)) { out.push(s); } };

    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = el.getAttribute(attr);
      if (v) { push(attr === 'data-testid' ? `testid=${v}` : `[${attr}="${v}"]`); }
    }

    const id = el.getAttribute('id');
    if (id && !GENERATED.test(id)) { push(`#${esc(id)}`); }

    const label = labelText(el);
    if (label) { push(`label=${label}`); }

    const name = el.getAttribute('name');
    if (name) { push(`${el.tagName.toLowerCase()}[name="${name}"]`); }

    const placeholder = el.getAttribute('placeholder');
    if (placeholder) { push(`placeholder=${placeholder}`); }

    const role = el.getAttribute('role')
      || ({BUTTON: 'button', A: 'link', SELECT: 'combobox'}[el.tagName]);
    const name2 = accessibleName(el);
    if (role && name2) { push(`role=${role}[name=${JSON.stringify(name2)}]`); }

    if (el.classList.contains('ProseMirror')) { push('.ProseMirror'); }
    if (el.classList.contains('ql-editor')) { push('.ql-editor'); }
    if (el.isContentEditable) { push('[contenteditable=true]'); }

    if (name2 && ['BUTTON', 'A'].includes(el.tagName)) {
      push(`${el.tagName.toLowerCase()}:has-text(${JSON.stringify(name2)})`);
    }

    // On a UI built from divs this is the only stable handle there is - no id,
    // no test id, no role. Playwright's text engine matches the smallest element
    // containing the text and normalises whitespace, so it lands on the control
    // itself rather than on its wrapper.
    const own = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (own && own.length <= 60) { push('text=' + JSON.stringify(own)); }

    push(cssPath(el));
    return out.slice(0, 3);
  };

  const frameOf = () => (window === window.top ? '' : 'IN-IFRAME');

  // Nothing on an identity provider's pages belongs in a recipe. Logging in is
  // done by a human once and replayed from a saved session, so recording the
  // sign-in form would capture credentials to disk for no benefit whatsoever.
  const AUTH_HOST = /login\.microsoftonline\.com|login\.live\.com|accounts\.google\.com|okta\.com|auth0\.com|onelogin\.com|pingidentity\.com|duosecurity\.com|login\.salesforce\.com/i;
  const AUTH_PATH = /(^|\/)(log[-_]?in|sign[-_]?in|sso|oauth|auth|mfa|2fa|verify|password)(\/|$|\?)/i;
  const onAuthPage = () =>
    AUTH_HOST.test(location.hostname) || AUTH_PATH.test(location.pathname);

  // Belt and braces: even off an auth page, never read a secret-shaped field.
  const SECRET_FIELD = /pass|pwd|secret|otp|mfa|totp|token|cvv|card|iban|ssn|security/i;
  const isSecret = (el) => {
    if (!el || el.type === 'password') { return true; }
    const hints = [
      el.getAttribute('name'), el.getAttribute('id'),
      el.getAttribute('autocomplete'), el.getAttribute('aria-label'),
    ].filter(Boolean).join(' ');
    return SECRET_FIELD.test(hints);
  };

  const send = (payload) => {
    if (onAuthPage()) { return; }
    try {
      window.__record({...payload, url: location.href, frame: frameOf()});
    } catch (e) { /* binding not ready yet */ }
  };

  const SEMANTIC = 'button, a, [role=button], [role=tab], [role=option], [role=menuitem], summary';

  // Only a genuinely interactive element becomes a step. Clicking a heading to
  // blur an editor is something an operator does constantly, and it is not part
  // of the job.
  //
  // Semantic markup is preferred and tried first, but plenty of apps build every
  // control out of bare divs - no button, no role, no label. On those, the only
  // thing marking a control as a control is the pointer cursor, and without this
  // fallback a recording of such an app captures nothing at all.
  const clickTarget = (start) => {
    if (!start || start.nodeType !== 1) { return null; }
    const semantic = start.closest(SEMANTIC);
    if (semantic) { return semantic; }
    let node = start;
    for (let depth = 0; node && node.nodeType === 1 && depth < 5; depth += 1) {
      const text = (node.innerText || '').trim();
      if (getComputedStyle(node).cursor === 'pointer' && text && text.length <= 60) {
        return node;
      }
      node = node.parentElement;
    }
    return null;
  };

  document.addEventListener('click', (event) => {
    const el = clickTarget(event.target);
    if (!el || el.isContentEditable) { return; }
    send({
      type: 'click',
      selectors: selectorsFor(el),
      text: accessibleName(el),
      tag: el.tagName.toLowerCase(),
    });
  }, true);

  // `input` rather than `change` for text fields: change only fires on blur, so
  // a form filled top to bottom would be recorded in the order fields were left
  // rather than the order they were filled. Repeats collapse on the Python side.
  document.addEventListener('input', (event) => {
    const el = event.target;
    if (el.isContentEditable) { return; }
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      if (['checkbox', 'radio', 'file'].includes(el.type)) { return; }
      if (isSecret(el)) { return; }
      send({
        type: 'fill',
        selectors: selectorsFor(el),
        value: el.value,
        tag: el.tagName.toLowerCase(),
      });
    }
  }, true);

  document.addEventListener('change', (event) => {
    const el = event.target;
    if (el.tagName === 'SELECT') {
      send({
        type: 'select',
        selectors: selectorsFor(el),
        value: el.value,
        label: el.selectedOptions[0] ? el.selectedOptions[0].text : '',
        tag: 'select',
      });
    } else if (el.type === 'checkbox' || el.type === 'radio') {
      if (isSecret(el)) { return; }
      send({type: 'check', selectors: selectorsFor(el), value: el.checked, tag: el.type});
    }
  }, true);

  // A rich-text editor has no change event, so read it when focus leaves.
  document.addEventListener('focusout', (event) => {
    const el = event.target;
    if (!el || !el.isContentEditable) { return; }
    send({
      type: 'fill_rich',
      selectors: selectorsFor(el),
      html: el.innerHTML,
      value: el.innerText,
      tag: 'contenteditable',
    });
  }, true);
})();
"""


@dataclass(slots=True)
class Recorded:
    type: str
    selectors: list[str] = field(default_factory=list)
    value: Any = None
    html: str = ""
    text: str = ""
    label: str = ""
    tag: str = ""
    url: str = ""
    frame: str = ""

    def key(self) -> tuple:
        return (self.type, tuple(self.selectors), str(self.value), self.html[:80])


class Recording:
    """Collects events, drops the noise, and writes a recipe."""

    def __init__(self, document: ParsedDocument | None = None) -> None:
        self.events: list[Recorded] = []
        self.navigations: list[str] = []
        self.skipped_sensitive = 0
        self.lookup = _context_lookup(document) if document else {}

    def add(self, payload: dict[str, Any]) -> None:
        event = Recorded(
            type=str(payload.get("type") or ""),
            selectors=[str(s) for s in (payload.get("selectors") or []) if s],
            value=payload.get("value"),
            html=str(payload.get("html") or ""),
            text=str(payload.get("text") or ""),
            label=str(payload.get("label") or ""),
            tag=str(payload.get("tag") or ""),
            url=str(payload.get("url") or ""),
            frame=str(payload.get("frame") or ""),
        )
        if not event.selectors:
            return

        if _is_sensitive(event):
            log.info(
                "skipped a sensitive interaction",
                extra={"type": event.type, "reason": "credential or auth page"},
            )
            self.skipped_sensitive += 1
            return

        # A field edited twice, or a click that also fired a change, should not
        # become two steps. Replacing the previous entry keeps the final value.
        if self.events:
            last = self.events[-1]
            if last.type == event.type and last.selectors == event.selectors:
                self.events[-1] = event
                return
            if last.key() == event.key():
                return

        self.events.append(event)
        log.debug("recorded", extra={"type": event.type, "selector": event.selectors[0]})

    def note_navigation(self, url: str) -> None:
        if not self.navigations or self.navigations[-1] != url:
            self.navigations.append(url)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def to_yaml(self, key: str, label: str, start_url: str, kind: str) -> str:
        lines: list[str] = [
            f"# Recorded from a live session on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC.",
            "#",
            "# Every selector below came from an element that was actually clicked or",
            "# typed into, so none of it is guesswork. Two things still need a human:",
            "#",
            "#   1. Split the steps. Everything repeated once per email belongs under",
            "#      `per_email:`, and the final save belongs under `finalise:` with",
            "#      `submit: true`. Repeats are marked >>> REPEAT <<< below.",
            "#   2. Check the values. A typed value that matched the document was",
            "#      replaced with its template path; anything still literal is either",
            "#      a real constant or something the parser did not supply.",
            "",
            f"key: {key}",
            f"label: {_yaml_value(label)}",
            f"kind: {kind}",
            "enabled: false",
            "",
            "login:",
            f"  url: {_yaml_value(start_url)}",
            "  # Something only a logged-in page renders - this proves the session is alive.",
            "  ready_selector: TODO",
            f"  session_file: {key}.storage_state.json",
            "",
            "defaults:",
            "  timeout_ms: 25000",
            "",
            "steps:",
        ]

        if start_url:
            lines += [
                "  - action: goto",
                f"    url: {_yaml_value(start_url)}",
                "",
            ]

        repeats = _repeat_markers(self.events)
        for index, event in enumerate(self.events):
            if index in repeats:
                lines.append(f"  # >>> REPEAT {repeats[index]} starts here <<<")
            lines.extend(self._step_lines(event))
            lines.append("")

        lines += [
            "# TODO: mark the step that actually publishes with `submit: true`, and",
            "# move it plus anything after it into a `finalise:` block.",
        ]
        return "\n".join(lines)

    def _step_lines(self, event: Recorded) -> list[str]:
        lines = [f"  - action: {event.type}"]
        if event.text or event.label:
            lines.append(f"    description: {_yaml_value(event.text or event.label)}")

        if len(event.selectors) == 1:
            lines.append(f"    selector: {_yaml_value(event.selectors[0])}")
        else:
            lines.append("    selector:")
            lines += [f"      - {_yaml_value(s)}" for s in event.selectors]

        if event.frame == "IN-IFRAME":
            lines.append("    # this element was inside an iframe - add: frame: \"<selector>\"")

        if event.type == "fill":
            matched, template = self._match(str(event.value or ""))
            lines.append(f"    value: {_yaml_value(template)}")
            if matched:
                lines.append(f"    # typed: {str(event.value)[:70]!r}")
        elif event.type == "fill_rich":
            matched, template = self._match(html_to_text(event.html) or str(event.value or ""))
            html_template = template.replace("body_text", "body_html") if matched else template
            lines.append(f"    value_html: {_yaml_value(html_template)}")
            lines.append(f"    value: {_yaml_value(template)}")
            if not matched:
                lines.append("    # did not match the document - set the right path by hand")
        elif event.type == "select":
            matched, template = self._match(event.label or str(event.value or ""))
            lines.append(f"    value: {_yaml_value(template)}")
            if event.label and event.label != str(event.value):
                lines.append("    map:")
                lines.append(f"      {_yaml_value(event.label)}: {_yaml_value(event.value)}")
        elif event.type == "check":
            lines.append(f"    value: {str(bool(event.value)).lower()}")

        return lines

    def _match(self, typed: str) -> tuple[bool, str]:
        """Find where a typed value came from, so the recipe references it."""
        cleaned = _normalise(typed)
        if not cleaned or not self.lookup:
            return False, typed

        exact = self.lookup.get(cleaned)
        if exact:
            return True, "{{ " + exact + " }}"

        # Long bodies get reflowed by the editor, so compare loosely.
        if len(cleaned) > 60:
            for value, path in self.lookup.items():
                if len(value) > 60 and (value[:60] == cleaned[:60] or cleaned in value):
                    return True, "{{ " + path + " }}"
        return False, typed


# ----------------------------------------------------------------------
# document lookup
# ----------------------------------------------------------------------


def _is_sensitive(event: Recorded) -> bool:
    """True when an event must not be written to a recipe file."""
    if AUTH_URL.search(event.url or ""):
        return True
    if SECRET_HINT.search(" ".join(event.selectors)):
        return True
    return bool(SECRET_HINT.search(event.text or ""))


def _context_lookup(document: ParsedDocument) -> dict[str, str]:
    """Normalised value -> the template path that produces it."""
    lookup: dict[str, str] = {}

    def add(value: str | None, path: str) -> None:
        cleaned = _normalise(value or "")
        if cleaned and cleaned not in lookup:
            lookup[cleaned] = path

    advert = document.advert
    if advert:
        add(advert.title, "advert.title")
        add(advert.location, "advert.location")
        add(advert.salary, "advert.salary")
        add(advert.employment_type, "advert.employment_type")
        add(advert.category, "advert.category")
        add(advert.reference, "advert.reference")
        add(advert.body_text, "advert.body_text")
        add(html_to_text(advert.body_html), "advert.body_text")
        for name, value in advert.fields.items():
            add(value, f'advert.fields["{name}"]')

    # Inside `per_email` the binding is always `email`, whichever step matched.
    for email in document.emails:
        add(email.subject, "email.subject")
        add(email.body_text, "email.body_text")
        add(html_to_text(email.body_html), "email.body_text")
        if email.delay_days is not None:
            add(str(email.delay_days), "email.delay_days")

    return lookup


def _normalise(value: str) -> str:
    return " ".join((value or "").split()).strip().lower()


def _repeat_markers(events: list[Recorded]) -> dict[int, int]:
    """Flag where the same selector sequence starts again.

    The per-email loop shows up as the same handful of selectors repeating, so
    pointing at the repeats is most of the work of splitting the phases.
    """
    seen: dict[str, int] = {}
    markers: dict[int, int] = {}
    occurrence = 0

    for index, event in enumerate(events):
        signature = f"{event.type}:{event.selectors[0]}"
        if signature in seen and index - seen[signature] > 1:
            occurrence += 1
            markers[index] = occurrence
            seen.clear()
        seen[signature] = index
    return markers


def _yaml_value(value: str) -> str:
    text = str(value or "")
    if not text:
        return '""'
    if re.search(r'[:#\[\]{}",\n]|^\s|\s$|^[-?&*!|>%@`]', text):
        return json.dumps(text)
    return text


# ----------------------------------------------------------------------
# the session
# ----------------------------------------------------------------------


async def record_session(
    key: str,
    label: str,
    start_url: str,
    output: Path,
    document: ParsedDocument | None = None,
    kind: str = "email_sequence",
    settings: Settings | None = None,
    storage_state: dict | None = None,
    save_session: bool = True,
    browser_channel: str | None = None,
) -> Path:
    """Open a browser, watch the operator do the job, write the recipe."""
    from app.platforms.browser import BrowserRunner

    settings = settings or get_settings()
    recording = Recording(document)

    use_profile = bool(settings.use_browser_profile)
    runner = BrowserRunner(settings, headless=False)
    await runner.start()

    opener = (
        runner.profile_context(
            key, trace_name=f"{key}-record", channel=browser_channel
        )
        if use_profile
        else runner.context(storage_state=storage_state)
    )

    try:
        async with opener as (context, page):
            await context.expose_binding(
                "__record", lambda source, payload: recording.add(payload or {})
            )
            # add_init_script re-installs the recorder after every navigation and
            # in every frame, so a single-page app does not lose it.
            await context.add_init_script(RECORDER_JS)

            page.on("framenavigated", lambda frame: (
                recording.note_navigation(frame.url) if frame is page.main_frame else None
            ))

            await page.goto(start_url, wait_until="domcontentloaded")
            await page.evaluate(RECORDER_JS)

            print(f"\n  Recording {label}.")
            print("  Do the whole job once: create the role, add every email, save.")
            print("  Take your time - nothing is timed.")
            print("\n  Press Enter here when you are finished.\n")

            await _wait_for_finish(page)

            events = len(recording.events)
            print(f"  Captured {events} interactions.")
            if recording.skipped_sensitive:
                print(
                    f"  Skipped {recording.skipped_sensitive} interaction(s) on "
                    "sign-in pages - credentials are never recorded."
                )

            if save_session:
                # An operator who logged in during the recording should not have
                # to log in again.
                from app.sessions.store import SessionStore

                state = await context.storage_state()
                if state.get("cookies"):
                    path = SessionStore(settings).save_state(key, state)
                    print(f"  Session saved: {path}")

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            recording.to_yaml(key, label, start_url, kind), encoding="utf-8"
        )
        log.info(
            "recording written",
            extra={"platform": key, "events": events, "path": str(output)},
        )
        return output
    finally:
        await runner.stop()


async def _wait_for_finish(page: Any) -> None:
    """Finish on Enter, or when the operator closes the browser."""
    enter = asyncio.create_task(asyncio.to_thread(input))
    closed = asyncio.create_task(page.wait_for_event("close", timeout=0))

    done, pending = await asyncio.wait(
        {enter, closed}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        with_exception = task.exception()
        if with_exception and task is not closed:
            raise with_exception
