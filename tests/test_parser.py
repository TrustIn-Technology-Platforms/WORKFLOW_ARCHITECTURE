"""Parser against real documents.

The synthetic fixtures prove the mechanism; these two came out of a recruiter's
Downloads folder and prove the fit. Each fixture is a different shape from the
one the parser was designed against, which is the point.
"""

from __future__ import annotations

from pathlib import Path

from app.documents import parser
from app.documents.docx_reader import read_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _parse(name: str):
    return parser.parse_document(read_blocks((FIXTURES / name).read_bytes()))


def test_real_advert_only_document():
    """A plain first line is the title; bold "Label:" lines are not.

    The document opens with an unstyled, non-bold title line, then bold labels
    ("About Company:", "The Role:", "What you'll do:") over paragraphs and
    bullets. There are no emails at all - an advert-only row is valid.
    """
    document = _parse("real-advert-only.docx")

    assert document.advert is not None
    assert document.advert.title.startswith("Platform Engineer / AWS, Kubernetes")
    assert document.emails == []

    # The bold labels become part of the body, in order, not the title. ("The
    # Role:" is an advert heading and is deliberately not repeated in the body.)
    body = document.advert.body_text
    assert "About Company:" in body
    assert body.index("About Company:") < body.index("What you'll do:") < body.index("Bonus points")

    # "Hands-on and scrappy, ..." is a bullet, not a field called "Hands".
    assert "Hands" not in document.advert.fields
    assert document.advert.fields == {}
    assert "<ul>" in document.advert.body_html
    assert "Hands-on and scrappy" in document.advert.body_html


def test_real_multi_role_document_is_flagged():
    """One document holding several roles' sequences is not the supported shape.

    The parser must still not lose anything: every email is kept in document
    order and the repeated numbering is reported, so the recruiter sees why the
    row needs splitting.
    """
    document = _parse("real-multi-role-emails.docx")

    assert len(document.emails) == 6
    assert [e.order for e in document.emails] == [1, 1, 2, 2, 3, 3]
    assert all(e.body_text.strip() for e in document.emails)
    assert any("are both numbered" in w for w in document.warnings)

    # None of these emails has a Subject: line and every one opens "Hi {{name}},".
    # The greeting stays in the body and the role title becomes the subject.
    first = document.emails[0]
    assert first.body_text.startswith("Hi {{name}},")
    assert first.subject.startswith("Senior Platform Engineer / AWS, Terraform")
    assert any("advert title was used" in w for w in document.warnings)


def test_field_line_needs_a_colon_or_a_spaced_dash():
    from app.models import Block

    def field(text: str):
        return parser._field_from(Block("body", 0, text, f"<p>{text}</p>"))[0]

    assert field("Location: Manchester") == "Location"
    assert field("Location - Manchester") == "Location"
    assert field("Location – Manchester") == "Location"
    assert field("Hands-on and scrappy, comfortable with minimal process") is None
    assert field("Self-starter who owns outcomes") is None


def test_channel_headings_become_steps_with_their_channel():
    """A sequence mixes channels under sibling headings.

    `LinkedIn Connection`, `InMail` and `Wellfound` are steps, not prose that
    belongs to the email above them. Before channels existed the InMail and
    Wellfound copy was silently swallowed into the preceding email.
    """
    channel_of = parser._channel_of

    assert channel_of("LinkedIn Connection") == "linkedin"
    assert channel_of("LinkedIn Connection Request") == "linkedin"
    assert channel_of("linkedin note") == "linkedin"
    assert channel_of("LinkedIn") == "linkedin"
    assert channel_of("InMail") == "inmail"
    assert channel_of("In-Mail 2") == "inmail"
    # Matches the linkedin pattern too; the narrower reading has to win.
    assert channel_of("LinkedIn InMail") == "inmail"
    assert channel_of("Wellfound") == "wellfound"
    assert channel_of("AngelList") == "wellfound"

    assert channel_of("Email1") is None
    assert channel_of("Follow up") is None
    assert channel_of("The Role") is None


