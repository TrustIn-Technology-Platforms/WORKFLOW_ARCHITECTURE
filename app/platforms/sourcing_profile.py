"""One sourcing profile per document: drafted once, saved, read everywhere.

Sohaib's review of the live searches (2026-09-28) found each platform being
told a different, thinner story about the same job: noon set up with little
more than a title, Juicebox handed the *advert* as its job description, and
skills lists that stopped at the broad strokes — an AI-engineer role with no
Python on it. Three platforms were each drafting their own filters from
whatever text they had, none of it kept, none of it shared.

This module is the fix. `ensure_sourcing` builds **one** profile per document —
what the role actually is, the titles a matching candidate holds, the essential
and the nice-to-have stack, years, where the candidate must be, the client's
stage and the same-stage companies, a boolean search string — and every
sourcing platform reads that same object. It is drafted from the `Client JD`
when the document carries one and from the advert when it does not, and in the
advert case the profile also carries a **composed JD**: the role restated as a
spec, which is what gets pasted into a platform's own JD box instead of the
pitch (`ParsedDocument.search_jd`).

The profile is saved as JSON under `<artifact_dir>/sourcing/` and reused when
the same document comes back unchanged, so a noon run today and a Loxo run
tomorrow configure their searches from identical lists — and a recruiter can
open the file and see exactly what every search was told. Recorded as D-024.

Skills policy, because it is the part that was wrong: the JD's own stack is
never enough. A JD that says "AI engineer" without saying "Python" still means
Python, and the searches missed such people. So the prompt is told to read the
role first and then list what a role of that kind *entails*, essential and
nice-to-have both — over-inclusion is cheap (a recruiter deletes a chip in a
second) and a missing essential skill silently excludes the right candidates.

Same key policy as the rest of the drafting layer: no `ANTHROPIC_API_KEY`
means callers get None and report the gap; a drafting failure is never a
failed run.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.logging_conf import get_logger
from app.models import ParsedDocument, SourcingProfile
from app.platforms import targeting_ai

log = get_logger(__name__)

MAX_TOKENS = 4_000

# In the cache fingerprint, so improving the prompt re-drafts every profile
# instead of serving answers the old prompt gave.
PROMPT_VERSION = "2026-09-28"

SYSTEM = """You configure a candidate search for a technical recruiter, from a
job description.

First understand the role. State it in `role_kind` as one sentence — what kind
of engineer/person this is, at what seniority, building what. Everything else
must be consistent with that reading.

`similar_titles` — job titles a strong candidate holds TODAY, most likely
first. Real titles as they appear on profiles ("Machine Learning Engineer",
"Site Reliability Engineer"), covering the adjacent titles the same person
hires under; seniority variants only when the JD demands that seniority.

`must_have_skills` — the essential stack, two sources merged:
- every hard skill the JD states as required;
- every skill a role of this kind cannot be done without, EVEN WHEN THE JD
  DOES NOT NAME IT. An AI engineer role entails Python and a deep-learning
  framework; a cloud/platform role entails a major cloud and infrastructure-
  as-code; a frontend role entails JavaScript or TypeScript. Name the concrete
  languages, frameworks, clouds and tools — a profile search matches on named
  things, and an unnamed essential skill silently excludes the right people.

`nice_to_have_skills` — the better-to-have layer: the JD's stated preferences,
plus the adjacent tools common on profiles that hold this role. Do not repeat
a must-have. It is fine for this list to be generous; the recruiter prunes it.

Both lists: canonical names a search box takes ("Python", "Kubernetes",
"PyTorch"), most central first. No soft skills, no duties, no seniority words.

`min_years` / `max_years` — the TOTAL years of professional experience the JD
asks for ("5+ years" -> min 5, no max; "3-5 years" -> 3 and 5). The overall
requirement, not years with one tool. Null when the JD gives no figure; never
guess one.

`candidate_location` — where the CANDIDATE must be based, as the JD states it,
in the form a search box takes: a city or metro ("New York"), a country or
region ("United Kingdom"), or the remote region for a remote role. Hybrid and
onsite roles are based where the office the candidate reports to is — NOT the
company's headquarters or founding city. Plain place names only, several
joined with " / " when the JD offers a choice. Null when the JD says nothing.

