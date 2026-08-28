"""Read a .docx into style-tagged blocks, preserving inline formatting.

We read structure ourselves rather than converting the whole file to HTML in one
shot, because the parser needs to split the document *by heading*, and a single
HTML blob throws that structure away.
"""

from __future__ import annotations

import html as html_lib
import re
from io import BytesIO

from app.logging_conf import get_logger
from app.models import Block, DocumentParseError

log = get_logger(__name__)

_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d)", re.IGNORECASE)
_BULLET_STYLE_RE = re.compile(r"bullet|list paragraph", re.IGNORECASE)
_NUMBER_STYLE_RE = re.compile(r"number", re.IGNORECASE)

# A short, bold, punctuation-free line is a heading in practice even when the
# author never applied a Heading style. Plenty of real adverts look like this.
_PSEUDO_HEADING_MAX_CHARS = 90


def read_blocks(content: bytes) -> list[Block]:
    try:
        import docx  # python-docx
        from docx.oxml.ns import qn
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise DocumentParseError(
            "python-docx is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        document = docx.Document(BytesIO(content))
    except Exception as exc:
        raise DocumentParseError(f"Could not open the .docx file: {exc}") from exc

    blocks: list[Block] = []
    body = document.element.body

    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            block = _paragraph_block(document, child, qn)
            if block is not None:
                blocks.append(block)
        elif tag == qn("w:tbl"):
            blocks.extend(_table_blocks(document, child, qn))

    _promote_pseudo_headings(blocks)
    log.info("docx parsed", extra={"blocks": len(blocks)})
    return blocks


# ----------------------------------------------------------------------
# paragraphs
# ----------------------------------------------------------------------


def _paragraph_block(document, element, qn) -> Block | None:
    import docx.text.paragraph as dp

    paragraph = dp.Paragraph(element, document)
    text = paragraph.text.strip()
    style_name = ""
    try:
        style_name = paragraph.style.name or ""
    except (AttributeError, KeyError):
        style_name = ""

    if not text:
        return None

    heading_match = _HEADING_STYLE_RE.match(style_name.strip())
    inline = _inline_html(paragraph, element, qn)

    if heading_match:
        level = int(heading_match.group(1))
        return Block("heading", level, text, f"<h{min(level, 6)}>{inline}</h{min(level, 6)}>")

    if style_name.strip().lower() in ("title", "subtitle"):
        level = 1 if style_name.strip().lower() == "title" else 2
        return Block("title", level, text, f"<h{level}>{inline}</h{level}>")

    if _is_list(element, style_name, qn):
        kind = "list_number" if _NUMBER_STYLE_RE.search(style_name) else "list_bullet"
        return Block(kind, 0, text, f"<li>{inline}</li>")

    return Block("body", 0, text, f"<p>{inline}</p>")


def _is_list(element, style_name: str, qn) -> bool:
    properties = element.find(qn("w:pPr"))
    if properties is not None and properties.find(qn("w:numPr")) is not None:
        return True
    return bool(_BULLET_STYLE_RE.search(style_name) or _NUMBER_STYLE_RE.search(style_name))


def _inline_html(paragraph, element, qn) -> str:
    """Rebuild the paragraph as HTML, keeping bold/italic/underline and links."""
    parts: list[str] = []
    rels = getattr(getattr(paragraph, "part", None), "rels", {}) or {}

    for child in element.iterchildren():
        if child.tag == qn("w:r"):
            parts.append(_run_html(child, qn))
        elif child.tag == qn("w:hyperlink"):
            inner = "".join(
                _run_html(run, qn) for run in child.iterchildren() if run.tag == qn("w:r")
            )
            rel_id = child.get(qn("r:id"))
            target = ""
            if rel_id and rel_id in rels:
                try:
                    target = rels[rel_id].target_ref or ""
                except AttributeError:
                    target = ""
            if target and inner:
                parts.append(f'<a href="{html_lib.escape(target, quote=True)}">{inner}</a>')
            else:
                parts.append(inner)

    rebuilt = "".join(parts).strip()
    return rebuilt or html_lib.escape(paragraph.text.strip())


def _run_html(run_element, qn) -> str:
    texts: list[str] = []
    for node in run_element.iter():
        if node.tag == qn("w:t"):
            texts.append(node.text or "")
        elif node.tag == qn("w:tab"):
            texts.append(" ")
        elif node.tag in (qn("w:br"), qn("w:cr")):
            texts.append("\n")

    text = "".join(texts)
    if not text:
        return ""

    escaped = html_lib.escape(text).replace("\n", "<br/>")
    properties = run_element.find(qn("w:rPr"))
    if properties is None:
        return escaped

    if _toggled(properties, qn("w:b")):
        escaped = f"<strong>{escaped}</strong>"
    if _toggled(properties, qn("w:i")):
        escaped = f"<em>{escaped}</em>"
    if properties.find(qn("w:u")) is not None:
        escaped = f"<u>{escaped}</u>"
    return escaped


def _toggled(properties, tag: str) -> bool:
    """Word writes <w:b/> for on and <w:b w:val="0"/> for off."""
    node = properties.find(tag)
    if node is None:
        return False
    value = node.get(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
    )
    return value not in ("0", "false", "none")


# ----------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------


def _table_blocks(document, element, qn) -> list[Block]:
    """Flatten a table to `key: value` lines.

    Adverts routinely carry their metadata in a two-column table (Location |
    London), and that reads identically to the labelled-line form the field
    extractor already understands.
    """
    import docx.table as dt

    blocks: list[Block] = []
    try:
        table = dt.Table(element, document)
    except Exception:
        return blocks

    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        cells = [c for c in cells if c]
        if not cells:
            continue
        if len(cells) >= 2:
            text = f"{cells[0]}: {' '.join(cells[1:])}"
        else:
            text = cells[0]
        blocks.append(Block("body", 0, text, f"<p>{html_lib.escape(text)}</p>"))
    return blocks


# ----------------------------------------------------------------------
# heuristics
# ----------------------------------------------------------------------


def _promote_pseudo_headings(blocks: list[Block]) -> None:
    """Treat fully-bold short lines as headings when the doc has no real ones."""
    if any(b.is_heading for b in blocks):
        return

    for block in blocks:
        if block.style != "body":
            continue
        stripped = block.html.strip()
        fully_bold = stripped.startswith("<p><strong>") and stripped.endswith(
            "</strong></p>"
        )
        short = len(block.text) <= _PSEUDO_HEADING_MAX_CHARS
        unpunctuated = not block.text.rstrip().endswith((".", "!", "?", ","))
        if fully_bold and short and unpunctuated:
            block.style = "heading"
            block.level = 2
            block.html = f"<h2>{html_lib.escape(block.text)}</h2>"
