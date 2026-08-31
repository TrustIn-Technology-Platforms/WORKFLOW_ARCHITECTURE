"""Loxo's Skill DNA: read it out of a job description, tighten it, write it back.

Loxo has no separate store for candidate criteria. `Write with AI (BETA)` on a
job generates what it calls the intelligence stack — role details, tech stack,
questions to ask, and the criteria — straight into the job's `description`
field, as ordinary paragraphs:

    Dealbreaker
      Work experience
      Built security infra at a B2B SaaS company in a regulated industry
      Updated
      Hard skills
      Deep hands-on AWS experience
    Baseline
      Seniority
      5-12 years building security programs
    Nice-to-have
      Work experience
      0-to-1 experience standing up security infrastructure
    Traits to avoid
      Contractors or consultants

So the criteria and the advert live in one field, and changing the criteria
means rewriting the description. That is what this module exists to do safely:
parse the buckets out of the HTML, move the items between them, and render the
result back with everything that was not a criterion left exactly as it was.

`tighten()` is the policy — every nice-to-have becomes a dealbreaker, because a
preference filters nobody out. It is the same rule the noon automation applies
to must-haves (see app/platforms/noon_sourcing.py), phrased in Loxo's buckets.

Nothing here touches a browser: the parse/tighten/render round trip is pure, so
the policy can be tested without writing to a live ATS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.logging_conf import get_logger

log = get_logger(__name__)

# The four buckets, in the order Loxo renders them.
DEALBREAKER = "Dealbreaker"
BASELINE = "Baseline"
NICE_TO_HAVE = "Nice-to-have"
AVOID = "Traits to avoid"
BUCKETS = (DEALBREAKER, BASELINE, NICE_TO_HAVE, AVOID)

# Criterion types Loxo groups items under. A bucket may use none of them —
# "Traits to avoid" lists its items bare — so an unknown line is an item, not a
# category, unless it matches one of these exactly.
CATEGORIES = (
    "Work experience",
    "Hard skills",
    "Soft skills",
    "Seniority",
    "Education",
    "Industry",
    "Location",
)

# Loxo stamps this after an item a human has edited. It is a marker, not a
# criterion, and re-rendering drops it.
EDITED_MARKER = "Updated"


@dataclass(slots=True)
class Criterion:
    text: str
    category: str = ""

    def __post_init__(self) -> None:
        self.text = " ".join(self.text.split())
        self.category = " ".join(self.category.split())

    @property
    def key(self) -> str:
        """For de-duplication. Loxo's own text carries soft hyphens and
        zero-width joiners from its editor, which two otherwise identical
        criteria will not share."""
        cleaned = "".join(
            ch for ch in self.text.lower() if ch.isalnum() or ch.isspace()
        )
        return " ".join(cleaned.split())


@dataclass(slots=True)
class SkillDNA:
    """A job description split into the prose and the criteria under it."""

    advert_html: str = ""
    buckets: dict[str, list[Criterion]] = field(default_factory=dict)

    def items(self, bucket: str) -> list[Criterion]:
        return self.buckets.get(bucket, [])

    @property
    def has_criteria(self) -> bool:
        return any(self.buckets.get(name) for name in BUCKETS)

    @property
    def summary(self) -> str:
        counts = [f"{len(self.items(name))} {name.lower()}" for name in BUCKETS]
        return ", ".join(counts)


def _soup(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "beautifulsoup4 is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return BeautifulSoup(html or "", "html.parser")


def _blocks(html: str) -> list[tuple[str, str]]:
    """The description as (text, inner_html) pairs, one per rendered line.

    Loxo writes each criterion as its own `<p>`, and bullets as `<li>`. Both are
    lines for our purposes; empty spacer paragraphs are dropped.
    """
    soup = _soup(html)
    out: list[tuple[str, str]] = []
    for element in soup.find_all(["p", "li", "h1", "h2", "h3", "h4"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text:
            continue
        out.append((text, element.decode_contents()))
    return out


def parse_skill_dna(description_html: str) -> SkillDNA:
    """Split a job description into its advert prose and its criteria.

    Everything before the first bucket heading is the advert and is preserved
    verbatim — it is the client-facing copy, and this module has no business
    rewriting it.
    """
    soup = _soup(description_html)
    dna = SkillDNA(buckets={name: [] for name in BUCKETS})

    elements = soup.find_all(["p", "li", "h1", "h2", "h3", "h4"])
    start = None
    for index, element in enumerate(elements):
        if " ".join(element.get_text(" ", strip=True).split()) == DEALBREAKER:
            start = index
            break

    if start is None:
        # No criteria yet: the whole description is advert. That is the state a
        # job is in before anyone has run Write with AI on it.
        dna.advert_html = description_html or ""
        return dna

    kept: list[str] = []
    for element in elements[:start]:
        kept.append(str(element))
    dna.advert_html = "".join(kept)

    bucket = ""
    category = ""
    for element in elements[start:]:
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text or text == EDITED_MARKER:
            continue
        if text in BUCKETS:
            bucket, category = text, ""
            continue
        if text in CATEGORIES:
            category = text
            continue
        if bucket:
            dna.buckets[bucket].append(Criterion(text=text, category=category))

    log.info("loxo skill dna parsed", extra={"summary": dna.summary})
    return dna


def tighten(dna: SkillDNA) -> tuple[SkillDNA, list[Criterion]]:
    """Every nice-to-have becomes a dealbreaker. Returns the new DNA and what moved.

    Baseline is left alone on purpose. Loxo treats it as the floor a candidate
    must clear rather than a preference — it already filters — so promoting it
    would say nothing new, while a nice-to-have that stays put filters nobody.
    "Traits to avoid" is untouched for the same reason: it is already an
    exclusion.
    """
    promoted: list[Criterion] = []
    seen = {item.key for item in dna.items(DEALBREAKER) if item.key}

    tightened = SkillDNA(
        advert_html=dna.advert_html,
        buckets={name: list(dna.items(name)) for name in BUCKETS},
    )

    for item in dna.items(NICE_TO_HAVE):
        if item.key and item.key in seen:
            continue
        seen.add(item.key)
        tightened.buckets[DEALBREAKER].append(item)
        promoted.append(item)

    tightened.buckets[NICE_TO_HAVE] = []

    log.info(
        "loxo criteria tightened",
        extra={"promoted": len(promoted), "dealbreakers": len(tightened.items(DEALBREAKER))},
    )
    return tightened, promoted


def _paragraph(text: str) -> str:
    from html import escape

    return f"<p>{escape(text)}</p>"


def render(dna: SkillDNA) -> str:
    """Back to description HTML, in the shape Loxo writes it.

    Items are grouped under their category heading the way Loxo groups them, so
    a description this produces reads the same as one their own generator wrote.
    """
    parts: list[str] = []
    if dna.advert_html.strip():
        parts.append(dna.advert_html.rstrip())
        parts.append("<p><br></p>")

    for name in BUCKETS:
        items = dna.items(name)
        if not items:
            continue
        parts.append(_paragraph(name))
        parts.append("<p><br></p>")
        current = None
        for item in items:
            if item.category and item.category != current:
                parts.append(_paragraph(item.category))
                current = item.category
            parts.append(_paragraph(item.text))
        parts.append("<p><br></p>")

    return "".join(parts)


def missing_fields(dna: SkillDNA, *, wanted: Iterable[str] = BUCKETS) -> list[str]:
    """Which buckets came back empty — the gaps worth filling from the advert.

    Loxo's generator leaves a bucket out when the advert gave it nothing to work
    with, and an empty Dealbreaker list means the role has no criteria at all.
    """
    return [name for name in wanted if not dna.items(name)]
