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


# noon expands {ai_intro} into a personalised opening line per candidate. No
# other platform can: sent through Loxo it goes out literally, and Juicebox
# would render it as a {{Ai Intro}} field it does not have — either way the
# first line of a real email to a real candidate. In the documents it always
# stands as its own paragraph, so for those platforms the whole paragraph goes;
# the inline pattern is the fallback for a token written mid-sentence.
_AI_INTRO_PARAGRAPH = re.compile(
    r"<p[^>]*>\s*\{\{?\s*ai[ _-]?intro\s*\}?\}\s*</p>\s*", re.IGNORECASE
)
# A plain-text line that is the token and nothing else, with the blank line
# under it. Removing just the token left its blank lines behind, and Loxo -
# which writes body_text, not HTML - sent emails 1 and 2 with three empty lines
# between the greeting and the first paragraph (seen live 2026-09-01).
_AI_INTRO_TEXT_LINE = re.compile(
    r"^[ \t]*\{\{?\s*ai[ _-]?intro\s*\}?\}[ \t]*\r?\n(?:[ \t]*\r?\n)*",
    re.IGNORECASE | re.MULTILINE,
)
_AI_INTRO_INLINE = re.compile(r"\{\{?\s*ai[ _-]?intro\s*\}?\}[ \t]*", re.IGNORECASE)
_EXTRA_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def drop_ai_intro(value: Any) -> str:
    """Remove the noon-only {ai_intro} token for platforms that cannot expand it.

    Whole-line removal, in both shapes the bodies travel in: the HTML paragraph
    (`<p>{{ai_intro}}</p>`) and the plain-text line. The inline pattern is the
    fallback for a token written mid-sentence, and any blank-line pileup the
    removal leaves is collapsed to a single blank line.
    """
    text = _AI_INTRO_PARAGRAPH.sub("", _as_text(value))
    text = _AI_INTRO_TEXT_LINE.sub("", text)
    text = _AI_INTRO_INLINE.sub("", text)
    return _EXTRA_BLANK_LINES.sub("\n\n", text)


_P_JOINT = re.compile(r"</p>\s*<p([^>]*)>", re.IGNORECASE)


def juicebox_spacing(value: Any) -> str:
    """An explicit blank line between paragraphs, the way a person types one.

    Juicebox's TinyMCE renders <p> blocks with no margins, so adjacent
    paragraphs from the docx reader arrive glued together - "Hi {{First Name}},"
    sitting directly on the next line (seen live, 2026-09-01). A person writing
    there makes space by pressing Enter twice, which leaves an empty <p><br></p>
    between blocks; this inserts the same. Plain-text bodies pass through
    untouched - their newlines already say what they mean.
    """
    text = _as_text(value)
    if "</p>" not in text.lower():
        return text
    return _P_JOINT.sub(r"</p><p><br></p><p\1>", text)


def juicebox_tokens(value: Any) -> str:
    """Rewrite personalisation tokens into Juicebox's {{Title Case}} form.

    Runs the double-brace pass first; its output ({{First Name}}) carries a
    space, so the single-brace pass — which matches only brace-free names — will
    not touch it and cannot double-convert. The noon-only {ai_intro} is removed
    before either pass: Title-casing it would invent a field Juicebox rejects.
    """
    text = drop_ai_intro(value)
    text = _DOUBLE_TOKEN.sub(lambda m: _juicebox_label(m.group(1)), text)
    text = _SINGLE_TOKEN.sub(lambda m: _juicebox_label(m.group(1)), text)
    return text


# Wellfound's job description is an EasyMDE editor - Markdown, not HTML. The
# docx reader gives us clean HTML (<p>, <strong>, <em>, <ul>/<li>, <h2>), so a
# small structural conversion keeps the bold labels and bullet lists that make
# an advert readable. Anything Markdown cannot say (underline, colour) is
# dropped to its text.
_MD_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_MD_BLOCKS = {"p", "div", "ul", "ol", "li", "table", "tr", "blockquote", "pre", *_MD_HEADINGS}


def html_to_markdown(value: str) -> str:
    """Convert the reader's HTML into Markdown for a Markdown-only editor."""
    if not value or not value.strip():
        return ""
    from bs4 import BeautifulSoup  # already a dependency of the docx reader

    soup = BeautifulSoup(value, "html.parser")
    text = _md_blocks(soup, depth=0)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _md_inline(node: Any) -> str:
    from bs4 import NavigableString

    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    name = (node.name or "").lower()
    inner = "".join(_md_inline(child) for child in node.children)
    if name == "br":
        return "\n"
    if not inner.strip():
        return inner
    if name in ("strong", "b"):
        return f"**{inner.strip()}**" + (" " if inner.endswith(" ") else "")
    if name in ("em", "i"):
        return f"*{inner.strip()}*" + (" " if inner.endswith(" ") else "")
    if name == "a" and node.get("href"):
        return f"[{inner.strip()}]({node.get('href')})"
    return inner


