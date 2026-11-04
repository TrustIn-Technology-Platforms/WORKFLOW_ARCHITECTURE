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

    `LinkedIn Connection` and `InMail` are steps, not prose belonging to the
    email above them. Before channels existed their copy was silently swallowed
    into the preceding email.

    `Wellfound` is deliberately NOT one of them - see
    `test_a_board_section_is_that_boards_advert_not_a_message`.
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
    # A board's name is not a message channel. It names an advert.
    assert channel_of("Wellfound") is None
    assert channel_of("AngelList") is None

    assert channel_of("Email1") is None
    assert channel_of("Follow up") is None
    assert channel_of("The Role") is None


def test_the_if_accepted_message_is_its_own_channel():
    """noon's "if accepted" LinkedIn message has a section of its own, so it is
    not the connection note sent a second time (2026-11-03)."""
    channel_of = parser._channel_of

    for heading in ("If Accepted", "LinkedIn If Accepted", "LinkedIn Accepted",
                    "Connection Accepted", "Accepted", "Once Accepted",
                    "If Accepted (wait 30 min)", "Accepted message",
                    # noon's own word for it; written as the heading on
                    # 2026-11-04 and merged into the InMail above it.
                    "Trigger", "Trigger message", "LinkedIn Trigger"):
        assert channel_of(heading) == "accepted", heading
    # The connection request itself is still the note.
    assert channel_of("Connection Request") == "linkedin"
    assert channel_of("Connect") == "linkedin"


def test_an_accepted_section_is_a_step_not_part_of_the_note():
    from app.models import Block

    def heading(text: str) -> Block:
        return Block("heading", 2, text, f"<h2>{text}</h2>")

    def body(text: str) -> Block:
        return Block("body", 0, text, f"<p>{text}</p>")

    document = parser.parse_document([
        heading("LinkedIn Connection"), body("Hi there, connecting."),
        heading("If Accepted"), body("Hi there, thanks for connecting."),
        heading("Subject"), body("A shared subject line"),
        heading("Email1"), body("Hi there, first email."),
    ])

    assert [(e.channel, e.label) for e in document.emails] == [
        ("linkedin", "LinkedIn Connection"), ("accepted", "If Accepted"), ("email", "Email1"),
    ]
    accepted = document.emails[1]
    assert accepted.body_text == "Hi there, thanks for connecting."
    # LinkedIn messages have no subject field.
    assert accepted.subject == ""


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


def test_inmail_without_its_own_subject_reuses_the_email_subject():
    """noon gives the InMail step a subject box. A heading's name is not a
    subject - "InMail" was posted as one on 2026-09-02 - so an InMail with no
    Subject: line of its own goes out under the emails' subject, whether that
    was written once under a shared "Subject" heading or on Email 1. Its own
    Subject: line still wins.
    """
    from app.models import Block

    def heading(text: str) -> Block:
        return Block("heading", 2, text, f"<h2>{text}</h2>")

    def body(text: str) -> Block:
        return Block("body", 0, text, f"<p>{text}</p>")

    shared = parser.parse_document([
        heading("Subject"), body("Staff Platform Engineer / up to $350k"),
        heading("Email 1 (Day 1)"), body("Hi {{first_name}}, first email."),
        heading("Email 2 (Day 3)"), body("Hi {{first_name}}, second email."),
        heading("InMail (Day 5)"), body("Hi {{first_name}}, the InMail."),
        heading("Connect (Day 7)"), body("Quick note, no greeting."),
    ])
    steps = {e.channel: e for e in shared.emails}
    assert steps["inmail"].subject == "Staff Platform Engineer / up to $350k"
    assert steps["inmail"].body_text == "Hi {{first_name}}, the InMail."
    # A connection note has no subject field anywhere: nothing is invented for
    # it, its first line stays in the note, and no warning is raised.
    assert steps["linkedin"].subject == ""
    assert steps["linkedin"].body_text == "Quick note, no greeting."
    assert not any("no subject" in w for w in shared.warnings), shared.warnings

    on_email_1 = parser.parse_document([
        heading("Email 1 (Day 1)"), body("Subject: Platform Engineer / AWS"),
        body("Hi {{first_name}}, first email."),
        heading("InMail (Day 5)"), body("Hi {{first_name}}, the InMail."),
    ])
    assert on_email_1.emails[1].channel == "inmail"
    assert on_email_1.emails[1].subject == "Platform Engineer / AWS"

    own = parser.parse_document([
        heading("Subject"), body("Staff Platform Engineer / up to $350k"),
        heading("Email 1 (Day 1)"), body("Hi {{first_name}}, first email."),
        heading("InMail (Day 5)"), body("Subject: A different angle"),
        body("Hi {{first_name}}, the InMail."),
    ])
    assert own.emails[1].subject == "A different angle"
    assert "different angle" not in own.emails[1].body_text


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
    assert parser._platform_advert_of("Wellfound (Anonymised)") == "wellfound"

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
        assert sum(1 for line in e.body_text.splitlines() if line.startswith("Hi ")) == 1
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


