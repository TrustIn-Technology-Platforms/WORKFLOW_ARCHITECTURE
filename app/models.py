"""Domain objects passed between the Notion, document and platform layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    POSTED = "posted"
    SKIPPED = "skipped"
    FAILED = "failed"
    DRY_RUN = "dry_run"


@dataclass(slots=True)
class NotionRow:
    """One record from the source database."""

    page_id: str
    title: str
    document_url: str | None
    status: str | None
    platforms: list[str] = field(default_factory=list)
    url: str | None = None
    raw_properties: dict[str, Any] = field(default_factory=dict)

    def property_text(self, name: str) -> str | None:
        """Read an arbitrary extra property as plain text.

        Adverts often carry structured fields (Location, Salary) as real Notion
        columns rather than prose inside the document, so platform adapters can
        pull them straight off the row.
        """
        from app.notion.schema import plain_text_of  # local import avoids a cycle

        prop = self.raw_properties.get(name)
        return plain_text_of(prop) if prop else None


@dataclass(slots=True)
class Block:
    """A single paragraph of the source document, style preserved."""

    style: str  # heading | title | body | list_bullet | list_number
    level: int  # 1..6 for headings, 0 otherwise
    text: str
    html: str

    @property
    def is_heading(self) -> bool:
        return self.style in {"heading", "title"}


@dataclass(slots=True)
class Section:
    heading: str
    level: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    @property
    def html(self) -> str:
        return "\n".join(b.html for b in self.blocks if b.text.strip())


@dataclass(slots=True)
class Advert:
    title: str
    body_text: str
    body_html: str
    location: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    category: str | None = None
    reference: str | None = None
    tags: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)

    def as_context(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "location": self.location or "",
            "salary": self.salary or "",
            "employment_type": self.employment_type or "",
            "category": self.category or "",
            "reference": self.reference or "",
            "tags": ", ".join(self.tags),
            "fields": dict(self.fields),
        }


@dataclass(slots=True)
class EmailStep:
    """One step of an outreach sequence.

    Named for the common case, but a sequence mixes channels: a LinkedIn
    connection note and an InMail are steps too, and they are not email. The
    `channel` decides which platform field a step is typed into, so a recipe
    can select the steps it can actually send.
    """

    order: int
    subject: str
    body_text: str
    body_html: str
    delay_days: int | None = None
    channel: str = "email"  # email | linkedin | inmail | wellfound
    label: str = ""         # the document's own heading, for logs and diffing

    @property
    def is_email(self) -> bool:
        return self.channel == "email"

    def as_context(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "subject": self.subject,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "delay_days": self.delay_days if self.delay_days is not None else "",
            "channel": self.channel,
            "label": self.label,
        }


@dataclass(slots=True)
class ParsedDocument:
    sections: list[Section] = field(default_factory=list)
    advert: Advert | None = None
    emails: list[EmailStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The document's own name, extension stripped - the recruiters' filename
    # convention "Company - Role - Location". This is what every platform names
    # its sequence after, so the name is identical across noon/Loxo/Juicebox.
    source_name: str = ""
    # The client's own job description, pasted verbatim under a `Client JD`
    # heading at the end of the document. Empty when nobody pasted one - which
    # is why the sourcing platforms read `job_description` below and not this.
    client_jd: str = ""
    # Adverts written for one destination, keyed by platform. A `Wellfound`
    # section carries copy shaped for Wellfound - anonymised differently, cut to
    # a different length - and posting the general advert there instead throws
    # away the version a recruiter wrote on purpose. Empty for a document that
    # names no platform, which is most of them.
    platform_adverts: dict[str, Advert] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.advert is None and not self.emails

    def advert_for(self, platform: str) -> Advert | None:
        """The advert this platform should post: its own if the document wrote
        one, the general advert otherwise.

        Every recipe reaches `advert` through this, so a platform section is
        honoured without any recipe knowing it exists.
        """
        return self.platform_adverts.get(platform.strip().lower()) or self.advert

    @property
    def job_description(self) -> str:
        """The text a search is built from.

        The advert is marketing copy: written to attract applicants, so it
        deliberately softens the years, the stack, the location and the
        non-negotiables - the very things a sourcing agent filters on. The
        client's JD states them. So the `Client JD` section wins wherever a
        recruiter pasted one, and the advert stands in where they did not,
        which is what every document written before this existed relies on.
        """
        if self.client_jd.strip():
            return self.client_jd.strip()
        return self.advert.body_text.strip() if self.advert else ""


@dataclass(slots=True)
class PostResult:
    platform: str
    outcome: Outcome
    post_url: str | None = None
    detail: str | None = None
    artifacts: list[str] = field(default_factory=list)
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in {Outcome.POSTED, Outcome.DRY_RUN, Outcome.SKIPPED}


class PipelineError(RuntimeError):
    """Raised when a row cannot be processed. Message is written back to Notion."""


class DocumentFetchError(PipelineError):
    pass


class DocumentParseError(PipelineError):
    pass


class PlatformError(PipelineError):
    pass


class AuthenticationRequired(PlatformError):
    """The saved browser session is missing or expired - a human must re-login."""
