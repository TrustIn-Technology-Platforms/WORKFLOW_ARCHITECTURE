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
    DELETED = "deleted"


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
    # Notion's own last-edited stamp (minute precision). A row claimed as
    # `Posting` and not touched since is how long a run has been going - or how
    # long ago the process running it died.
    last_edited: datetime | None = None

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
class SourcingProfile:
    """Everything a candidate search is configured from, drafted once per role.

    Every sourcing platform used to draft its own titles and skills from the
    JD, so three platforms could disagree about one job and nothing kept what
    was drafted. This is the one shared answer: built by
    `app.platforms.sourcing_profile.ensure_sourcing`, saved as JSON under the
    artifact dir, and read by noon, Juicebox and Loxo alike (D-024).
    """

    # One sentence saying what the role actually is - the model states its
    # reading before listing anything, so a wrong list is explainable.
    role_kind: str = ""
    similar_titles: list[str] = field(default_factory=list)
    # Two tiers on purpose: the essential stack filters, the rest ranks. The
    # platforms that take one list read `skills`, which is both in that order.
    must_have_skills: list[str] = field(default_factory=list)
    nice_to_have_skills: list[str] = field(default_factory=list)
    min_years: int | None = None
    max_years: int | None = None
    # Where the CANDIDATE must be, as the JD states it; empty when it is silent.
    candidate_location: str = ""
    stage: str = ""
    stage_stated: bool = False
    companies: list[str] = field(default_factory=list)
    # A recruiter-usable boolean string over the titles and essential skills.
    boolean_search: str = ""
    # A search-grade JD composed from the advert, only when the document has no
    # Client JD - the advert is marketing copy and pasting it into a platform's
    # JD box builds the search from the pitch (the 2026-09-28 review).
    drafted_jd: str = ""
    jd_source: str = ""  # client_jd | advert
    path: str = ""       # the saved JSON, "" when saving failed
    reused: bool = False  # answered from the saved JSON rather than drafted

    @property
    def skills(self) -> list[str]:
        """One list for platforms with one box: essentials first, no repeats."""
        seen: set[str] = set()
        merged: list[str] = []
        for skill in (*self.must_have_skills, *self.nice_to_have_skills):
            key = skill.strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(skill.strip())
        return merged

    @property
    def is_empty(self) -> bool:
        return not (self.similar_titles or self.must_have_skills or self.nice_to_have_skills)

    def as_must_haves(self, *, limit: int = 12) -> list[str]:
        """The essentials phrased as must-have lines, for noon's wizard when
        its own extractor reads nothing. One method, because the adapter and
        the CLI each building this by hand had already diverged (the CLI's
        copy lost the years line). Capped: every line here becomes a starred
        non-negotiable, and twenty of them would match nobody.
        """
        lines = [f"Experience with {skill}" for skill in self.must_have_skills[:limit]]
        if self.min_years is not None:
            span = (
                f"{self.min_years}+"
                if self.max_years is None
                else f"{self.min_years}-{self.max_years}"
            )
            lines.insert(0, f"{span} years of professional experience")
        return lines

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.similar_titles)} title(s)",
            f"{len(self.must_have_skills)}+{len(self.nice_to_have_skills)} skill(s)",
            f"{len(self.companies)} company(ies)",
        ]
        if self.candidate_location:
            parts.append(f"location {self.candidate_location}")
        if self.stage:
            parts.append(f"stage {self.stage} ({'stated' if self.stage_stated else 'inferred'})")
        if self.reused:
            parts.append("reused from the saved profile")
        return ", ".join(parts)


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
    # The shared search setup, drafted once per document by `ensure_sourcing`
    # and read by every sourcing platform. None until someone builds it.
    sourcing: SourcingProfile | None = None

    @property
    def is_empty(self) -> bool:
        # A board advert counts: a document holding only a `Wellfound` section
        # still has something to post, and failing the row as "empty" would be
        # wrong twice over.
        return self.advert is None and not self.emails and not self.platform_adverts

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

    @property
    def search_jd(self) -> str:
        """The text pasted into a platform's own JD box.

        `job_description` is what *we* draft from; this is what a platform's own
        extractor reads. A pasted Client JD is still first, verbatim. But where
        there is none, the advert must not stand in here the way it does there:
        Juicebox and noon build their searches from whatever lands in that box,
        and the advert is the pitch, not the spec (the 2026-09-28 review found
        an advert pasted as a Juicebox JD). So the profile's composed JD - the
        role's facts restated as a spec - stands in instead, and the advert is
        the last resort only when nothing was ever drafted.
        """
        if self.client_jd.strip():
            return self.client_jd.strip()
        if self.sourcing is not None and self.sourcing.drafted_jd.strip():
            return self.sourcing.drafted_jd.strip()
        return self.advert.body_text.strip() if self.advert else ""


@dataclass(slots=True)
class Credentials:
    """A platform login the service may type for itself.

    Held as a Railway secret and read through `Settings.credentials_for`; never
    written to a recipe, a log line, a row or an artifact. `repr` masks the
    secrets so an accidental `%r` in a log cannot leak them.
    """

    platform: str
    username: str
    password: str
    # Base32 seed of a TOTP authenticator registered on the account. Empty when
    # the platform enforces no second factor - or enforces one this cannot
    # answer, in which case the login says so.
    totp_secret: str = ""

    def __repr__(self) -> str:
        return (
            f"Credentials(platform={self.platform!r}, username={self.username!r}, "
            f"password='***', totp_secret={'***' if self.totp_secret else ''!r})"
        )

    @property
    def secrets(self) -> list[str]:
        """Every value that must never appear in text a person reads."""
        return [s for s in (self.password, self.totp_secret) if s]


@dataclass(slots=True)
class PostResult:
    platform: str
    outcome: Outcome
    post_url: str | None = None
    detail: str | None = None
    artifacts: list[str] = field(default_factory=list)
    finished_at: datetime | None = None
    # What the post created on the platform, in the platform's own terms -
    # `{"role": <uuid>}` on noon, `{"sequence": <url>, "project": <url>}` on
    # Juicebox. Kept so the row can be deleted later: the Notion `Post URL`
    # column holds one link, and a row that posted to three platforms keeps
    # only the first. Only the adapter that wrote a record reads it back.
    records: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in {Outcome.POSTED, Outcome.DRY_RUN, Outcome.SKIPPED}


@dataclass(slots=True)
class DeleteResult:
    """One platform's half of deleting a row - the reverse of a `PostResult`."""

    platform: str
    outcome: Outcome  # DELETED | SKIPPED | FAILED | DRY_RUN
    detail: str | None = None
    artifacts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in {Outcome.DELETED, Outcome.DRY_RUN, Outcome.SKIPPED}


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