`boolean_search` — one boolean search string a recruiter can paste into
LinkedIn or a database: the title variants OR'd in one group, AND'd with the
essential skills OR'd in another, quotes around multi-word terms. Keep it to
the terms that matter; a 40-term string matches nobody.

`job_description` — ONLY when the user message asks for it: the role restated
as the job description a search engine should read. Use everything the advert
and the stated facts give you — a summary of what the role is, the
responsibilities, the required skills and years (including the entailed ones,
plainly marked as required), the nice-to-haves, the location and working
arrangement, the seniority. Plain text with short headed sections. State only
what the source text supports or what a role of this kind plainly entails;
never invent company facts, salary, or benefits. When not asked for, leave it
empty."""


class DraftProfile(BaseModel):
    role_kind: str = Field(default="", description="One sentence: what this role actually is.")
    similar_titles: list[str] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    min_years: int | None = None
    max_years: int | None = None
    candidate_location: str | None = None
    boolean_search: str = ""
    job_description: str = Field(
        default="", description="Only when asked: the role restated as a search-grade JD."
    )


# ----------------------------------------------------------------------
# pure helpers
# ----------------------------------------------------------------------


def draft_title(document: ParsedDocument, fallback: str = "") -> str:
    """The role name the draft is prompted with, the same whoever asks.

    noon asks with the advert's title and Juicebox with the sequence name —
    the filename, "Company - Role - Location", whose *leading* segment is the
    company. Left to the caller, the cached profile's content would depend on
    which platform drafted first. So the document decides: the advert's title,
    else the filename's middle segment, else whatever the caller offered.
    """
    if document.advert is not None and document.advert.title.strip():
        return document.advert.title.strip()
    segments = [s.strip() for s in (document.source_name or "").split(" - ") if s.strip()]
    if len(segments) >= 2:
        return segments[1]
    return fallback.strip()


def _term(value: str) -> str:
    text = " ".join(value.split())
    return f'"{text}"' if " " in text else text


def build_boolean(titles: list[str], skills: list[str],
                  *, max_titles: int = 6, max_skills: int = 8) -> str:
    """A recruiter-usable boolean string, for when the model returned none.

    Titles OR'd, AND'd with the essential skills OR'd — the shape recruiters
    paste into LinkedIn. Capped: a boolean over thirty terms matches nobody
    and reads like noise.
    """
    title_terms = [_term(t) for t in titles[:max_titles] if t.strip()]
    skill_terms = [_term(s) for s in skills[:max_skills] if s.strip()]
    groups = []
    if title_terms:
        groups.append("(" + " OR ".join(title_terms) + ")")
    if skill_terms:
        groups.append("(" + " OR ".join(skill_terms) + ")")
    return " AND ".join(groups)


def _slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return text[:60] or "document"


def fingerprint(jd: str, settings: Settings, *, location: str = "", notes: str = "") -> str:
    """What the profile was drafted from. A changed JD, prompt or list size
    means the saved answer no longer answers the question.

    The caller's `role_title` is deliberately not in here: `draft_title`
    normalises it from the document, and the same document must answer every
    platform from one saved profile - that is the point of it. The fallback
    `location` IS in here: it shapes the company list when the JD names no
    place, the adapters all derive it from the same row so the cache still
    shares, and an operator's `--location` is genuinely a different question.
    """
    material = "\n".join([
        PROMPT_VERSION,
        settings.criteria_model,
        str(settings.sourcing_max_titles),
        str(settings.sourcing_max_skills),
        str(settings.sourcing_max_companies),
        location.strip(),
        jd.strip(),
        notes.strip(),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def profile_path(document: ParsedDocument, settings: Settings) -> Path:
    return Path(settings.artifact_dir) / "sourcing" / f"{_slug(document.source_name)}.json"


def save_profile(profile: SourcingProfile, path: Path, *, stamp: str,
                 document_name: str) -> str:
    """The profile on disk, for reuse and for a recruiter to read. Best effort:
    a full disk costs the cache, not the run."""
    payload = {
        "fingerprint": stamp,
        "drafted_at": datetime.now(timezone.utc).isoformat(),
        "document": document_name,
        "profile": {k: v for k, v in asdict(profile).items() if k not in ("path", "reused")},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("sourcing profile not saved", extra={"error": str(exc)[:120]})
        return ""
    return str(path)


def load_profile(path: Path, *, stamp: str) -> SourcingProfile | None:
    """The saved profile, only when it answers this exact document."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("fingerprint") != stamp:
        return None
    data = payload.get("profile")
    if not isinstance(data, dict):
        return None
    known = {f for f in SourcingProfile.__dataclass_fields__}  # type: ignore[attr-defined]
    profile = SourcingProfile(**{k: v for k, v in data.items() if k in known})
    profile.path = str(path)
    profile.reused = True
    return profile


