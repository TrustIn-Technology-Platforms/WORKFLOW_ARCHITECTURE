"""The shared sourcing profile: drafted once, saved, read everywhere (D-024).

What these tests hold:

- one profile object feeds every platform, so the searches cannot disagree;
- the saved JSON answers the same document again, and only the same document
  (a changed JD is a changed question);
- the composed JD is drafted exactly when the document has no Client JD, and
  `search_jd` never hands a platform the raw advert while a spec exists —
  the 2026-09-28 review found a Juicebox search built from marketing copy.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.models import Advert, ParsedDocument, SourcingProfile
from app.platforms.sourcing_profile import (
    DraftProfile,
    build_boolean,
    ensure_sourcing,
    fingerprint,
    load_profile,
    profile_path,
    save_profile,
)
from app.platforms.targeting_ai import CompanyTargeting


def _document(client_jd: str = "") -> ParsedDocument:
    return ParsedDocument(
        advert=Advert(
            title="AI Engineer",
            body_text="Join our friendly team building the future of AI.",
            body_html="<p>Join us.</p>",
        ),
        emails=[],
        source_name="Acme - AI Engineer - London",
        client_jd=client_jd,
    )


def _stub_drafts(monkeypatch, *, drafted: dict | None = None):
    """Stand-ins for the two Claude calls, remembering what they were asked."""
    asked: dict = {}

    async def fake_profile(jd, **kwargs):
        asked["jd"] = jd
        asked.update(kwargs)
        fields = {
            "role_kind": "A hands-on AI engineer building LLM products.",
            "similar_titles": ["AI Engineer", "Machine Learning Engineer"],
            "must_have_skills": ["Python", "PyTorch"],
            "nice_to_have_skills": ["TypeScript", "Python"],  # dupe on purpose
            "min_years": 4,
            "candidate_location": "London",
            "boolean_search": '("AI Engineer") AND (Python)',
        }
        fields.update(drafted or {})
        return DraftProfile(**fields)

    async def fake_companies(jd, **kwargs):
        asked["companies_jd"] = jd
        asked["companies_kwargs"] = kwargs
        return CompanyTargeting(
            stage="Series B", stage_basis="inferred", companies=["Anthropic"]
        )

    monkeypatch.setattr("app.platforms.sourcing_profile.draft_profile", fake_profile)
    monkeypatch.setattr("app.platforms.targeting_ai.draft_companies", fake_companies)
    return asked


# ----------------------------------------------------------------------
# pure pieces
# ----------------------------------------------------------------------


def test_the_boolean_fallback_quotes_multiword_terms_and_groups_them():
    boolean = build_boolean(
        ["AI Engineer", "ML Engineer"], ["Python", "Large Language Models"]
    )
    assert boolean == (
        '("AI Engineer" OR "ML Engineer") AND (Python OR "Large Language Models")'
    )


def test_the_boolean_fallback_survives_one_empty_group():
    assert build_boolean([], ["Python"]) == "(Python)"
    assert build_boolean(["AI Engineer"], []) == '("AI Engineer")'
    assert build_boolean([], []) == ""


def test_the_merged_skills_list_puts_essentials_first_and_never_repeats():
    profile = SourcingProfile(
        must_have_skills=["Python", "PyTorch"],
        nice_to_have_skills=["python", "TypeScript"],
    )
    assert profile.skills == ["Python", "PyTorch", "TypeScript"]


def test_search_jd_prefers_the_client_jd_then_the_composed_spec_then_the_advert():
    pasted = _document(client_jd="The client's own spec.")
    pasted.sourcing = SourcingProfile(drafted_jd="Composed spec.")
    assert pasted.search_jd == "The client's own spec."

    composed = _document()
    composed.sourcing = SourcingProfile(drafted_jd="Composed spec.")
    assert composed.search_jd == "Composed spec."

    bare = _document()
    assert bare.search_jd == "Join our friendly team building the future of AI."


def test_the_saved_profile_only_answers_the_same_question(tmp_path):
    profile = SourcingProfile(similar_titles=["AI Engineer"], must_have_skills=["Python"])
    path = tmp_path / "acme.json"
    saved = save_profile(profile, path, stamp="abc123", document_name="Acme - AI Engineer")
    assert saved == str(path)

    loaded = load_profile(path, stamp="abc123")
    assert loaded is not None
    assert loaded.similar_titles == ["AI Engineer"]
    assert loaded.reused is True
    assert loaded.path == str(path)

    # A different fingerprint - a changed JD, prompt or list size - is a
    # different question, and a stale answer is worse than a redraft.
    assert load_profile(path, stamp="something-else") is None


def test_the_fingerprint_moves_with_the_jd_not_with_the_caller():
    settings = Settings()
    one = fingerprint("JD one", settings)
    two = fingerprint("JD two", settings)
    assert one != two
    assert one == fingerprint("JD one", settings)


def test_platforms_asking_with_different_role_titles_share_one_saved_profile(
    monkeypatch, tmp_path
):
    """noon asks with the advert's title, Juicebox with the sequence name.
    Same document, one draft - that is the point of the shared profile."""
    _stub_drafts(monkeypatch, drafted={"job_description": "Spec."})
    settings = Settings(artifact_dir=str(tmp_path))
    asyncio.run(ensure_sourcing(_document(), settings, role_title="AI Engineer"))

    async def exploding_profile(*args, **kwargs):
        raise AssertionError("the saved profile should have answered")

    monkeypatch.setattr("app.platforms.sourcing_profile.draft_profile", exploding_profile)
    again = asyncio.run(
        ensure_sourcing(_document(), settings, role_title="Acme - AI Engineer - London")
    )
    assert again is not None and again.reused is True


# ----------------------------------------------------------------------
# ensure_sourcing
# ----------------------------------------------------------------------


def test_a_document_without_a_client_jd_gets_a_composed_spec(monkeypatch, tmp_path):
    asked = _stub_drafts(
        monkeypatch, drafted={"job_description": "Requirements: Python, PyTorch."}
    )
    settings = Settings(artifact_dir=str(tmp_path))
    document = _document()

    profile = asyncio.run(ensure_sourcing(document, settings, role_title="AI Engineer"))

    assert asked["compose_jd"] is True
    assert profile is not None
    assert profile.jd_source == "advert"
    assert profile.drafted_jd == "Requirements: Python, PyTorch."
    assert document.search_jd == "Requirements: Python, PyTorch."
    # The companies rest on the spec too, not on the pitch.
    assert asked["companies_jd"] == "Requirements: Python, PyTorch."
    # The nice-to-have that duplicated a must-have was dropped, not repeated.
    assert profile.must_have_skills == ["Python", "PyTorch"]
    assert profile.nice_to_have_skills == ["TypeScript"]
    assert profile.stage == "Series B"
    assert profile.stage_stated is False
    assert document.sourcing is profile


def test_a_pasted_client_jd_is_never_rewritten(monkeypatch, tmp_path):
    asked = _stub_drafts(
        monkeypatch, drafted={"job_description": "Should be ignored."}
    )
    settings = Settings(artifact_dir=str(tmp_path))
    document = _document(client_jd="The client's spec, verbatim. Series A fintech.")

    profile = asyncio.run(ensure_sourcing(document, settings))

    assert asked["compose_jd"] is False
    assert profile is not None
    assert profile.jd_source == "client_jd"
    assert profile.drafted_jd == ""
    assert document.search_jd.startswith("The client's spec")
    # The model filled `job_description` although told not to; the companies
    # must still be drafted from the client's own words, not the restatement.
    assert asked["companies_jd"].startswith("The client's spec")
    # A stage the document states is kept and marked stated (D-022 rests on it).
    assert profile.stage == "Series A"
    assert profile.stage_stated is True


def test_the_second_run_reads_the_saved_json_not_claude(monkeypatch, tmp_path):
    _stub_drafts(monkeypatch, drafted={"job_description": "Spec."})
    settings = Settings(artifact_dir=str(tmp_path))
    first = asyncio.run(ensure_sourcing(_document(), settings, role_title="AI Engineer"))
    assert first is not None and first.path

    calls = {"n": 0}

    async def exploding_profile(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("the saved profile should have answered")

    monkeypatch.setattr("app.platforms.sourcing_profile.draft_profile", exploding_profile)

    # A fresh document object - a new run of the same unchanged document.
    again = asyncio.run(ensure_sourcing(_document(), settings, role_title="AI Engineer"))
    assert again is not None
    assert again.reused is True
    assert again.similar_titles == first.similar_titles
    assert calls["n"] == 0


def test_a_changed_jd_is_redrafted_not_served_stale(monkeypatch, tmp_path):
    _stub_drafts(monkeypatch)
    settings = Settings(artifact_dir=str(tmp_path))
    asyncio.run(ensure_sourcing(_document(), settings, role_title="AI Engineer"))

    changed = _document(client_jd="A completely different role: Staff SRE.")
    profile = asyncio.run(ensure_sourcing(changed, settings, role_title="AI Engineer"))
    assert profile is not None
    assert profile.reused is False


def test_the_boolean_is_built_from_the_lists_when_the_model_returns_none(
    monkeypatch, tmp_path
):
    _stub_drafts(monkeypatch, drafted={"boolean_search": ""})
    settings = Settings(artifact_dir=str(tmp_path))
    profile = asyncio.run(ensure_sourcing(_document(), settings))
    assert profile is not None
    assert profile.boolean_search == (
        '("AI Engineer" OR "Machine Learning Engineer") AND (Python OR PyTorch)'
    )


def test_no_draft_means_none_and_nothing_written(monkeypatch, tmp_path):
    async def no_profile(*args, **kwargs):
        return None  # no key, or the call failed - same caller contract

    monkeypatch.setattr("app.platforms.sourcing_profile.draft_profile", no_profile)
    settings = Settings(artifact_dir=str(tmp_path))
    document = _document()

    assert asyncio.run(ensure_sourcing(document, settings)) is None
    assert document.sourcing is None
    assert not list(Path(tmp_path).rglob("*.json"))


def test_an_incomplete_draft_serves_the_run_but_is_never_frozen(monkeypatch, tmp_path):
    """`draft_companies` never raises - a rate limit hands back an empty list.
    Cached, that empty list (or a missing composed JD, i.e. the raw advert as
    the paste) would be served to every platform on every future run without a
    retry. So a profile with holes works once and is drafted again next time."""
    asked = _stub_drafts(monkeypatch, drafted={"job_description": "Spec."})

    async def no_companies(jd, **kwargs):
        asked["companies_jd"] = jd
        return CompanyTargeting()

    monkeypatch.setattr("app.platforms.targeting_ai.draft_companies", no_companies)
    settings = Settings(artifact_dir=str(tmp_path))
    document = _document()

    profile = asyncio.run(ensure_sourcing(document, settings))
    assert profile is not None
    assert document.sourcing is profile   # this run still uses what it got
    assert profile.path == ""             # but nothing was frozen
    assert not list(Path(tmp_path).rglob("*.json"))

    # Same when the composed JD is the hole: without it, search_jd would fall
    # back to the raw advert - the very defect the profile exists to fix.
    _stub_drafts(monkeypatch, drafted={"job_description": ""})
    bare = _document()
    profile = asyncio.run(ensure_sourcing(bare, settings))
    assert profile is not None and profile.path == ""
    assert not list(Path(tmp_path).rglob("*.json"))


def test_the_draft_is_prompted_with_the_documents_own_title():
    from app.platforms.sourcing_profile import draft_title

    # The advert's title wins; the filename's middle segment stands in; the
    # caller's word is last - so the cached profile does not depend on whether
    # noon or Juicebox drafted first.
    assert draft_title(_document()) == "AI Engineer"
    no_advert = ParsedDocument(source_name="Acme - AI Engineer - London")
    assert draft_title(no_advert) == "AI Engineer"
    assert draft_title(ParsedDocument(), "Fallback Title") == "Fallback Title"


def test_the_location_fallback_reaches_the_company_draft_when_the_jd_is_silent(
    monkeypatch, tmp_path
):
    asked = _stub_drafts(
        monkeypatch, drafted={"candidate_location": None, "job_description": "Spec."}
    )
    settings = Settings(artifact_dir=str(tmp_path))
    asyncio.run(ensure_sourcing(_document(), settings, location="Manchester"))
    assert asked["companies_kwargs"]["location"] == "Manchester"

    # And the JD's own statement still wins over the fallback. A fresh cache
    # dir, because the first half legitimately cached its answer.
    asked = _stub_drafts(monkeypatch, drafted={"job_description": "Spec."})
    settings = Settings(artifact_dir=str(tmp_path / "fresh"))
    asyncio.run(ensure_sourcing(_document(), settings, location="Manchester"))
    assert asked["companies_kwargs"]["location"] == "London"


def test_the_must_have_lines_for_noon_carry_the_years_and_are_capped():
    profile = SourcingProfile(
        must_have_skills=[f"Skill{i}" for i in range(20)],
        min_years=4,
        max_years=8,
    )
    lines = profile.as_must_haves()
    assert lines[0] == "4-8 years of professional experience"
    assert lines[1] == "Experience with Skill0"
    # Capped: every line becomes a starred non-negotiable on noon.
    assert len(lines) == 13
    assert SourcingProfile(min_years=5).as_must_haves() == [
        "5+ years of professional experience"
    ]


def test_the_saved_json_is_readable_by_a_recruiter(monkeypatch, tmp_path):
    """The file is the record of what every search was told - it has to say
    which document it answers and when it was drafted."""
    _stub_drafts(monkeypatch, drafted={"job_description": "Spec."})
    settings = Settings(artifact_dir=str(tmp_path))
    document = _document()
    profile = asyncio.run(ensure_sourcing(document, settings))

    payload = json.loads(Path(profile.path).read_text(encoding="utf-8"))
    assert payload["document"] == "Acme - AI Engineer - London"
    assert payload["drafted_at"]
    assert payload["profile"]["must_have_skills"] == ["Python", "PyTorch"]
    assert payload["profile"]["boolean_search"]
    assert profile_path(document, settings) == Path(profile.path)


def test_recruiter_notes_go_to_the_draft_and_name_the_company(monkeypatch, tmp_path):
    """The notes ride into the Claude call and change the fingerprint; a
    `Company:` note replaces the filename's codename for the company list."""
    from app.documents import parser
    from app.models import Block

    asked = _stub_drafts(monkeypatch)
    settings = Settings(artifact_dir=str(tmp_path))
    document = parser.parse_document([
        Block("heading", 1, "Email 1", "<h1>Email 1</h1>"),
        Block("body", 0, "Hi {{first_name}}.", "<p>Hi {{first_name}}.</p>"),
        Block("heading", 1, "Recruiter Notes", "<h1>Recruiter Notes</h1>"),
        Block("body", 0, "Company: Acme AI", "<p>Company: Acme AI</p>"),
        Block("body", 0, "Poach from Databricks.", "<p>Poach from Databricks.</p>"),
        Block("heading", 1, "Client JD", "<h1>Client JD</h1>"),
        Block("body", 0, "Staff Platform Engineer, SF.", "<p>Staff Platform Engineer, SF.</p>"),
    ])
    document.source_name = "Project Falcon - Staff Platform Engineer - SF"

    profile = asyncio.run(ensure_sourcing(document, settings))

    assert profile is not None
    assert "Poach from Databricks." in asked["notes"]
    assert asked["jd"] == "Staff Platform Engineer, SF."
    assert asked["companies_kwargs"]["company"] == "Acme AI"
    assert fingerprint("x", settings) != fingerprint("x", settings, notes="y")