def _md_blocks(parent: Any, depth: int) -> str:
    """Walk a container, emitting blocks; stray inline runs become paragraphs."""
    from bs4 import NavigableString

    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        para = "".join(run).strip()
        run.clear()
        if para:
            out.append(para + "\n\n")

    for child in parent.children:
        name = (getattr(child, "name", None) or "").lower()
        if isinstance(child, NavigableString) or name not in _MD_BLOCKS:
            run.append(_md_inline(child))
            continue
        flush()
        if name in _MD_HEADINGS:
            out.append("#" * _MD_HEADINGS[name] + " " + _md_inline(child).strip() + "\n\n")
        elif name in ("ul", "ol"):
            out.append(_md_list(child, depth))
        elif name == "li":  # a loose <li> outside any list
            out.append("- " + _md_inline(child).strip() + "\n")
        else:
            inner = _md_blocks(child, depth)
            out.append(inner + ("\n\n" if inner and not inner.endswith("\n\n") else ""))
    flush()
    return "".join(out)


def _md_list(node: Any, depth: int) -> str:
    ordered = (node.name or "").lower() == "ol"
    lines: list[str] = []
    for index, item in enumerate(node.find_all("li", recursive=False), start=1):
        marker = f"{index}." if ordered else "-"
        text_parts: list[str] = []
        nested: list[str] = []
        for child in item.children:
            child_name = (getattr(child, "name", None) or "").lower()
            if child_name in ("ul", "ol"):
                nested.append(_md_list(child, depth + 1))
            else:
                text_parts.append(_md_inline(child))
        lines.append("  " * depth + f"{marker} " + "".join(text_parts).strip())
        lines.extend(block.rstrip("\n") for block in nested if block.strip())
    return "\n".join(lines) + "\n\n"


# Salary in the documents and Notion rows is free text - "$180k-$220k",
# "180,000 - 220,000 USD", "Up to $350k", "£90k". Job boards want two integers
# and a currency. Wellfound also caps the spread at 80,000, so a wider range is
# narrowed from the bottom rather than rejected at the form.
_SALARY_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?(?![\d,])")
_SALARY_MAX_SPREAD = 80_000


def _salary_bounds(value: Any) -> tuple[int, int] | None:
    text = _as_text(value)
    if not text.strip():
        return None
    has_k = bool(re.search(r"\d\s*[kK]\b", text))
    numbers: list[int] = []
    for match in _SALARY_NUMBER.finditer(text):
        amount = float(match.group(1).replace(",", ""))
        if match.group(2) or (has_k and amount < 1000):
            amount *= 1000  # "$180-220k": the first number inherits the k
        if amount < 1000:
            continue  # a stray "3" or "401" is not a salary
        numbers.append(int(round(amount)))
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    low, high = sorted(numbers[:2])
    if high - low > _SALARY_MAX_SPREAD:
        low = high - _SALARY_MAX_SPREAD
    return low, high


def _filter_salary_min(value: Any) -> str:
    bounds = _salary_bounds(value)
    return "" if bounds is None else str(bounds[0])


def _filter_salary_max(value: Any) -> str:
    bounds = _salary_bounds(value)
    return "" if bounds is None else str(bounds[1])


_CURRENCY_SIGNS = (("£", "GBP"), ("€", "EUR"), ("$", "USD"))
_CURRENCY_CODES = re.compile(r"\b(GBP|EUR|USD|CAD|AUD|CHF|INR|SGD)\b", re.IGNORECASE)


def _filter_salary_currency(value: Any) -> str:
    """ISO code from a salary string, or empty to leave the form's default."""
    text = _as_text(value)
    code = _CURRENCY_CODES.search(text)
    if code:
        return code.group(1).upper()
    for sign, iso in _CURRENCY_SIGNS:
        if sign in text:
            return iso
    return ""


# "15+ years", "5-8 years' experience", "minimum of 3 years". The unit has to be
# present: a bare number in an advert is a salary, a headcount or a funding round
# far more often than it is a length of service.
_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|\s*-\s*\d{1,2})?\s*(?:\+)?\s*year", re.IGNORECASE
)


def _filter_years_min(value: Any) -> str:
    """The years-of-experience floor an advert states, as a bare number.

    Wellfound offers `0+` through `10+ years of experience` and nothing above, so
    a 15+ advert becomes 10+ — which is what the recruiters pick by hand anyway.

    A range gives its low end, because the regex captures the first number and
    swallows the rest: "5-8 years" is a floor of five. Across several separate
    mentions the *highest* wins, since an advert states its headline seniority as
    its biggest figure and the skill-specific asides sit below it. The advert is
    saved as a draft either way, so a recruiter sees this before anyone else does.
    """
    matches = _YEARS.findall(_as_text(value))
    if not matches:
        return ""
    stated = max(int(match) for match in matches)
    return str(max(0, min(stated, 10)))


_FILTERS = {
    "truncate": _filter_truncate,
    "years_min": _filter_years_min,
    "markdown": lambda v: html_to_markdown(_as_text(v)),
    "salary_min": _filter_salary_min,
    "salary_max": _filter_salary_max,
    "salary_currency": _filter_salary_currency,
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