# ----------------------------------------------------------------------
# the draft
# ----------------------------------------------------------------------


async def draft_profile(
    jd: str,
    *,
    role_title: str = "",
    compose_jd: bool = False,
    notes: str = "",
    settings: Settings | None = None,
) -> DraftProfile | None:
    """One Claude call for everything but the companies. Never raises.

    `compose_jd` asks for the search-grade JD too — set when the document has
    no Client JD, so the platforms' own JD boxes get a spec, not the pitch.
    `notes` is the recruiter's own section: it outranks the JD where the two
    disagree, because it was written after the call that explained the JD.
    """
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        log.info("sourcing profile not drafted - no ANTHROPIC_API_KEY")
        return None
    if not jd.strip():
        return None

    try:
        from anthropic import AsyncAnthropic
    except ImportError:  # pragma: no cover - dependency guard
        log.warning("sourcing profile not drafted - anthropic package not installed")
        return None

    ask_jd = (
        "This text is a job advert, not the client's own spec, so ALSO fill "
        "`job_description` with the search-grade restatement."
        if compose_jd
        else "Leave `job_description` empty."
    )
    prompt = (
        f"Role: {role_title or 'unnamed'}\n"
        f"Return up to {settings.sourcing_max_titles} similar titles, up to "
        f"{settings.sourcing_max_skills} must-have skills and up to "
        f"{settings.sourcing_max_skills} nice-to-have skills.\n"
        f"{ask_jd}\n\n"
        f"<job_description>\n{jd.strip()}\n</job_description>"
    )
    if notes.strip():
        prompt += (
            "\n\nThe recruiter's own notes on this role follow. They come from "
            "the briefing call and take precedence over the job description "
            "where the two differ. Never quote them in `job_description`.\n"
            f"<recruiter_notes>\n{notes.strip()}\n</recruiter_notes>"
        )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=4)
    try:
        response = await client.messages.parse(
            model=settings.criteria_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=DraftProfile,
        )
        draft = response.parsed_output
    except Exception as exc:
        log.warning("sourcing profile could not be drafted", extra={"error": str(exc)[:200]})
        return None
    finally:
        await client.close()
    return draft


