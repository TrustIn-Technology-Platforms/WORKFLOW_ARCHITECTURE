"""Render `{{ ... }}` expressions in recipe values against a run context.

Deliberately tiny. A recipe should describe *which* value goes in a field, not
compute one - anything needing real logic belongs in the parser, so that every
platform sees the same cleaned values.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

from app.models import PipelineError

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_TOKEN = re.compile(
    r"""
      \.?(?P<attr>[A-Za-z_][A-Za-z0-9_]*)      # .name  or leading name
    | \[\s*"(?P<dq>[^"]*)"\s*\]                # ["key"]
    | \[\s*'(?P<sq>[^']*)'\s*\]                # ['key']
    | \[\s*(?P<idx>\d+)\s*\]                   # [0]
    """,
    re.VERBOSE,
)


class TemplateError(PipelineError):
    pass


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


MISSING = _Missing()


# ----------------------------------------------------------------------
# public
# ----------------------------------------------------------------------


def render(value: Any, context: dict[str, Any]) -> str:
    """Render every expression in `value`, returning a string.

    A missing path renders as an empty string rather than raising. Combined
    with `optional: true` on a step, that means a field the document did not
    supply quietly skips its step instead of failing the row.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if "{{" not in value:
        return value

    def substitute(match: re.Match[str]) -> str:
        resolved = evaluate(match.group(1).strip(), context)
        return "" if resolved is MISSING or resolved is None else str(resolved)

    return _EXPR.sub(substitute, value)