def test_a_job_description_sub_heading_stays_in_the_wellfound_advert_when_the_original_follows():
    """The recruiters' template: `Wellfound`, then a `Job Description`
    sub-heading inside it, then `Original Job Description` holding the client's
    spec. The Wellfound post runs to the `Original Job Description` heading; the
    bare `Job Description` is neither the client's JD nor a second advert
    (2026-11-05).
    """
    document = parser.parse_document([
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
        _heading("Wellfound"),
        _block("Platform Engineer / Seed / NY / up to $250K + equity"),
        _block("Opening line of the advert."),
        _heading("Job Description"),
        _block("You will own the platform."),
        _heading("Original Job Description"),
        _block("8+ years of production Kubernetes."),
        _heading("Requirements"),
        _block("Manchester, no sponsorship."),
    ])
    wellfound = document.platform_adverts["wellfound"].body_text
    assert "Opening line of the advert." in wellfound
    assert "Job Description" in wellfound
    assert "You will own the platform." in wellfound
    assert "8+ years" not in wellfound
    assert document.advert is None
    assert document.client_jd.startswith("8+ years")
    assert "Original Job Description" not in document.client_jd
    assert "Requirements" in document.client_jd
    assert not any("Move that section" in w for w in document.warnings)


def test_a_bare_job_description_after_the_sequence_is_still_the_jd_when_nothing_explicit_follows():
    document = parser.parse_document([
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
        _heading("Wellfound"),
        _block("Opening line of the advert."),
        _heading("Job Description"),
        _block("8+ years of production Kubernetes."),
    ])
    assert document.client_jd.startswith("8+ years")
    assert "8+ years" not in document.platform_adverts["wellfound"].body_text


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


def test_a_document_with_a_client_jd_and_no_advert_still_has_something_to_search_on():
    """An emails-only document is valid, and the JD is then the only description
    of the job the platforms get - so `job_description` must not depend on an
    advert existing.
    """
    document = parser.parse_document([
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
        _heading("Client JD"),
        _block("8+ years of production Kubernetes. Manchester, no sponsorship."),
    ])
    assert document.advert is None
    assert document.job_description.startswith("8+ years")


# ----------------------------------------------------------------------
# a board's own advert
# ----------------------------------------------------------------------


def _multi_channel_document() -> list:
    """The real shape: general advert, sequence, then a Wellfound advert."""
    return [
        _block("Backend Platform Engineer - NYC / Series A / Kubernetes"),
        _block("Job Advert", style="heading", level=2),
        _block("The long advert, written for the client and LinkedIn."),
        _block("Email 1", style="heading", level=2),
        _block("Subject: Hello"),
        _block("Hi {first_name}, we are hiring."),
        _block("InMail", style="heading", level=2),
        _block("Short InMail copy."),
        _block("Wellfound", style="heading", level=2),
        _block("Anonymised copy, cut for Wellfound."),
        _block("What you'll do:", style="heading", level=3),
        _block("Run the platform."),
    ]


