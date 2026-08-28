"""Turn style-tagged blocks into an advert and an ordered email sequence.

The rules here are heuristics over how people actually write these documents,
not a schema they are made to follow. So nothing raises for merely-messy input:
anything ambiguous is recorded in `ParsedDocument.warnings` and surfaces on the
Notion row, where a human can see it and decide whether it matters.
"""

from __future__ import annotations

import re

from app.logging_conf import get_logger
from app.models import Advert, Block, EmailStep, ParsedDocument, Section

log = get_logger(__name__)

# --- heading classification ------------------------------------------------

# "Email1" with no separator is as common as "Email 1", so the digit group has
# to be reachable without one.
_EMAIL_HEADING = re.compile(
    r"""^\s*
    (?:e[-\s]?mail|follow[\s-]?up|touch|step|sequence\s+step|outreach|message)
    \s*[#:\-–]?\s*(\d+)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Non-email steps. A sequence document mixes channels under sibling headings,
# and each one is a step that has to reach a different field on the platform -
# posting an InMail into an email body is silently wrong, so the channel is
# carried on the step rather than inferred later.
_CHANNEL_HEADINGS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # InMail first: "LinkedIn InMail" matches the linkedin pattern too, and the
    # narrower reading is the correct one.
    (
        "inmail",
        re.compile(
            r"^\s*(?:linked[\s-]?in\s+)?in[\s-]?mail"
            r"\s*[#:\-–]?\s*(\d+)?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "linkedin",
        re.compile(
            r"^\s*(?:linked[\s-]?in)\s*"
            r"(?:connection(?:\s+request)?|invite|invitation|note|dm|message)?"
            r"\s*[#:\-–]?\s*(\d+)?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        # "Connect (Day 7)" - generator shorthand for the connection request.
        "linkedin",
        re.compile(
            r"^\s*connect(?:ion)?(?:\s+request|\s+note)?\s*[#:\-–]?\s*(\d+)?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "wellfound",
        re.compile(
            r"^\s*(?:wellfound|angel\s*list)\s*[#:\-–]?\s*(\d+)?\s*$",
            re.IGNORECASE,
        ),
    ),
)

# A lone "Subject" heading sets the subject for the email steps around it,
# because authors write it once above a run of emails.
_SHARED_SUBJECT_HEADING = re.compile(
    r"^\s*(?:subject|subject\s*line)\s*[:\-–]?\s*$", re.IGNORECASE
)

# "Ad · LinkedIn (Anonymised)", "Ad - Wellfound": a per-site job advert section.
# Classified as advert content so it never gets absorbed into an email body.
_AD_SECTION = re.compile(r"^\s*ad(?:vert(?:isement)?)?\s*[·•\-–:]\s*\S", re.IGNORECASE)
_ADVERT_HEADING = re.compile(
    r"""^\s*(?:
        job\s*(?:advert|ad|spec|description)
      | advert(?:isement)?
      | vacancy
      | the\s+role
      | role\s*(?:overview|summary|profile)?
      | position
      | about\s+the\s+role
    )\s*[:\-–]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_SEQUENCE_HEADING = re.compile(
    r"^\s*(?:email\s+sequence|outreach\s+sequence|sequence|campaign|cadence)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)

_SUBJECT_LINE = re.compile(r"^\s*(?:subject|subject\s*line|re)\s*[:\-–]\s*(.+)$", re.I)
_GREETING = re.compile(
    r"^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))\b", re.I
)
_DELAY = re.compile(
    r"""(?:
        (?:send|wait|delay)\s*(?:after|for|by)?\s*\+?\s*(\d+)\s*(?:working\s+)?days?
      | day\s*[#:]?\s*(\d+)\b
      | \+\s*(\d+)\s*d(?:ays?)?\b
      | after\s+(\d+)\s*(?:working\s+)?days?
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# --- advert fields ---------------------------------------------------------

# A dash only counts as a separator when it has spaces around it. Without that
# rule a bullet such as "Hands-on and scrappy, ..." reads as a field called
# "Hands", which is exactly what happened on the first real document.
_FIELD_LINE = re.compile(
    r"^\s*([A-Za-z][A-Za-z /&'()-]{1,38}?)\s*(?::|\s[–-]\s)\s*(.+?)\s*$"
)
# "About Company:" is a section label, not a title.
_LABEL_LIKE = re.compile(r"[:\-–]\s*$")
_MAX_LABEL_CHARS = 40

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "location": ("location", "based in", "where", "site", "office", "region"),
    "salary": ("salary", "rate", "package", "compensation", "pay", "salary range"),
    "employment_type": (
        "type", "contract", "contract type", "employment type", "hours",
        "job type", "term",
    ),
    "category": ("sector", "category", "discipline", "industry", "department", "function"),
    "reference": ("ref", "reference", "job ref", "vacancy ref", "job reference", "ref no"),
}
_FIELD_LOOKUP = {
    alias: field for field, aliases in _FIELD_ALIASES.items() for alias in aliases
}

# A heading this long is almost certainly a sentence that got bolded.
_MAX_SUBJECT_CHARS = 200


def parse_document(blocks: list[Block]) -> ParsedDocument:
    """Blocks in, structure out. Never raises for messy input."""
    warnings: list[str] = []
    sections = split_sections(blocks)

    if not sections:
        return ParsedDocument(warnings=["The document had no readable content."])

    advert_sections, email_sections, warnings, shared_subject = _classify(sections)
    # Steps are numbered within their own channel: "Email1" is the first email
    # and the LinkedIn note is the first linkedin step, so a mixed document does
    # not produce two #1s. Position is counted per channel for the same reason.
    positions: dict[str, int] = {}
    emails: list[EmailStep] = []
    for section in email_sections:
        channel = _channel_of(section.heading.strip()) or "email"
        positions[channel] = positions.get(channel, 0) + 1
        emails.append(_build_email(section, positions[channel], warnings))

    # Sort within a channel only; document order decides between channels.
    channel_order = list(dict.fromkeys(e.channel for e in emails))
    emails.sort(key=lambda e: (channel_order.index(e.channel), e.order))
    _check_orders(emails, warnings)

    advert = _build_advert(advert_sections, warnings)

    # An email with no subject of its own goes out under the role's title -
    # which is what recruiters write by hand anyway.
    for email in emails:
        if not email.subject.strip() and shared_subject and email.is_email:
            # A document-level "Subject" heading is the author's intent for the
            # email steps; it is not a subject for a LinkedIn note or an InMail.
            email.subject = shared_subject
            continue
        if not email.subject.strip():
            email.subject = advert.title if advert and advert.title else f"Email {email.order}"
            warnings.append(
                f"Email {email.order} had no subject line; the advert title was used."
            )

    document = ParsedDocument(
        sections=sections, advert=advert, emails=emails, warnings=warnings
    )
    log.info(
        "document parsed",
        extra={
            "sections": len(sections),
            "emails": len(emails),
            "has_advert": advert is not None,
            "warnings": len(warnings),
        },
    )
    return document


# ----------------------------------------------------------------------
# 4a. blocks into sections
# ----------------------------------------------------------------------


def split_sections(blocks: list[Block]) -> list[Section]:
    """Start a new section at each heading, keeping everything before the first."""
    sections: list[Section] = []
    current: Section | None = None

    for block in blocks:
        if block.is_heading:
            current = Section(heading=block.text.strip(), level=block.level or 1)
            sections.append(current)
            continue
        if current is None:
            current = Section(heading="", level=0)
            sections.append(current)
        current.blocks.append(block)

    return [s for s in sections if s.heading or s.blocks]


# ----------------------------------------------------------------------
# 4b. classify
# ----------------------------------------------------------------------


def _classify(
    sections: list[Section],
) -> tuple[list[Section], list[Section], list[str]]:
    warnings: list[str] = []
    advert_sections: list[Section] = []
    email_sections: list[Section] = []
    seen_email = False
    seen_advert_heading = False
    shared_subject = ""

    for section in sections:
        heading = section.heading.strip()

        if _SEQUENCE_HEADING.match(heading):
            # A container heading for the emails that follow. It carries no copy
            # of its own, but its body is worth keeping if the author wrote any.
            if section.blocks:
                warnings.append(
                    f"Text under {heading!r} was not attached to an email step."
                )
            continue

        if _SHARED_SUBJECT_HEADING.match(heading):
            # Applies to every step that does not carry its own subject.
            text = " ".join(b.text.strip() for b in section.blocks if b.text.strip())
            if text and not shared_subject:
                shared_subject = text.strip()
            continue

        if _is_email_heading(heading) or _channel_of(heading):
            email_sections.append(section)
            seen_email = True
            continue

        if _ADVERT_HEADING.match(heading) or _AD_SECTION.match(heading):
            advert_sections.append(section)
            seen_advert_heading = True
            continue

        # An unlabelled section continues whatever came before it. Once the
        # emails have started, a bare heading is a part of the current email
        # rather than a return to the advert.
        if seen_email and email_sections:
            email_sections[-1].blocks.extend(_heading_as_block(section))
            email_sections[-1].blocks.extend(section.blocks)
        else:
            advert_sections.append(section)

    if not advert_sections and not email_sections:
        warnings.append("Nothing in the document looked like an advert or an email.")
    elif not advert_sections:
        warnings.append("No advert section was found - the document is emails only.")
    elif not seen_advert_heading and email_sections:
        warnings.append(
            "No explicit advert heading was found; the text before the first "
            "email was used as the advert."
        )

    return advert_sections, email_sections, warnings, shared_subject


# Trailing decoration a generator appends to a heading: "InMail (Day 5)",
# "Connect (Day 7)", "Email 2 · Deeper". The channel patterns anchor on $, so
# strip it before matching; the full heading still feeds the delay parser.
_HEADING_SUFFIX = re.compile(r"\s*(?:[(\[][^)\]]*[)\]]|[·•]\s*[^()\[\]]*)\s*$")


def _channel_of(heading: str) -> str | None:
    """The channel a heading names, or None when it is not a channel heading.

    Patterns are tried in _CHANNEL_HEADINGS order, which is why InMail sits
    first in that tuple.
    """
    if not heading:
        return None
    candidates = (heading, _HEADING_SUFFIX.sub("", heading).strip())
    for channel, pattern in _CHANNEL_HEADINGS:
        for text in candidates:
            if text and pattern.match(text):
                return channel
    return None


def _is_email_heading(heading: str) -> bool:
    if not heading:
        return False
    match = _EMAIL_HEADING.match(heading)
    if not match:
        return False
    # "Follow up" alone is an email; "Steps to apply" is not. Requiring either a
    # number or a short heading keeps prose out.
    return bool(match.group(1)) or len(heading) <= 60


def _heading_as_block(section: Section) -> list[Block]:
    if not section.heading:
        return []
    import html as html_lib

    escaped = html_lib.escape(section.heading)
    return [Block("body", 0, section.heading, f"<p><strong>{escaped}</strong></p>")]


# ----------------------------------------------------------------------
# 4c. email steps
# ----------------------------------------------------------------------


def _build_email(section: Section, position: int, warnings: list[str]) -> EmailStep:
    heading = section.heading.strip()
    channel = _channel_of(heading) or "email"
    if channel == "email":
        match = _EMAIL_HEADING.match(heading)
    else:
        match = next(
            (p.match(heading) for c, p in _CHANNEL_HEADINGS if c == channel), None
        )
    order = int(match.group(1)) if match and match.group(1) else position

    blocks = list(section.blocks)
    subject = ""

    # An explicit "Subject:" line always wins, and is consumed so it is not
    # posted twice - once as the subject and again inside the body.
    for index, block in enumerate(blocks):
        found = _SUBJECT_LINE.match(block.text)
        if found:
            subject = found.group(1).strip()
            blocks.pop(index)
            break

    if not subject:
        subject = _subject_from_heading(heading)

    # With no Subject: line and nothing usable in the heading, the first body
    # line stands in - unless it is the greeting. "Hi {{name}}," is how most
    # real emails open, and it is never a subject. Leave it empty instead;
    # parse_document falls back to the advert title once that is known.
    if not subject and blocks and not _GREETING.match(blocks[0].text):
        subject = blocks[0].text.strip()
        if len(subject) <= _MAX_SUBJECT_CHARS:
            blocks.pop(0)
        else:
            subject = subject[:_MAX_SUBJECT_CHARS].rstrip()
            warnings.append(
                f"Email {order} had no subject line; the first line was used."
            )

    delay = _delay_from(heading)
    if delay is None and blocks:
        delay = _delay_from(blocks[0].text)

    body_text = "\n\n".join(b.text for b in blocks if b.text.strip())
    body_html = "\n".join(_wrap_lists(blocks))

    if not body_text.strip():
        warnings.append(f"Email {order} ({subject!r}) has no body text.")

    # Two greetings in one step means a section the parser did not recognise
    # was absorbed into this message - exactly how two emails end up pasted
    # into one on a platform. Say so loudly; the recruiter can fix the doc's
    # headings before anything sends.
    greetings = sum(
        1 for line in body_text.splitlines() if _GREETING.match(line.strip())
    )
    if greetings >= 2:
        warnings.append(
            f"Email {order} ({heading!r}) contains {greetings} greetings - it "
            "looks like two messages merged into one. A heading between them "
            "was probably not recognised; check the document's section headings."
        )

    return EmailStep(
        channel=channel,
        label=heading,
        order=order,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        delay_days=delay,
    )


def _subject_from_heading(heading: str) -> str:
    """`Email 2 - Following up` becomes `Following up`."""
    stripped = _EMAIL_HEADING.sub("", heading, count=1).strip(" -–:—")
    stripped = _DELAY.sub("", stripped)
    stripped = re.sub(r"\(\s*\)", "", stripped).strip(" -–:()")
    return stripped if len(stripped) > 2 else ""


def _delay_from(text: str) -> int | None:
    match = _DELAY.search(text or "")
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None


def _check_orders(emails: list[EmailStep], warnings: list[str]) -> None:
    """Warn about a repeated number within one channel.

    Across channels a repeat is expected - the first email and the first
    LinkedIn note are both step 1 - so the count is per channel.
    """
    seen: dict[tuple[str, int], int] = {}
    for email in emails:
        key = (email.channel, email.order)
        seen[key] = seen.get(key, 0) + 1
    duplicates = sorted(key for key, count in seen.items() if count > 1)
    for channel, order in duplicates:
        warnings.append(
            f"Two {channel} steps are both numbered {order}. "
            "They were kept in document order."
        )


# ----------------------------------------------------------------------
# 4d. advert
# ----------------------------------------------------------------------


def _build_advert(sections: list[Section], warnings: list[str]) -> Advert | None:
    if not sections:
        return None

    title = ""
    title_block: Block | None = None

    # Real documents often open with the title as a plain first line - not bold,
    # not a heading style - followed by bold labels like "About Company:". A
    # short, unlabelled first line before any heading is the title; the labels
    # that follow are not.
    first = sections[0]
    if not first.heading and first.blocks:
        candidate = first.blocks[0]
        text = candidate.text.strip()
        if (
            candidate.style in ("body", "title")
            and 0 < len(text) <= 120
            and not _LABEL_LIKE.search(text)
            and _field_from(candidate)[0] is None
        ):
            title, title_block = text, candidate

    if not title:
        for section in sections:
            heading = section.heading.strip()
            if heading and not _ADVERT_HEADING.match(heading) and not _LABEL_LIKE.search(heading):
                title = heading
                break
    if not title:
        for section in sections:
            if section.heading:
                title = section.heading.strip()
                break

    body_blocks: list[Block] = []
    fields: dict[str, str] = {}

    for section in sections:
        if section.heading and section.heading != title:
            if not _ADVERT_HEADING.match(section.heading):
                body_blocks.extend(_heading_as_block(section))
        for block in section.blocks:
            if block is title_block:
                continue
            label, value = _field_from(block)
            if label is not None and value:
                fields.setdefault(label, value)
                continue
            body_blocks.append(block)

    if not title and body_blocks:
        title = body_blocks[0].text.strip()[:120]
        warnings.append("The advert had no heading; its first line was used as the title.")

    return _finish_advert(title, body_blocks, fields, warnings)


def _finish_advert(
    title: str, body_blocks: list[Block], fields: dict[str, str], warnings: list[str]
) -> Advert:
    known = {name: fields.pop(alias) for alias, name in _matched_aliases(fields)}
    body_text = "\n\n".join(b.text for b in body_blocks if b.text.strip())
    body_html = "\n".join(_wrap_lists(body_blocks))

    if not body_text.strip():
        warnings.append("The advert has no body text.")

    return Advert(
        title=title or "(untitled)",
        body_text=body_text,
        body_html=body_html,
        location=known.get("location"),
        salary=known.get("salary"),
        employment_type=known.get("employment_type"),
        category=known.get("category"),
        reference=known.get("reference"),
        fields=fields,
    )


def _field_from(block: Block) -> tuple[str | None, str]:
    """`Location: Manchester` becomes ('Location', 'Manchester')."""
    if block.style not in ("body", "list_bullet", "list_number"):
        return None, ""
    match = _FIELD_LINE.match(block.text)
    if not match:
        return None, ""

    label, value = match.group(1).strip(), match.group(2).strip()
    if len(label) > _MAX_LABEL_CHARS or not value:
        return None, ""
    # A sentence with a colon in it is not a field. Real labels are short and
    # rarely run to several words of prose.
    if len(label.split()) > 4:
        return None, ""
    return label, value


def _matched_aliases(fields: dict[str, str]) -> list[tuple[str, str]]:
    matched: list[tuple[str, str]] = []
    for label in list(fields):
        canonical = _FIELD_LOOKUP.get(label.strip().lower())
        if canonical:
            matched.append((label, canonical))
    return matched


# ----------------------------------------------------------------------
# html assembly
# ----------------------------------------------------------------------


def _wrap_lists(blocks: list[Block]) -> list[str]:
    """Wrap runs of `<li>` blocks in a real list element.

    The reader emits bare `<li>` per paragraph because it works one block at a
    time. Pasting those into an editor without a parent list produces plain
    lines, which is not what the document said.
    """
    out: list[str] = []
    open_tag: str | None = None

    for block in blocks:
        if not block.text.strip():
            continue
        if block.style in ("list_bullet", "list_number"):
            wanted = "ul" if block.style == "list_bullet" else "ol"
            if open_tag != wanted:
                if open_tag:
                    out.append(f"</{open_tag}>")
                out.append(f"<{wanted}>")
                open_tag = wanted
            out.append(block.html)
            continue
        if open_tag:
            out.append(f"</{open_tag}>")
            open_tag = None
        out.append(block.html)

    if open_tag:
        out.append(f"</{open_tag}>")
    return out