async def build_profile(
    document: ParsedDocument,
    *,
    role_title: str = "",
    location: str = "",
    settings: Settings | None = None,
) -> SourcingProfile | None:
    """Draft the whole profile for one document. None when drafting is off.

    Two calls: the profile itself, and the same-stage companies through the
    existing `draft_companies` (D-020 lives there and is not re-implemented).
    `location` is the fallback region for that company list - the row's
    column, or the operator's `--location` - used only when the JD itself
    names no place.
    """
    settings = settings or get_settings()
    jd = document.job_description
    if not jd:
        return None
    title = draft_title(document, role_title)
    draft = await draft_profile(
        jd,
        role_title=title,
        compose_jd=not bool(document.client_jd.strip()),
        notes=document.notes,
        settings=settings,
    )
    if draft is None:
        return None

    # The composed spec exists only where there is no Client JD. The model is
    # told to leave `job_description` empty otherwise, but a model that fills
    # it anyway must not have its restatement quietly replace the client's
    # own words anywhere - so the guard is applied once, here, and everything
    # below uses the guarded value.
    composed = draft.job_description.strip() if not document.client_jd.strip() else ""

    advert_text = document.advert.body_text if document.advert else ""
    # The filename's first segment, unless the notes name the real employer -
    # the filename carries a codename when the client is confidential, and a
    # company list built around a codename excludes nobody.
    company = (
        document.notes_fields.get("company", "").strip()
        or (document.source_name or "").split(" - ")[0].strip()
    )
    stated = targeting_ai.stage_from_text(jd, advert_text)
    candidate_location = targeting_ai.sourcing_location(draft.candidate_location)
    companies = await targeting_ai.draft_companies(
        # The composed spec when the document had no Client JD: the company
        # list should rest on the requirements, not on the pitch either.
        composed or jd,
        company=company,
        stage=stated,
        location=targeting_ai.sourcing_location(candidate_location, location),
        role_title=title,
        limit=settings.sourcing_max_companies,
        settings=settings,
    )

    min_years, max_years = targeting_ai.clamp_years(draft.min_years, draft.max_years)
    cap = settings.sourcing_max_skills
    must = targeting_ai.clean_list(draft.must_have_skills, cap)
    # Duplicates of a must-have are dropped BEFORE the cap, so a nice-to-have
    # past position 20 still makes the list when earlier entries were repeats.
    must_keys = {m.lower() for m in must}
    nice_all = targeting_ai.clean_list(
        draft.nice_to_have_skills, len(draft.nice_to_have_skills)
    )
    nice = [s for s in nice_all if s.lower() not in must_keys][:cap]
    profile = SourcingProfile(
        role_kind=" ".join(draft.role_kind.split()),
        similar_titles=targeting_ai.clean_list(draft.similar_titles, settings.sourcing_max_titles),
        must_have_skills=must,
        nice_to_have_skills=nice,
        min_years=min_years,
        max_years=max_years,
        candidate_location=candidate_location,
        stage=(stated or (companies.stage if companies.stage != "Unknown" else "")).strip(),
        stage_stated=bool(stated),
        companies=companies.companies,
        boolean_search=" ".join(draft.boolean_search.split()),
        drafted_jd=composed,
        jd_source="client_jd" if document.client_jd.strip() else "advert",
    )
    if not profile.boolean_search:
        profile.boolean_search = build_boolean(profile.similar_titles, profile.must_have_skills)

    log.info(
        "sourcing profile drafted",
        extra={
            "model": settings.criteria_model,
            "titles": profile.similar_titles,
            "must_have": profile.must_have_skills,
            "nice_to_have": profile.nice_to_have_skills,
            "companies_n": len(profile.companies),
            "candidate_location": profile.candidate_location,
            "jd_source": profile.jd_source,
            "composed_jd": bool(profile.drafted_jd),
        },
    )
    return profile


async def ensure_sourcing(
    document: ParsedDocument,
    settings: Settings | None = None,
    *,
    role_title: str = "",
    location: str = "",
) -> SourcingProfile | None:
    """The document's profile: in memory, then the saved JSON, then drafted.

    Called by every sourcing adapter and CLI command. The first caller in a run
    pays the drafting; everyone after reads `document.sourcing`, and a re-run
    of the same unchanged document reads the JSON — so all searches for one
    role are configured from one answer. Never raises; None means no key, no
    JD, or a drafting failure, all logged, all for the caller to report.

    `location` is the fallback region for the company list (the row's column,
    or `--location`); it is part of the fingerprint, because a different
    region is a different question.
    """
    if document.sourcing is not None:
        return document.sourcing
    settings = settings or get_settings()
    jd = document.job_description
    if not jd:
        return None

    stamp = fingerprint(jd, settings, location=location, notes=document.notes)
    path = profile_path(document, settings)
    saved = load_profile(path, stamp=stamp)
    if saved is not None:
        log.info("sourcing profile reused", extra={"path": str(path)})
        document.sourcing = saved
        return saved

    profile = await build_profile(
        document, role_title=role_title, location=location, settings=settings
    )
    if profile is None:
        return None

    # A profile with holes still serves THIS run - the adapters report the
    # gaps - but it is not frozen: `draft_companies` never raises, so a rate
    # limit would otherwise cache an empty company list (or a missing composed
    # JD, meaning the raw advert as the paste) and serve it to every platform
    # on every future run without ever retrying.
    missing = [
        part
        for part, gone in (
            ("titles/skills", profile.is_empty),
            ("companies", not profile.companies),
            ("composed JD", profile.jd_source == "advert" and not profile.drafted_jd),
        )
        if gone
    ]
    if missing:
        log.warning(
            "sourcing profile incomplete - not cached", extra={"missing": missing}
        )
    else:
        profile.path = save_profile(
            profile, path, stamp=stamp, document_name=document.source_name
        )
    document.sourcing = profile
    return profile
