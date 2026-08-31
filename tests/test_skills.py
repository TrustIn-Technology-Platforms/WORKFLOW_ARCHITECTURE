"""Where the advert's skills list comes from, and what it refuses to invent.

The list feeds Wellfound's Skills tag field. Two rules are worth pinning: a
recruiter's own list is read as written, and nothing is guessed when there is no
key - the field is optional, so an empty list is the correct answer.
"""

from __future__ import annotations

import asyncio

from app.models import Advert, ParsedDocument
from app.platforms.skills import MAX_SKILLS, ensure_skills, split_skills


def _document(tags: list[str] | None = None, body: str = "Python and Go.") -> ParsedDocument:
    return ParsedDocument(
        advert=Advert(
            title="Platform Engineer",
            body_text=body,
            body_html=f"<p>{body}</p>",
            tags=list(tags or []),
        ),
        emails=[],
    )


def test_split_skills_separates_on_commas_semicolons_pipes_and_newlines():
    assert split_skills("Python, Go; Rust | Kubernetes\nTerraform") == [
        "Python",
        "Go",
        "Rust",
        "Kubernetes",
        "Terraform",
    ]


def test_split_skills_keeps_a_slash_inside_a_name():
    """`CI/CD` and `TCP/IP` are single skills. Splitting on `/` would break both,
    and a recruiter who separates with slashes still separates with commas."""
    assert split_skills("CI/CD, TCP/IP") == ["CI/CD", "TCP/IP"]


def test_split_skills_drops_bullets_blanks_and_case_duplicates():
    assert split_skills("- Python,, python , • Go,") == ["Python", "Go"]


def test_split_skills_caps_the_list():
    many = ", ".join(f"Skill{n}" for n in range(30))
    assert len(split_skills(many)) == MAX_SKILLS
    assert len(split_skills(many, limit=3)) == 3


def test_ensure_skills_leaves_a_list_that_is_already_there(monkeypatch):
    """The Notion column has already run by this point. It wins, and no API call
    is made - which is also what keeps a run cheap."""
    called = False

    async def _fail(*args, **kwargs):
        nonlocal called
        called = True
        return ["Invented"]

    monkeypatch.setattr("app.platforms.skills.draft_skills", _fail)
    document = _document(tags=["Python", "Go"])
    added = asyncio.run(ensure_skills(document))
    assert added == []
    assert document.advert.tags == ["Python", "Go"]
    assert not called, "a populated list must not be re-drafted"


def test_ensure_skills_without_an_api_key_leaves_the_list_empty(monkeypatch):
    """No key is not an error. Wellfound's Skills field is optional, and an
    advert tagged with nothing is better than one tagged with guesses."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.config import reset_settings_cache

    reset_settings_cache()
    document = _document()
    added = asyncio.run(ensure_skills(document))
    reset_settings_cache()
    assert added == []
    assert document.advert.tags == []


def test_ensure_skills_writes_the_drafted_list_onto_the_advert(monkeypatch):
    async def _draft(advert_text, *, title="", settings=None):
        assert "Python" in advert_text
        return ["Python", "Go"]

    monkeypatch.setattr("app.platforms.skills.draft_skills", _draft)
    document = _document()
    added = asyncio.run(ensure_skills(document))
    assert added == ["Python", "Go"]
    assert document.advert.tags == ["Python", "Go"]


def test_ensure_skills_does_nothing_without_an_advert(monkeypatch):
    added = asyncio.run(ensure_skills(ParsedDocument(advert=None, emails=[])))
    assert added == []