def test_a_board_section_is_that_boards_advert_not_a_message():
    """`Wellfound` names the advert written for that board.

    It used to parse as an outreach step with `channel: wellfound`, on the
    assumption it meant a message through Wellfound's messaging. Nothing ever
    read that channel, so it cost nothing until Wellfound started posting - at
    which point the general advert went up and the copy written for the board
    was silently dropped. Reported by Sohaib, 2026-08-31.
    """
    document = parser.parse_document(_multi_channel_document())

    assert "wellfound" in document.platform_adverts
    assert [e.channel for e in document.emails] == ["email", "inmail"], (
        "the board section must not become a step, and must not disturb the ones "
        "that are"
    )

    wellfound = document.advert_for("wellfound")
    assert "Anonymised copy, cut for Wellfound." in wellfound.body_text
    assert "Run the platform." in wellfound.body_text
    assert "What you'll do:" in wellfound.body_text, "its sub-headings stay with it"
    assert "long advert" not in wellfound.body_text


def test_the_board_section_does_not_leak_into_the_general_advert():
    """noon, Loxo and Juicebox must still get the general advert."""
    document = parser.parse_document(_multi_channel_document())

    assert document.advert is not None
    assert "The long advert" in document.advert.body_text
    assert "Anonymised copy" not in document.advert.body_text
    for platform in ("noon", "loxo", "juicebox"):
        assert document.advert_for(platform) is document.advert


def test_a_board_advert_inherits_what_it_does_not_restate():
    """Board copy is copy, not a metadata sheet: it rarely repeats the title and
    never repeats the salary. Its first line is the advert's opening sentence,
    not a title - promoting it would both lose the line and title the post with
    it."""
    document = parser.parse_document(_multi_channel_document())
    wellfound = document.advert_for("wellfound")

    assert wellfound.title == document.advert.title
    assert wellfound.body_text.startswith("Anonymised copy")


def test_a_document_with_no_board_section_is_unchanged():
    document = parser.parse_document(
        [
            _block("Platform Engineer", style="heading", level=1),
            _block("The advert."),
            _block("Email 1", style="heading", level=2),
            _block("Hi there."),
        ]
    )
    assert document.platform_adverts == {}
    assert document.advert_for("wellfound") is document.advert


def test_an_empty_board_section_falls_back_and_says_so():
    """Better the general advert than a blank post, but never silently."""
    document = parser.parse_document(
        [
            _block("Platform Engineer", style="heading", level=1),
            _block("The advert."),
            _block("Wellfound", style="heading", level=2),
        ]
    )
    assert document.advert_for("wellfound") is document.advert
    assert any("wellfound" in w.lower() for w in document.warnings)


def test_board_heading_spellings():
    for heading in (
        "Wellfound",
        "wellfound:",
        "Wellfound (Anonymised)",
        "Ad - Wellfound",
        "Ad · Wellfound",
        "Wellfound Ad",
        "Wellfound Job Post",
        "AngelList",
    ):
        assert parser._platform_advert_of(heading) == "wellfound", heading

    for heading in ("Email 1", "InMail", "LinkedIn", "The Role", "Client JD"):
        assert parser._platform_advert_of(heading) is None, heading


def test_prose_with_a_colon_stays_in_the_body():
    """A sentence is not a field, however short its opening clause.

    "Run the core infrastructure: services, data pipelines and distributed
    AI/ML workloads on AWS" passes every other rule - four words, under forty
    characters - and was being lifted out of the advert into a field nothing
    reads, so the line vanished from the post. Found 2026-08-31 while checking
    that a Wellfound section survived parsing intact.
    """
    document = parser.parse_document(
        [
            _block("Platform Engineer", style="heading", level=1),
            _block("Run the core infrastructure: services, data pipelines and "
                   "distributed AI/ML workloads on AWS."),
            _block("Own CI/CD: automated testing, rollback and release gating."),
        ]
    )
    advert = document.advert
    assert advert is not None
    assert "Run the core infrastructure" in advert.body_text
    assert "Own CI/CD" in advert.body_text
    assert advert.fields == {}


