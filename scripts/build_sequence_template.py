"""Build the recruiter-facing sequence document template.

    python scripts/build_sequence_template.py
    python scripts/build_sequence_template.py path/to/out.docx

One file: every section heading in order, each with a single `<< ... >>` slot
saying what to paste there. Nothing else - no margin comments, no example copy.
The annotated version this replaced (2026-11-03) carried its rules as Word
comments; the people filling it in found them noise and nobody read them, so
the rules live in docs/templates/sequence-document.md and the slots say just
enough to paste the right thing.

The order is the one TrustIn's own documents already use (Sohaib's working
template, 2026-10-08): connection note, the message sent once it is accepted
(added 2026-11-03), shared subject, three emails, InMail, the Wellfound advert,
then the two sections only the search reads. There is no general advert
because Wellfound is the only board the automation posts to; the Wellfound
section is the advert.

Every slot left unreplaced is posted as written - the slots are text like any
other.
"""

from __future__ import annotations

import sys
from pathlib import Path

import docx

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "templates" / "sequence-document-template.docx"

SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LinkedIn Connection", ("paste the connection note, under 300 characters, greeting included",)),
    ("If Accepted", ("paste the LinkedIn message sent once they accept the connection request; a new message, not the connection note",)),
    ("Subject", ("paste the one subject line: Role / Stage / On-site / Stack / up to $Nk + equity",)),
    ("Email1", ("paste email 1, greeting to sign-off; {ai_intro} on its own line where noon's opener goes",)),
    ("Email2", ("paste email 2",)),
    ("Email3", ("paste email 3",)),
    ("Inmail", ("paste the LinkedIn InMail",)),
    ("Wellfound", (
        "title line: Role / Stage / On-site / Stack / up to $Nk + equity",
        "paste the advert opening, anonymised",
        "**Candidate Background",
        "paste the background wanted, with \"N+ years\" where it applies",
        "**Technical Skills",
        "paste the stack and systems",
    )),
    ("Recruiter Notes", (
        "Company: << real company name, if the file uses a codename >>",
        "Skills: << comma-separated skills the client stressed >>",
        "anything learned on the call that the JD does not say; internal, never posted",
    )),
    ("Client JD", ("paste the client's job description exactly as received",)),
)


def build(path: Path) -> Path:
    document = docx.Document()
    for heading, lines in SECTIONS:
        # A real Heading 2, not a bold line: bold is only read as a heading
        # above the first styled one, and a pasted Client JD brings styled
        # headings with it.
        document.add_heading(heading, level=2)
        for line in lines:
            if line.startswith("**"):
                # The Wellfound advert's own sub-headings: bold text inside the
                # section, not section headings.
                document.add_paragraph().add_run(line[2:]).bold = True
            elif "<<" in line:
                # A labelled line ("Company: << ... >>") keeps its label as is.
                document.add_paragraph(line)
            else:
                document.add_paragraph().add_run(f"<< {line} >>").italic = True

    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT))
