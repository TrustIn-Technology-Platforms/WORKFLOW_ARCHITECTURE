"""The actions a recipe step can perform.

Each handler receives a `StepRun` carrying the page, the already-rendered
parameters, and somewhere to record captures. Handlers raise `PlatformError`
with a message naming what failed; the engine adds the step number and the
platform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from app.logging_conf import get_logger
from app.models import PlatformError
from app.utils.templating import html_to_text

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Locator, Page

log = get_logger(__name__)


@dataclass(slots=True)
class StepRun:
    page: "Page"
    params: dict[str, Any]
    captures: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 20_000

    def get(self, key: str, default: Any = None) -> Any:
        value = self.params.get(key, default)
        return default if value is None else value

    def text(self, key: str, default: str = "") -> str:
        value = self.params.get(key)
        return default if value is None else str(value)


Handler = Callable[[StepRun], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    handler: Handler
    required: tuple[str, ...] = ()
    #  Parameters whose values are rendered as templates before the handler runs.
    templated: tuple[str, ...] = ()
    needs_selector: bool = True


# ----------------------------------------------------------------------
# locators
# ----------------------------------------------------------------------


def resolve_locator(run: StepRun, selector: str) -> "Locator":
    """Turn one selector string into a Locator, honouring a `frame` parameter.

    Rich-text editors are often inside an iframe, and a selector that works in
    devtools then finds nothing from the page root.
    """
    root: Any = run.page
    frame_selector = run.params.get("frame")
    if frame_selector:
        root = run.page.frame_locator(str(frame_selector))

    prefix, _, rest = selector.partition("=")
    prefix = prefix.strip().lower()

    if prefix == "label":
        return root.get_by_label(rest, exact=False)
    if prefix == "placeholder":
        return root.get_by_placeholder(rest, exact=False)
    if prefix == "testid":
        return root.get_by_test_id(rest)
    if prefix == "alt":
        return root.get_by_alt_text(rest, exact=False)
    if prefix == "title":
        return root.get_by_title(rest, exact=False)
    # `role=`, `text=`, `css=`, `xpath=` and bare CSS are all handled natively.
    return root.locator(selector)


async def find(run: StepRun, *, required: bool = True) -> "Locator | None":
    """First selector that actually matches, or None when optional.

    A recipe may give `selector` as a list. On a platform whose markup is not
    fully known, listing two or three candidates is what keeps a small UI change
    from breaking the run.
    """
    selectors = _selector_list(run)
    if not selectors:
        raise PlatformError("step has no selector")

    timeout = run.timeout_ms if required else min(run.timeout_ms, 3_000)
    per_selector = max(500, timeout // max(1, len(selectors)))
    problems: list[str] = []

    for selector in selectors:
        locator = resolve_locator(run, selector).first
        try:
            await locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except Exception as exc:
            problems.append(f"{selector} ({_short(exc)})")

    if not required:
        return None
    raise PlatformError(f"no element matched: {'; '.join(problems)}")


def _selector_list(run: StepRun) -> list[str]:
    selector = run.params.get("selector")
    if selector is None:
        return []
    if isinstance(selector, str):
        return [selector]
    return [str(s) for s in selector if s]


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0][:120] if text else exc.__class__.__name__


# ----------------------------------------------------------------------
# navigation and clicking
# ----------------------------------------------------------------------


async def action_goto(run: StepRun) -> None:
    url = run.text("url")
    if not url:
        raise PlatformError("goto needs a url")
    wait_until = run.text("wait_until", "domcontentloaded")
    await run.page.goto(url, wait_until=wait_until)


async def action_click(run: StepRun) -> None:
    locator = await find(run, required=not run.get("optional", False))
    if locator is None:
        return
    await locator.scroll_into_view_if_needed(timeout=run.timeout_ms)
    # `force: true` skips Playwright's actionability checks. Needed when the
    # target sits under a floating panel or only reaches full opacity on hover -
    # both true of noon's campaign editor.
    await locator.click(timeout=run.timeout_ms, force=bool(run.get("force", False)))


async def action_dismiss(run: StepRun) -> None:
    """Click if present, never fail. For cookie banners and onboarding tours."""
    locator = await find(run, required=False)
    if locator is None:
        return
    try:
        await locator.click(timeout=3_000)
    except Exception as exc:
        log.debug("dismiss skipped", extra={"error": _short(exc)})


async def action_press(run: StepRun) -> None:
    key = run.text("key")
    if not key:
        raise PlatformError("press needs a key")
    if _selector_list(run):
        locator = await find(run, required=not run.get("optional", False))
        if locator is None:
            return
        await locator.press(key, timeout=run.timeout_ms)
    else:
        await run.page.keyboard.press(key)


# ----------------------------------------------------------------------
# plain fields
# ----------------------------------------------------------------------


async def action_fill(run: StepRun) -> None:
    value = run.text("value")
    optional = bool(run.get("optional", False))
    if not value.strip() and optional:
        return

    locator = await find(run, required=not optional)
    if locator is None:
        return
    await locator.scroll_into_view_if_needed(timeout=run.timeout_ms)
    await locator.fill(value, timeout=run.timeout_ms)


async def action_select(run: StepRun) -> None:
    value = _mapped_value(run)
    optional = bool(run.get("optional", False))
    if not value and optional:
        return

    locator = await find(run, required=not optional)
    if locator is None:
        return

    # A native <select> takes a value or a visible label, and which one the
    # platform uses is rarely documented. Try both before giving up.
    last: Exception | None = None
    for attempt in ("value", "label"):
        try:
            await locator.select_option(**{attempt: value}, timeout=run.timeout_ms)
            return
        except Exception as exc:
            last = exc
    raise PlatformError(
        f"could not select {value!r}: {_short(last) if last else 'no option matched'}"
    )


async def action_combobox(run: StepRun) -> None:
    """Type into a custom dropdown, then click the matching option.

    Modern apps rarely use a native <select>. Location and owner fields are
    almost always an autocomplete that ignores a plain fill and only commits a
    value when an option is clicked.
    """
    value = _mapped_value(run)
    optional = bool(run.get("optional", False))
    if not value and optional:
        return

    locator = await find(run, required=not optional)
    if locator is None:
        return

    await locator.click(timeout=run.timeout_ms)
    await locator.fill("", timeout=run.timeout_ms)
    await locator.type(value, delay=40)

    option_selector = run.params.get("option_selector")
    option_run = StepRun(
        page=run.page,
        params={
            "selector": option_selector
            or [
                f"role=option[name=\"{value}\"i]",
                f"[role=option]:has-text('{value}')",
                f"li:has-text('{value}')",
            ],
            "frame": run.params.get("frame"),
        },
        timeout_ms=run.timeout_ms,
    )
    option = await find(option_run, required=False)
    if option is not None:
        await option.click(timeout=run.timeout_ms)
        return

    # Some comboboxes commit the highlighted option on Enter and expose no
    # clickable list node at all.
    await locator.press("Enter", timeout=run.timeout_ms)


async def action_check(run: StepRun) -> None:
    locator = await find(run, required=not run.get("optional", False))
    if locator is None:
        return
    if run.get("value", True) in (False, "false", "0", "no"):
        await locator.uncheck(timeout=run.timeout_ms)
    else:
        await locator.check(timeout=run.timeout_ms)


async def action_upload(run: StepRun) -> None:
    path = run.text("path")
    if not path:
        raise PlatformError("upload needs a path")
    locator = await find(run, required=True)
    assert locator is not None
    await locator.set_input_files(path, timeout=run.timeout_ms)


def _mapped_value(run: StepRun) -> str:
    value = run.text("value").strip()
    mapping = run.get("map") or {}
    if isinstance(mapping, dict) and value:
        for ours, theirs in mapping.items():
            if str(ours).strip().lower() == value.lower():
                return str(theirs)
    if not value:
        return str(run.get("default", "") or "")
    return value


# ----------------------------------------------------------------------
# rich text
# ----------------------------------------------------------------------

# Writing formatted copy into a rich-text editor is the awkward part of every
# platform. `fill()` works only on real inputs; a contenteditable driven by
# Quill, ProseMirror, TipTap, Slate or Lexical keeps its own model and ignores
# direct DOM writes. The reliable route is a paste event carrying text/html,
# because every one of those editors implements paste handling.

_PASTE_HTML = """
(args) => {
  const el = args.el;
  el.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  if (args.replace) {
    selection.removeAllRanges();
    selection.addRange(range);
  } else {
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
  }
  const data = new DataTransfer();
  data.setData('text/html', args.html);
  data.setData('text/plain', args.text);
  const event = new ClipboardEvent('paste', {
    clipboardData: data, bubbles: true, cancelable: true
  });
  return el.dispatchEvent(event);
}
"""

_INSERT_HTML = """
(args) => {
  const el = args.el;
  el.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  selection.removeAllRanges();
  selection.addRange(range);
  if (!args.replace) { selection.collapseToEnd(); }
  return document.execCommand('insertHTML', false, args.html);
}
"""

_IS_PLAIN_FIELD = """
(el) => ['INPUT', 'TEXTAREA'].includes(el.tagName)
"""

_CONTENT_LENGTH = """
(el) => (el.innerText || el.textContent || el.value || '').trim().length
"""


async def action_fill_rich(run: StepRun) -> None:
    html = run.text("value_html")
    plain = run.text("value") or html_to_text(html)
    optional = bool(run.get("optional", False))

    if not html.strip() and not plain.strip():
        if optional:
            return
        raise PlatformError("fill_rich has nothing to write")

    locator = await find(run, required=not optional)
    if locator is None:
        return

    await locator.scroll_into_view_if_needed(timeout=run.timeout_ms)
    handle = await locator.element_handle(timeout=run.timeout_ms)
    if handle is None:
        raise PlatformError("could not take a handle on the editor")

    # A plain <textarea> needs none of the ceremony below.
    if await run.page.evaluate(_IS_PLAIN_FIELD, handle):
        await locator.fill(plain, timeout=run.timeout_ms)
        return

    replace = bool(run.get("replace", True))
    requested = str(run.get("strategy", "auto")).lower()
    order = (
        ["paste_event", "insert_html", "type"]
        if requested == "auto"
        else [requested]
    )

    await locator.click(timeout=run.timeout_ms)
    if replace:
        # Select-all and then delete, rather than pasting over a selection. An
        # editor with its own model (Draft.js on noon) syncs the native selection
        # asynchronously, so a paste dispatched straight after select-all can
        # land on a stale, shorter selection and leave a tail of the old text
        # behind. Deleting first makes the paste go into an empty editor.
        await run.page.keyboard.press("Control+A")
        await run.page.keyboard.press("Delete")
        await run.page.wait_for_timeout(150)

    problems: list[str] = []
    for strategy in order:
        try:
            written = await _write_rich(
                run, locator, handle, strategy, html, plain, replace
            )
        except Exception as exc:
            problems.append(f"{strategy}: {_short(exc)}")
            continue
        if written:
            log.info(
                "rich text written",
                extra={"strategy": strategy, "chars": len(html or plain)},
            )
            return
        problems.append(f"{strategy}: editor stayed empty")

    raise PlatformError("could not write into the editor - " + "; ".join(problems))


async def _write_rich(
    run: StepRun,
    locator: "Locator",
    handle: Any,
    strategy: str,
    html: str,
    plain: str,
    replace: bool,
) -> bool:
    before = int(await run.page.evaluate(_CONTENT_LENGTH, handle) or 0)

    if strategy == "paste_event":
        await run.page.evaluate(
            _PASTE_HTML,
            {"el": handle, "html": html or plain, "text": plain, "replace": replace},
        )
    elif strategy == "insert_html":
        await run.page.evaluate(
            _INSERT_HTML, {"el": handle, "html": html or plain, "replace": replace}
        )
    elif strategy == "type":
        # Last resort: formatting is lost, but the copy still lands.
        if replace:
            await run.page.keyboard.press("Control+A")
            await run.page.keyboard.press("Delete")
        await locator.type(plain, delay=5)
    else:
        raise PlatformError(f"unknown fill_rich strategy {strategy!r}")

    await run.page.wait_for_timeout(150)
    after = int(await run.page.evaluate(_CONTENT_LENGTH, handle) or 0)

    # Replacing should leave roughly the new content; appending should grow it.
    # Either way an unchanged empty editor means the strategy did nothing.
    return after > 0 and (after != before or before == 0)


# ----------------------------------------------------------------------
# waiting and assertions
# ----------------------------------------------------------------------


async def action_wait_for(run: StepRun) -> None:
    locator = await find(run, required=True)
    assert locator is not None


async def action_wait_for_hidden(run: StepRun) -> None:
    selectors = _selector_list(run)
    if not selectors:
        raise PlatformError("wait_for_hidden needs a selector")
    locator = resolve_locator(run, selectors[0]).first
    await locator.wait_for(state="hidden", timeout=run.timeout_ms)


async def action_wait_for_url(run: StepRun) -> None:
    pattern = run.text("pattern")
    if not pattern:
        raise PlatformError("wait_for_url needs a pattern")
    await run.page.wait_for_url(re.compile(pattern), timeout=run.timeout_ms)


async def action_wait(run: StepRun) -> None:
    await run.page.wait_for_timeout(int(run.get("ms", 1000)))


async def action_assert_text(run: StepRun) -> None:
    expected = run.text("text")
    locator = await find(run, required=True)
    assert locator is not None
    actual = (await locator.inner_text(timeout=run.timeout_ms)) or ""
    if expected.lower() not in actual.lower():
        raise PlatformError(f"expected {expected!r}, page said {actual.strip()[:160]!r}")


# ----------------------------------------------------------------------
# captures
# ----------------------------------------------------------------------


async def action_capture_url(run: StepRun) -> None:
    name = run.text("as", "post_url")
    url = run.page.url
    pattern = run.params.get("pattern")
    if pattern:
        match = re.search(str(pattern), url)
        if not match:
            if run.get("optional", False):
                return
            raise PlatformError(f"URL {url!r} did not match {pattern!r}")
        url = match.group(0)
    run.captures[name] = url


async def action_capture_text(run: StepRun) -> None:
    name = run.text("as", "captured")
    locator = await find(run, required=not run.get("optional", False))
    if locator is None:
        return
    run.captures[name] = ((await locator.inner_text(timeout=run.timeout_ms)) or "").strip()


async def action_capture_attribute(run: StepRun) -> None:
    name = run.text("as", "captured")
    attribute = run.text("attribute", "href")
    locator = await find(run, required=not run.get("optional", False))
    if locator is None:
        return
    value = await locator.get_attribute(attribute, timeout=run.timeout_ms)
    if value:
        run.captures[name] = value


async def action_screenshot(run: StepRun) -> None:
    from app.config import get_settings

    directory = get_settings().artifact_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.text('name', 'step')}.png"
    await run.page.screenshot(path=str(path), full_page=True)
    run.captures.setdefault("screenshots", "")
    run.captures["screenshots"] = f"{run.captures['screenshots']} {path}".strip()


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------

ACTIONS: dict[str, ActionSpec] = {
    "goto": ActionSpec(action_goto, ("url",), ("url",), needs_selector=False),
    "click": ActionSpec(action_click, ("selector",)),
    "dismiss": ActionSpec(action_dismiss, ("selector",)),
    "press": ActionSpec(action_press, ("key",), ("key",), needs_selector=False),
    "fill": ActionSpec(action_fill, ("selector", "value"), ("value",)),
    "fill_rich": ActionSpec(
        action_fill_rich, ("selector",), ("value", "value_html")
    ),
    "select": ActionSpec(action_select, ("selector", "value"), ("value", "default")),
    "combobox": ActionSpec(
        action_combobox, ("selector", "value"), ("value", "default")
    ),
    "check": ActionSpec(action_check, ("selector",)),
    "upload": ActionSpec(action_upload, ("selector", "path"), ("path",)),
    "wait_for": ActionSpec(action_wait_for, ("selector",)),
    "wait_for_hidden": ActionSpec(action_wait_for_hidden, ("selector",)),
    "wait_for_url": ActionSpec(
        action_wait_for_url, ("pattern",), (), needs_selector=False
    ),
    "wait": ActionSpec(action_wait, (), (), needs_selector=False),
    "assert_text": ActionSpec(action_assert_text, ("selector", "text"), ("text",)),
    "capture_url": ActionSpec(action_capture_url, (), ("as",), needs_selector=False),
    "capture_text": ActionSpec(action_capture_text, ("selector",), ("as",)),
    "capture_attribute": ActionSpec(action_capture_attribute, ("selector",), ("as",)),
    "screenshot": ActionSpec(
        action_screenshot, (), ("name",), needs_selector=False
    ),
}