def test_real_metadata_is_still_read_however_it_is_written():
    """The named fields keep no length cap - a location legitimately runs long."""
    document = parser.parse_document(
        [
            _block("Platform Engineer", style="heading", level=1),
            _block("Location: San Francisco Bay Area, or remote within the United States"),
            _block("Salary: $200,000 - $260,000 plus equity"),
            _block("Start Date: ASAP"),
            _block("We are hiring."),
        ]
    )
    advert = document.advert
    assert advert is not None
    assert advert.location == "San Francisco Bay Area, or remote within the United States"
    assert advert.salary == "$200,000 - $260,000 plus equity"
    assert advert.fields.get("Start Date") == "ASAP"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Senior Platform Engineer / AWS / SF / up to $225k + Equity", "up to $225k"),
        ("Backend Platform Engineer - NYC / Series A / Up to $300k", "Up to $300k"),
        ("Platform Engineer / Hedge Fund / London / up to £250k + bonus", "up to £250k"),
        ("Staff Engineer / NY / $200 - 260k/yr", "$200 - 260k/yr"),
        ("Infrastructure Engineer / $200,000 - $260,000 / Onsite", "$200,000 - $260,000"),
    ],
)
def test_a_salary_in_the_title_reaches_the_advert(title, expected):
    """The titles carry the package by convention and the documents rarely
    state it anywhere else - the Notion board has no Salary column, so without
    this the boards get no salary at all (first real Wellfound draft,
    2026-09-01)."""
    document = parser.parse_document(
        [_block(title), _block("About Company:", style="heading", level=2), _block("An AI startup.")]
    )
    assert document.advert is not None
    assert document.advert.salary == expected


def test_a_funding_round_is_not_a_salary():
    document = parser.parse_document(
        [
            _block("Platform Engineer / Backed by a $120M round / SF"),
            _block("About Company:", style="heading", level=2),
            _block("An AI startup."),
        ]
    )
    assert document.advert is not None
    assert document.advert.salary is None


def test_a_labelled_salary_line_beats_the_title():
    document = parser.parse_document(
        [
            _block("Platform Engineer / up to $300k"),
            _block("Salary: $250,000 - $280,000"),
            _block("We are hiring."),
        ]
    )
    assert document.advert is not None
    assert document.advert.salary == "$250,000 - $280,000"


def test_a_board_advert_inherits_the_title_salary():
    """Wellfound reads its own advert; the salary lives in the document title."""
    document = parser.parse_document(
        [
            _block("Platform Engineer / SF / up to $260k"),
            _block("Job Advert", style="heading", level=2),
            _block("The general advert."),
            _block("Wellfound", style="heading", level=2),
            _block("Anonymised board copy."),
        ]
    )
    board = document.advert_for("wellfound")
    assert board.salary == "up to $260k"


def test_a_board_section_opening_with_a_title_line_gets_it_as_its_title():
    """The recruiters open the board section with a TrustIn-convention title -
    "Role / Company / Place / up to $250K + equity". Left in the body, the post
    went out titled with the inherited fallback and without the salary that
    line carries (first real drafts, 2026-09-01)."""
    line = "Staff Platform Engineer / Agentic AI Platform / South Bay / up to $250K + equity"
    document = parser.parse_document(
        [
            _block("Job Description", style="heading", level=2),
            _block("The general advert prose."),
            _block("Wellfound", style="heading", level=2),
            _block(line),
            _block("A Series A SRE Startup, backed by a $30M round, is hiring."),
        ]
    )
    board = document.advert_for("wellfound")
    assert board.title == line
    assert board.salary == "up to $250K"
    assert line not in board.body_text, "the title is not repeated as body line one"
    assert "Series A SRE Startup" in board.body_text
    assert "$30M" in board.body_text, "the funding line stays prose, never a salary"