def render_deep(value: Any, context: dict[str, Any]) -> Any:
    """Render recursively through dicts and lists, leaving non-strings alone."""
    if isinstance(value, str):
        return render(value, context)
    if isinstance(value, dict):
        return {k: render_deep(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_deep(v, context) for v in value]
    return value


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Resolve `path | filter | filter(arg)` against the context."""
    parts = _split_pipes(expression)
    if not parts or not parts[0]:
        return MISSING

    current = _resolve_path(parts[0], context)
    for filter_expression in parts[1:]:
        current = _apply_filter(filter_expression, current)
    return current


def expressions_in(value: Any) -> list[str]:
    """Every raw expression inside a value, for validation at load time."""
    if not isinstance(value, str):
        return []
    return [match.strip() for match in _EXPR.findall(value)]


def validate(value: Any, known_roots: set[str]) -> list[str]:
    """Return a problem per expression that cannot possibly resolve.

    Only the root segment and the filter names are checked. Deeper paths depend
    on the document being processed, so they are left to render as empty.
    """
    problems: list[str] = []
    for expression in expressions_in(value):
        parts = _split_pipes(expression)
        if not parts or not parts[0]:
            problems.append(f"empty expression: {{{{{expression}}}}}")
            continue

        root = _root_of(parts[0])
        if root is None:
            problems.append(f"unreadable path: {expression!r}")
        elif root not in known_roots:
            options = ", ".join(sorted(known_roots))
            problems.append(f"unknown root {root!r} in {expression!r} (known: {options})")

        for filter_expression in parts[1:]:
            name = filter_expression.split("(", 1)[0].strip()
            if name not in _FILTERS:
                problems.append(f"unknown filter {name!r} in {expression!r}")
    return problems


def html_to_text(value: str) -> str:
    """Flatten HTML to readable plain text, for typing into a plain field."""
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</(p|div|h[1-6]|tr)>", "\n\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</(li|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ----------------------------------------------------------------------
# path resolution
# ----------------------------------------------------------------------


def _resolve_path(path: str, context: dict[str, Any]) -> Any:
    tokens = _tokenize(path.strip())
    if tokens is None:
        return MISSING

    current: Any = context
    for kind, key in tokens:
        if current is MISSING or current is None:
            return MISSING
        if kind == "index":
            try:
                current = current[int(key)]
            except (TypeError, KeyError, IndexError, ValueError):
                return MISSING
            continue
        if isinstance(current, dict):
            if key not in current:
                return MISSING
            current = current[key]
        else:
            current = getattr(current, key, MISSING)
    return current


def _tokenize(path: str) -> list[tuple[str, str]] | None:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(path):
        match = _TOKEN.match(path, position)
        if match is None:
            return None
        if match.group("attr") is not None:
            tokens.append(("attr", match.group("attr")))
        elif match.group("dq") is not None:
            tokens.append(("key", match.group("dq")))
        elif match.group("sq") is not None:
            tokens.append(("key", match.group("sq")))
        else:
            tokens.append(("index", match.group("idx")))
        position = match.end()
    return tokens or None


def _root_of(path: str) -> str | None:
    tokens = _tokenize(path.strip())
    return tokens[0][1] if tokens else None


def _split_pipes(expression: str) -> list[str]:
    """Split on `|`, ignoring pipes inside quotes or brackets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None

    for char in expression:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char in "[(":
            depth += 1
            current.append(char)
        elif char in "])":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return parts


# ----------------------------------------------------------------------
# filters
# ----------------------------------------------------------------------


def _filter_truncate(value: Any, limit: str = "120", suffix: str = "") -> str:
    text = "" if value is MISSING or value is None else str(value)
    try:
        cap = int(limit)
    except ValueError as exc:
        raise TemplateError(f"truncate() needs a number, got {limit!r}") from exc
    return text if len(text) <= cap else text[: max(0, cap - len(suffix))] + suffix


def _filter_default(value: Any, fallback: str = "") -> Any:
    empty = value is MISSING or value is None or str(value).strip() == ""
    return fallback if empty else value


def _as_text(value: Any) -> str:
    return "" if value is MISSING or value is None else str(value)


# Documents are written with double-brace tokens ({{name}}, {{job_company}});
# noon reads single-brace ones ({first_name}, {company}). Anything not in the
# rename map keeps its name and just loses a brace on each side.
_NOON_TOKEN_NAMES = {"name": "first_name", "job_company": "company", "company": "company"}
_CURLY_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _filter_noon_tokens(value: Any) -> str:
    text = _as_text(value)
    return _CURLY_TOKEN.sub(
        lambda m: "{" + _NOON_TOKEN_NAMES.get(m.group(1), m.group(1)) + "}", text
    )


# Juicebox wants double-brace, space-separated, Title Case tokens that match its
# own field labels ({{First Name}}, {{Current Company}}). Its editor refuses to
# save otherwise and shows "use double curly braces like {{First Name}}". Our
# documents write either {first_name} (single) or {{name}} (double), so both
# shapes are normalised. Anything unmapped keeps its words, spaced and cased.
_JUICEBOX_TOKEN_NAMES = {
    "name": "First Name",
    "first_name": "First Name",
    "firstname": "First Name",
    "company": "Current Company",
    "job_company": "Current Company",
    "current_company": "Current Company",
    "job_title": "Job Title",
    "jobtitle": "Job Title",
    "title": "Job Title",
    "education": "Education",
    "sender_first_name": "Sender First Name",
}
_DOUBLE_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SINGLE_TOKEN = re.compile(r"(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})")


def _juicebox_label(name: str) -> str:
    label = _JUICEBOX_TOKEN_NAMES.get(name.lower())
    if label is None:
        label = name.replace("_", " ").strip().title()
    return "{{" + label + "}}"


def juicebox_tokens(value: Any) -> str:
    """Rewrite personalisation tokens into Juicebox's {{Title Case}} form.

    Runs the double-brace pass first; its output ({{First Name}}) carries a
    space, so the single-brace pass — which matches only brace-free names — will
    not touch it and cannot double-convert.
    """
    text = _as_text(value)
    text = _DOUBLE_TOKEN.sub(lambda m: _juicebox_label(m.group(1)), text)
    text = _SINGLE_TOKEN.sub(lambda m: _juicebox_label(m.group(1)), text)
    return text


_FILTERS = {
    "truncate": _filter_truncate,
    "default": _filter_default,
    "noon_tokens": _filter_noon_tokens,
    "juicebox_tokens": juicebox_tokens,
    "upper": lambda v: _as_text(v).upper(),
    "lower": lambda v: _as_text(v).lower(),
    "strip": lambda v: _as_text(v).strip(),
    "oneline": lambda v: " ".join(_as_text(v).split()),
    "plain": lambda v: html_to_text(_as_text(v)),
}

_ARG = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)'|([^,]+?))\s*(?:,|$)""")


def _apply_filter(expression: str, value: Any) -> Any:
    name, _, argument_text = expression.partition("(")
    name = name.strip()
    handler = _FILTERS.get(name)
    if handler is None:
        raise TemplateError(f"Unknown filter {name!r}")

    arguments: list[str] = []
    if argument_text:
        inner = argument_text.rstrip().removesuffix(")")
        for double, single, bare in _ARG.findall(inner):
            argument = double or single or bare.strip()
            if argument:
                arguments.append(argument)

    try:
        return handler(value, *arguments)
    except TypeError as exc:
        raise TemplateError(f"Bad arguments for filter {name!r}: {exc}") from exc