def test_email_heading_without_a_separator():
    """"Email1" is as common as "Email 1" and must still yield order 1."""
    assert parser._is_email_heading("Email1")
    assert parser._EMAIL_HEADING.match("Email1").group(1) == "1"
    assert parser._EMAIL_HEADING.match("Email 2").group(1) == "2"


def test_steps_are_numbered_within_their_channel():
    """Numbering is per channel, so a mixed document has no colliding #1s.

    Ordering across channels stays document order: sorting globally by `order`
    would interleave a LinkedIn note into the middle of the email sequence.
    """
    from app.models import Block

    def heading(text: str) -> Block:
        return Block("heading", 2, text, f"<h2>{text}</h2>")

    def body(text: str) -> Block:
        return Block("body", 0, text, f"<p>{text}</p>")

    document = parser.parse_document([
        heading("LinkedIn Connection"), body("Hi there, connecting."),
        heading("Subject"), body("A shared subject line"),
        heading("Email1"), body("Hi there, first email."),
        heading("Email2"), body("Hi there, second email."),
        heading("InMail"), body("Hi there, an InMail."),
    ])

    assert [(e.channel, e.order) for e in document.emails] == [
        ("linkedin", 1), ("email", 1), ("email", 2), ("inmail", 1),
    ]
    assert [e.label for e in document.emails] == [
        "LinkedIn Connection", "Email1", "Email2", "InMail",
    ]
    # Two steps numbered 1 in different channels is not a collision.
    assert not any("are both numbered" in w for w in document.warnings)


def test_shared_subject_heading_applies_to_emails_only():
    """A standalone "Subject" heading is the subject for the email steps.

    It is not a subject for a LinkedIn note, which has no subject field, and
    the block must not be left to fall through into an email body.
    """
    from app.models import Block

    def heading(text: str) -> Block:
        return Block("heading", 2, text, f"<h2>{text}</h2>")

    def body(text: str) -> Block:
        return Block("body", 0, text, f"<p>{text}</p>")

    document = parser.parse_document([
        heading("LinkedIn Connection"), body("Hi there, connecting."),
        heading("Subject"), body("Staff Platform Engineer / up to $350k"),
        heading("Email1"), body("Hi there, first email."),
        heading("Email2"), body("Hi there, second email."),
    ])

    by_channel = {e.channel: e for e in document.emails}
    assert by_channel["email"].subject == "Staff Platform Engineer / up to $350k"
    assert all(
        e.subject == "Staff Platform Engineer / up to $350k"
        for e in document.emails if e.is_email
    )
    assert by_channel["linkedin"].subject != "Staff Platform Engineer / up to $350k"
    # The subject text is consumed, not repeated inside an email body.
    assert "up to $350k" not in by_channel["email"].body_text


def test_fenced_lines_are_headings():
    """`=== Email 1 (Day 1 - Anonymous) ===` marks a section in generator-made
    documents that use no Word heading styles and no bold. The fences are
    decoration; the text inside is the heading, and the day number is the delay.
    """
    from app.documents.docx_reader import _promote_pseudo_headings
    from app.models import Block

    def body(text: str) -> Block:
        return Block("body", 0, text, f"<p>{text}</p>")

    blocks = [
        body("=== Email 1 (Day 1 - Anonymous) ==="),
        body("Subject: Platform Engineer / AWS"),
        body("Hi {{first_name}}, first email."),
        body("=== Email 2 (Day 3) ==="),
        body("Hi {{first_name}}, second email."),
    ]
    _promote_pseudo_headings(blocks)
    assert [b.text for b in blocks if b.is_heading] == [
        "Email 1 (Day 1 - Anonymous)", "Email 2 (Day 3)",
    ]

    document = parser.parse_document(blocks)
    assert [(e.order, e.delay_days) for e in document.emails] == [(1, 1), (2, 3)]
    assert document.emails[0].subject == "Platform Engineer / AWS"