def test_a_board_section_opening_with_prose_keeps_it_in_the_body():
    document = parser.parse_document(
        [
            _block("Title / Advert", style="heading", level=1),
            _block("General advert."),
            _block("Wellfound", style="heading", level=2),
            _block("We are hiring a founding engineer to build CI/CD tooling."),
            _block("You will own the platform."),
        ]
    )
    board = document.advert_for("wellfound")
    assert "founding engineer" in board.body_text, "a sentence is not a title"
    assert board.title == "Title / Advert", "title still inherited from the document"


def test_a_trailing_job_description_heading_is_the_client_jd():
    """The recruiters paste the client's spec under the heading they'd naturally
    write - `Job Description` - at the end. At the top of a document that
    heading is the advert; after the last message it is the client's JD, and
    reading it as an advert named noon roles "Job Description" (2026-09-01)."""
    document = parser.parse_document(
        [
            _block("LinkedIn Connection", style="heading", level=2),
            _block("Hi {first_name}, quick note."),
            _block("Email 1", style="heading", level=2),
            _block("Subject: A role"),
            _block("Hi {first_name}, the body."),
            _block("Wellfound", style="heading", level=2),
            _block("Role / Startup / NY / up to $250K"),
            _block("Board copy for Wellfound."),
            _block("About You", style="heading", level=2),
            _block("You have shipped systems."),
            _block("Job Description", style="heading", level=2),
            _block("8+ years with Kafka and vector databases."),
            _block("Run reliability for a multi-tenant SaaS system."),
        ]
    )
    assert len(document.emails) == 2, "the sequence is untouched"
    assert document.client_jd is not None
    assert "Kafka and vector databases" in document.client_jd
    board = document.advert_for("wellfound")
    assert "Board copy for Wellfound" in board.body_text
    assert "You have shipped systems" in board.body_text, "About You stays board copy"
    assert "Kafka and vector databases" not in board.body_text, "the JD is not advert copy"
    assert document.job_description == document.client_jd


def test_a_leading_job_description_heading_is_still_the_advert():
    document = parser.parse_document(
        [
            _block("Job Description", style="heading", level=2),
            _block("We are hiring a platform engineer to build infrastructure."),
            _block("Email 1", style="heading", level=2),
            _block("Subject: Hello"),
            _block("Hi {first_name}."),
        ]
    )
    assert document.advert is not None
    assert "platform engineer" in document.advert.body_text
    assert not document.client_jd


# ----------------------------------------------------------------------
# a document whose only advert is a board section
# ----------------------------------------------------------------------


def _board_only_document(*, with_jd: bool):
    blocks = [
        _heading("Email 1 (Day 1)"),
        _block("Subject: Platform Engineer"),
        _block("Hi {{first_name}}, worth a chat?"),
        _heading("Wellfound"),
        _block("Anonymised board copy for Wellfound."),
    ]
    if with_jd:
        blocks += [
            _heading("Client JD"),
            _block("8+ years of production Kubernetes. Manchester, no sponsorship."),
        ]
    return parser.parse_document(blocks)


def test_a_wellfound_section_is_an_advert_not_an_absence():
    """The shape that shipped on 2026-09-01: emails plus a `Wellfound` section,
    no general advert. It was warned about as "emails only", which reads as
    "Wellfound has nothing to post" - the opposite of the truth. The board
    section posts; what such a document actually lacks is a job description for
    the sourcing criteria, and the warning must say that instead.
    """
    document = _board_only_document(with_jd=False)

    board = document.advert_for("wellfound")
    assert board is not None
    assert board.body_text == "Anonymised board copy for Wellfound."
    assert not document.is_empty
    assert not any("emails only" in w for w in document.warnings)
    assert any("Client JD" in w for w in document.warnings)


