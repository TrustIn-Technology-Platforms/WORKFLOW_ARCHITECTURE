"""Parser against real documents.

The synthetic fixtures prove the mechanism; these two came out of a recruiter's
Downloads folder and prove the fit. Each fixture is a different shape from the
one the parser was designed against, which is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.documents import parser
from app.documents.docx_reader import read_blocks
from app.models import Block

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _parse(name: str):
    return parser.parse_document(read_blocks((FIXTURES / name).read_bytes()))


def _block(text: str, *, style: str = "body", level: int = 0) -> Block:
    return Block(style=style, level=level, text=text, html=f"<p>{text}</p>")


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


def test_generator_vocabulary_and_merge_tripwire():
    """The AI-generator dialect: "(Day N)" suffixes on channel headings,
    "Connect" for the connection request, and "Ad · <site>" advert sections.
    Live failure 2026-08-28: "InMail (Day 5)" was not recognised and its copy
    was absorbed into Email 2 - two messages posted as one email.
    """
    channel_of = parser._channel_of
    assert channel_of("InMail (Day 5)") == "inmail"
    assert channel_of("Connect (Day 7)") == "linkedin"
    assert channel_of("Connection request") == "linkedin"
    assert channel_of("Wellfound (Anonymised)") == "wellfound"

    from app.models import Block

    def h(t):
        return Block("heading", 2, t, f"<h2>{t}</h2>")

    def b(t):
        return Block("body", 0, t, f"<p>{t}</p>")

    document = parser.parse_document([
        h("Email 1 (Day 1)"), b("Hi {{first_name}}, first."),
        h("Email 2 (Day 3)"), b("Hi {{first_name}}, second."),
        h("InMail (Day 5)"), b("Hi {{first_name}}, the inmail."),
        h("Connect (Day 7)"), b("Hi {{first_name}}, connecting."),
        h("Ad · LinkedIn (Anonymised)"), b("Title: Platform Engineer"), b("About the role."),
    ])
    assert [(e.channel, e.order, e.delay_days) for e in document.emails] == [
        ("email", 1, 1), ("email", 2, 3), ("inmail", 1, 5), ("linkedin", 1, 7),
    ]
    # Nothing merged: exactly one greeting per step, and the ad went to the advert.
    for e in document.emails:
        assert sum(1 for l in e.body_text.splitlines() if l.startswith("Hi ")) == 1
    assert document.advert is not None
    assert "About the role." in document.advert.body_text
    assert not any("merged" in w for w in document.warnings)

    # An unknown heading between emails is still absorbed - but no longer silently.
    merged = parser.parse_document([
        h("Email 1"), b("Hi {{first_name}}, first."),
        h("Mystery Section (Day 4)"), b("Hi {{first_name}}, a stray message."),
    ])
    assert any("merged into one" in w for w in merged.warnings)


# ----------------------------------------------------------------------
# a dash-separated job title is a title, not a field
# ----------------------------------------------------------------------

# TrustIn's live adverts, verbatim. Every one of these is the first line of a
# real document, and seven of the nine used to be read as advert metadata -
# which silently cost the advert its title. Found 2026-08-31 by reading a saved
# Wellfound draft back and finding it titled "About Company:".
REAL_TITLES = [
    "Backend Platform Engineer - NYC / AI Infrastructure Startup / Series A / Kubernetes",
    "Backend Infrastructure Engineer - AI Startup - AWS, Python, K8s, Terraform, FastAPI",
    "Distributed Systems Engineer - Open Source Runtime for AI - Node, TS, Python, GCP",
    "Platform Engineering Leader (Hands-on) - AI + Accounting Agents",
    "Senior Infrastructure Engineer - Founding Team / AI Observability Startup",
    "Staff Platform Engineer - RL - LLM, Python, AWS, AI - SF",
    "Cloud DevSecOps Engineer / Onsite / Blockchain, CI/CD, Cloud, Vaults",
    "Platform Engineer / AWS, Kubernetes, GPU Infra / SF / causal AI platform",
]


@pytest.mark.parametrize("line", REAL_TITLES)
def test_a_dash_in_a_job_title_does_not_make_it_a_field(line):
    document = parser.parse_document(
        [
            _block(line),
            _block("About Company:", style="heading", level=2),
            _block("An early-stage AI company."),
            _block("What we are looking for:", style="heading", level=2),
            _block("5+ years of Kubernetes."),
        ]
    )
    assert document.advert is not None
    assert document.advert.title == line
    assert document.advert.fields == {}, "nothing in the title became metadata"


@pytest.mark.parametrize(
    "line, label, value",
    [
        ("Location - San Francisco", "Location", "San Francisco"),
        ("Salary - $200k", "Salary", "$200k"),
        ("Employment Type - Permanent", "Employment Type", "Permanent"),
        ("Ref - TRN-4821", "Ref", "TRN-4821"),
    ],
)
def test_a_dash_still_labels_a_field_we_recognise(line, label, value):
    """The names that map onto advert fields keep working with a dash. Only an
    unrecognised label needs the colon, which is the documented form anyway."""
    document = parser.parse_document(
        [
            _block("Platform Engineer", style="heading", level=1),
            _block(line),
            _block("We are hiring."),
        ]
    )
    advert = document.advert
    assert advert is not None
    combined = dict(advert.fields)
    for attribute in ("location", "salary", "employment_type", "reference"):
        if getattr(advert, attribute):
            combined[label] = getattr(advert, attribute)
    assert combined.get(label) == value


# ----------------------------------------------------------------------
# the client's job description
# ----------------------------------------------------------------------


def _heading(text: str, level: int = 1) -> Block:
    return Block(style="heading", level=level, text=text, html=f"<h{level}>{text}</h{level}>")


def _document_with_jd(jd_heading: str = "Client JD"):
    return parser.parse_document([
        _heading("Job Advert"),
        _block("Join a team that ships. We move fast and look after each other."),
        _heading("Email 1 (Day 1)"),
        _block("Subject: Platform Engineer"),
        _block("Hi {{first_name}}, we are hiring."),
        _heading("Email 2 (Day 3)"),
        _block("Hi {{first_name}}, following up."),
        _heading(jd_heading),
        _block("Kepler Systems is hiring a Senior Platform Engineer in Manchester."),
        _heading("Requirements", level=2),
        _block("8+ years of production Kubernetes.", style="list_bullet"),
        _block("Must hold the right to work in the UK - no sponsorship.", style="list_bullet"),
        _heading("The Role", level=2),
        _block("Own the platform end to end."),
    ])


def test_the_client_jd_section_is_kept_whole_and_out_of_everything_else():
    """The one text the recruiter controls that states what a search needs.

    Its own headings travel with it: "Requirements" above a bullet list is what
    makes the list mean anything, and neither it nor "The Role" may be read as
    an advert section or appended to the email above.
    """
    document = _document_with_jd()

    assert document.client_jd.startswith("Kepler Systems is hiring")
    assert "Requirements" in document.client_jd
    assert "8+ years of production Kubernetes." in document.client_jd
    assert "no sponsorship" in document.client_jd
    # "The Role" matches the advert heading pattern; inside the JD it is JD.
    assert "The Role" in document.client_jd
    assert "Own the platform end to end." in document.client_jd
    # The heading that opened the section is not part of the client's words.
    assert not document.client_jd.startswith("Client JD")

    # Nothing leaked upwards.
    assert document.advert is not None
    assert "Kubernetes" not in document.advert.body_text
    assert "Own the platform" not in document.advert.body_text
    assert len(document.emails) == 2
    assert "sponsorship" not in document.emails[-1].body_text
    assert document.emails[-1].body_text == "Hi {{first_name}}, following up."


def test_the_search_reads_the_client_jd_and_falls_back_to_the_advert():
    """`job_description` is what every sourcing platform is handed."""
    document = _document_with_jd()
    assert document.job_description == document.client_jd

    without = parser.parse_document([
        _heading("Job Advert"),
        _block("Join a team that ships."),
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
    ])
    assert without.client_jd == ""
    assert without.job_description == "Join a team that ships."


@pytest.mark.parametrize("heading", ["Client JD", "Full JD", "Original JD", "Job Spec", "JD"])
def test_the_headings_a_recruiter_might_write_all_open_the_section(heading):
    document = _document_with_jd(heading)
    assert "8+ years of production Kubernetes." in document.client_jd


def test_job_spec_above_the_sequence_is_still_the_advert():
    """`Job Spec` names the advert at the top and the client's spec at the
    bottom, so position settles it rather than the word.
    """
    document = parser.parse_document([
        _heading("Job Spec"),
        _block("Join a team that ships."),
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
    ])
    assert document.client_jd == ""
    assert document.advert is not None
    assert "Join a team that ships." in document.advert.body_text


def test_a_client_jd_in_the_wrong_place_is_reported_not_guessed_at():
    """Read as the JD it would swallow the rest of the sequence, so it is not."""
    document = parser.parse_document([
        _heading("Job Advert"),
        _block("Join a team that ships."),
        _heading("Client JD"),
        _block("8+ years of production Kubernetes."),
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
    ])
    assert document.client_jd == ""
    assert len(document.emails) == 1
    assert any("Move that section to the end" in w for w in document.warnings)


def test_an_empty_client_jd_section_says_so_and_falls_back():
    document = parser.parse_document([
        _heading("Job Advert"),
        _block("Join a team that ships."),
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
        _heading("Client JD"),
    ])
    assert document.client_jd == ""
    assert document.job_description == "Join a team that ships."
    assert any("is empty" in w for w in document.warnings)