def test_a_board_section_plus_a_client_jd_is_a_complete_document():
    """Everything has the text it needs, so nothing is worth a warning: the
    board posts its section and the criteria read the JD.
    """
    document = _board_only_document(with_jd=True)

    assert document.advert_for("wellfound") is not None
    assert document.job_description.startswith("8+ years")
    assert document.warnings == []


def test_a_board_advert_alone_is_not_an_empty_document():
    """A document holding only a `Wellfound` section still has something to
    post; `is_empty` failing the row would be wrong twice over.
    """
    document = parser.parse_document([
        _heading("Wellfound"),
        _block("Anonymised board copy."),
    ])
    assert not document.is_empty


def test_an_emails_only_document_without_a_jd_says_what_to_paste():
    document = parser.parse_document([
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
    ])
    assert any("emails only" in w for w in document.warnings)
    assert any("Client JD" in w for w in document.warnings)


# ----------------------------------------------------------------------
# a document with no formatting at all
# ----------------------------------------------------------------------


def test_an_unformatted_document_still_finds_its_sections():
    """A real document arrived as 52 plain-body blocks - no Heading styles,
    nothing bold - and produced zero email steps, failing all three sequence
    platforms at once (2026-09-01). The section names were there; only the
    formatting was missing. A bare line that entirely matches a known section
    name is a heading in a document that has no other kind."""
    document = parser.parse_document(
        [
            _block("LinkedIn Connection"),
            _block("Hi {{first_name}},"),
            _block("I am sourcing an engineer for a YC-backed company."),
            _block("Subject"),
            _block("Infrastructure Engineer / AWS / NYC / up to $250K"),
            _block("Email 1"),
            _block("Hi {{first_name}},"),
            _block("The first email body."),
            _block("Email 2"),
            _block("The second email body."),
            _block("InMail"),
            _block("The InMail body."),
            _block("Wellfound"),
            _block("Engineer / AWS / NYC / up to $250K"),
            _block("Board copy for Wellfound."),
            _block("Job Description"),
            _block("8+ years with Kubernetes. SOC 2 experience."),
        ]
    )
    channels = [(e.channel, e.order) for e in document.emails]
    assert channels == [("linkedin", 1), ("email", 1), ("email", 2), ("inmail", 1)]
    assert all(e.subject for e in document.emails if e.is_email), "shared Subject applied"
    assert document.client_jd and "Kubernetes" in document.client_jd
    board = document.advert_for("wellfound")
    assert "Board copy" in board.body_text
    assert board.salary == "up to $250K"


def test_a_section_name_on_its_own_line_is_a_section_even_beside_real_headings():
    """Headings styled inconsistently - one a Word heading, the next a plain or
    bold line - used to merge every later section into the one above it
    (2026-11-03). A line that is a section name and nothing else is a section
    however it is formatted; prose that mentions one is still prose."""
    document = parser.parse_document(
        [
            _block("Email 1", style="heading", level=2),
            _block("Subject: Hello"),
            _block("See Email 2 below for details."),
            _block("Email 2"),
            _block("Hi there, the second email."),
        ]
    )
    assert len(document.emails) == 2
    assert "See Email 2 below for details." in document.emails[0].body_text
    assert document.emails[1].body_text == "Hi there, the second email."


def test_the_client_jd_keeps_lines_that_look_like_section_names():
    """Everything below the Client JD heading is the client's text, verbatim -
    a spec with its own "Subject" or "Email" line is not split."""
    document = parser.parse_document(
        [
            _block("Email 1", style="heading", level=2),
            _block("Hi there, the email."),
            _block("Client JD"),
            _block("About the role"),
            _block("Email"),
            _block("Subject"),
            _block("Build the platform."),
        ]
    )
    assert len(document.emails) == 1
    for line in ("About the role", "Email", "Subject", "Build the platform."):
        assert line in document.client_jd


def test_a_bold_sub_heading_inside_the_advert_stays_inside_it():
    """Only the document's own section names are promoted below real headings:
    "The Role" or "Requirements" in the Wellfound advert is the advert's."""
    document = parser.parse_document(
        [
            _block("Wellfound", style="heading", level=2),
            _block("Founding Engineer / Seed / SF / up to $200k + equity"),
            _block("The Role"),
            _block("Build the platform."),
            _block("Requirements"),
            _block("5+ years."),
            _block("Client JD", style="heading", level=2),
            _block("The spec."),
        ]
    )
    body = document.advert_for("wellfound").body_text
    assert "The Role" in body and "Requirements" in body and "5+ years." in body


def test_prose_lines_are_not_promoted_in_an_unformatted_document():
    document = parser.parse_document(
        [
            _block("Email 1"),
            _block("Subject: A role"),
            _block("Email me at sohaib@trust-in.co.uk if this is interesting."),
            _block("Touch base soon."),
        ]
    )
    assert len(document.emails) == 1
    body = document.emails[0].body_text
    assert "Email me at" in body and "Touch base soon." in body


def _template_path() -> Path:
    return Path(__file__).parent.parent / "docs" / "templates" / "sequence-document-template.docx"


def test_the_recruiter_template_has_every_section_in_order():
    """The template handed to recruiters: one heading per section, one paste
    slot under each. Rebuild it with scripts/build_sequence_template.py.

    Every slot holds a bracketed prompt ("[Email 1 body.]"), so the parser sees
    a body under each heading and raises no warning. An unfilled template posts
    those prompts verbatim - the prompts are what a recruiter reads, not a
    parser check.
    """
    document = parser.parse_document(read_blocks(_template_path().read_bytes()))

    assert [e.channel for e in document.emails] == [
        "linkedin", "accepted", "email", "email", "email", "inmail",
    ]
    assert [e.label for e in document.emails] == [
        "LinkedIn Connection", "If Accepted", "Email1", "Email2", "Email3", "Inmail",
    ]
    assert document.warnings == []
    # TrustIn's shape has no general advert: Wellfound is the only board, so
    # the Wellfound section is the advert.
    assert document.advert is None
    assert document.advert_for("wellfound") is not None
    assert document.notes_fields.keys() == {"company", "skills"}
    assert document.client_jd.startswith("[Paste the client's job description")


# What a recruiter pastes over each bracketed prompt, keyed by the prompt's
# opening words. The greeting and sign-off lines are already in the template.
_TITLE = "Founding Platform Engineer / Seed / On-site SF / K8s / up to $275k + equity"
_FILLED = {
    "[Connection note": ["I place platform engineers in SF. Let's talk."],
    "[The message sent once": ["Thanks for connecting."],
    "[Role title]": [_TITLE],
    "[Email 1 body.]": ["Email one."],
    "[Email 2 body.]": ["Email two."],
    "[Email 3 body.]": ["Email three."],
    "[InMail body.]": ["The InMail."],
    "[Opening paragraph": ["A seed-stage AI startup."],
    "[What they need you to build.]": ["Build the platform."],
    "[Background the client wants": ["5+ years of platform work."],
    "[The stack and the kind": ["Kubernetes, Terraform."],
    "[Closing line": ["Talk to us if that is you."],
    "Company: [": ["Company: Acme"],
    "Skills: [": ["Skills: Kubernetes, Terraform"],
    "[Anything learned": ["The hiring manager stressed on-call."],
    "[Paste the client's job description": ["About the role", "Build the platform."],
}


def test_the_recruiter_template_filled_in_parses_cleanly():
    """Filled in, the template parses with nothing to warn about.

    The template it replaced did not: its advert sat after the messages, so the
    advert was titled "Ad · LinkedIn", emails 2 and 3 took that as their
    subject, and a sub-heading in the advert was appended to the connection
    note.
    """
    import io

    import docx

    template = docx.Document(str(_template_path()))
    for paragraph in list(template.paragraphs):
        lines = next((v for k, v in _FILLED.items() if paragraph.text.startswith(k)), None)
        if lines is None:
            continue
        assert "[" in paragraph.text
        for line in lines:
            paragraph.insert_paragraph_before(line)
        paragraph._p.getparent().remove(paragraph._p)
    assert not any("[" in p.text for p in template.paragraphs), "a slot has no filling"
    buffer = io.BytesIO()
    template.save(buffer)

    document = parser.parse_document(read_blocks(buffer.getvalue()))

    assert document.warnings == []
    assert [(e.channel, e.body_text.splitlines()[-1]) for e in document.emails] == [
        ("linkedin", "I place platform engineers in SF. Let's talk."),
        ("accepted", "Yours Sincerely,"),
        ("email", "Yours Sincerely,"),
        ("email", "Yours Sincerely,"),
        ("email", "Yours Sincerely,"),
        ("inmail", "Yours Sincerely,"),
    ]
    subjects = {e.subject for e in document.emails if e.channel in ("email", "inmail")}
    assert subjects == {_TITLE}
    # LinkedIn messages carry no subject.
    assert document.emails[0].subject == "" and document.emails[1].subject == ""
    assert "{ai_intro}" in document.emails[2].body_text

    wellfound = document.advert_for("wellfound")
    assert wellfound.title.startswith("Founding Platform Engineer / Seed")
    assert "Candidate Background" in wellfound.body_text
    assert "Technical Skills" in wellfound.body_text
    assert document.notes_fields == {"company": "Acme", "skills": "Kubernetes, Terraform"}
    assert "Candidate Background" not in document.notes
    assert document.client_jd.startswith("About the role")


def _document_with_notes():
    return parser.parse_document([
        _heading("Job Advert"),
        _block("Join a team that ships."),
        _heading("Email 1"),
        _block("Subject: Platform Engineer"),
        _block("Hi {{first_name}}, we are hiring."),
        _heading("Wellfound"),
        _block("A stealth AI company is hiring."),
        _heading("Recruiter Notes"),
        _block("Company: Acme AI"),
        _block("Skills: Go, Kubernetes; CI/CD"),
        _block("Hiring manager wants people from Databricks or Scale."),
        _heading("From the call", level=2),
        _block("No sponsorship, five days on-site."),
        _heading("Client JD"),
        _block("Acme AI is hiring a Staff Platform Engineer."),
    ])


def test_recruiter_notes_reach_the_draft_and_nothing_that_posts():
    """The notes are internal: the drafting model reads them, no screen does."""
    document = _document_with_notes()

    assert document.notes.startswith("Company: Acme AI")
    assert "Databricks" in document.notes
    # A sub-heading inside the notes stays in the notes, not the last email.
    assert "From the call" in document.notes
    assert "five days on-site" in document.notes
    assert document.notes_fields == {
        "company": "Acme AI", "skills": "Go, Kubernetes; CI/CD",
    }
    assert document.advert.tags == ["Go", "Kubernetes", "CI/CD"]
    assert document.advert_for("wellfound").tags == ["Go", "Kubernetes", "CI/CD"]

    assert "Databricks" not in document.job_description
    assert "Databricks" not in document.search_jd
    assert "Databricks" not in document.advert.body_text
    assert "Databricks" not in document.advert_for("wellfound").body_text
    assert "Databricks" not in document.emails[-1].body_text
    assert document.client_jd == "Acme AI is hiring a Staff Platform Engineer."
    assert document.warnings == []


@pytest.mark.parametrize(
    "heading",
    ["Recruiter Notes", "Notes", "Notes for AI", "Sourcing Notes", "Research", "Internal notes"],
)
def test_the_notes_headings_a_recruiter_might_write(heading):
    document = parser.parse_document([
        _heading("Email 1"),
        _block("Hi {{first_name}}."),
        _heading(heading),
        _block("Target Stripe alumni."),
    ])
    assert document.notes == "Target Stripe alumni."
    assert document.emails[0].body_text == "Hi {{first_name}}."
