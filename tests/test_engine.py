"""End-to-end engine test against a local page that behaves like a real editor.

This is the test that matters before a live platform is wired up: it proves the
recipe format, the templating, the per-email loop and - most importantly - that
formatted HTML survives the trip into a contenteditable editor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import get_settings, reset_settings_cache
from app.documents import parser
from app.documents.docx_reader import read_blocks
from app.models import Advert, EmailStep, ParsedDocument
from app.platforms.browser import BrowserRunner
from app.platforms.engine import RecipeEngine
from app.platforms.recipe import load_recipe

FIXTURES = Path(__file__).parent / "fixtures"
RECIPE = FIXTURES / "recipes" / "mock.yaml"


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("HEADLESS", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _document() -> ParsedDocument:
    return ParsedDocument(
        advert=Advert(
            title="Senior Recruitment Consultant",
            body_text="We are hiring.\n\nYou will own a desk.",
            body_html="<p>We are hiring.</p>\n<p>You will <strong>own a desk</strong>.</p>",
            location="Manchester (hybrid)",
            employment_type="Permanent",
        ),
        emails=[
            EmailStep(
                order=1,
                subject="Senior Recruitment Consultant - Manchester",
                body_text="Hi there,\n\nWorth a conversation?",
                body_html="<p>Hi there,</p>\n<p>Worth a <em>conversation</em>?</p>",
            ),
            EmailStep(
                order=2,
                subject="Following up",
                body_text="Circling back.",
                body_html=(
                    "<p>Circling back.</p>\n<ul>\n<li>Hybrid working</li>\n"
                    "<li>Clear progression</li>\n</ul>"
                ),
                delay_days=3,
            ),
        ],
    )


async def _run(page_url: str, dry_run: bool = False) -> tuple[dict, list[dict]]:
    recipe = load_recipe(RECIPE)
    settings = get_settings()

    async with BrowserRunner(settings, headless=True) as runner:
        async with runner.context() as (context, page):
            await page.goto(page_url)
            engine = RecipeEngine(recipe, page, settings, dry_run=dry_run)
            report = await engine.run(_document())

            steps = await page.evaluate(
                """() => [...document.querySelectorAll('#steps li')].map((li) => ({
                    subject: li.dataset.subject,
                    delay: li.dataset.delay,
                    body: li.dataset.body,
                }))"""
            )
            state = {
                "title": await page.input_value("#title"),
                "location": await page.input_value("#location"),
                "committed": await page.get_attribute("#location", "data-committed"),
                "employment": await page.input_value("#employmentType"),
                "description": await page.inner_html("#description"),
                "banner_visible": await page.is_visible("#saved-banner"),
                "url": page.url,
                "emails_written": report.emails_written,
                "submitted": report.submitted,
                "captures": dict(report.captures),
                "warnings": list(report.warnings),
            }
            return state, steps


def test_recipe_is_valid():
    recipe = load_recipe(RECIPE)
    assert recipe.kind == "email_sequence"
    assert len(recipe.per_email) == 7
    assert sum(1 for s in recipe.all_steps if s.submit) == 1


def test_full_run_writes_every_email(page_url):
    state, steps = asyncio.run(_run(page_url, dry_run=False))

    assert state["title"] == "Senior Recruitment Consultant"
    assert state["location"] == "Manchester (hybrid)"
    assert state["committed"] == "true", "combobox must commit by clicking an option"
    assert state["employment"] == "FULL_TIME", "map: Permanent -> FULL_TIME"

    # The advert body kept its formatting through the paste.
    assert "<strong>own a desk</strong>" in state["description"]

    assert state["emails_written"] == 2
    assert len(steps) == 2

    assert steps[0]["subject"] == "Senior Recruitment Consultant - Manchester"
    assert "<em>conversation</em>" in steps[0]["body"]
    assert steps[0]["delay"] == "", "no delay set means the field stays untouched"

    assert steps[1]["subject"] == "Following up"
    assert "<li>Hybrid working</li>" in steps[1]["body"], "bullet list must survive"
    assert steps[1]["delay"] == "3"

    assert state["submitted"] is True
    assert state["banner_visible"] is True
    assert "sequence=live" in state["captures"]["post_url"]


def test_dry_run_stops_at_submit(page_url):
    state, steps = asyncio.run(_run(page_url, dry_run=True))

    # Everything before the submit still ran against the real page - which is
    # what makes a dry run worth anything.
    assert state["emails_written"] == 2
    assert len(steps) == 2
    assert "<li>Hybrid working</li>" in steps[1]["body"]

    assert state["submitted"] is False
    assert state["banner_visible"] is False
    assert any("stopped before" in w for w in state["warnings"])


def test_parser_against_sample_docx():
    sample = FIXTURES / "documents" / "sample.docx"
    if not sample.exists():
        pytest.skip("run scripts/make_sample_docx.py first")

    document = parser.parse_document(read_blocks(sample.read_bytes()))

    assert document.advert is not None
    assert document.advert.title == "Senior Recruitment Consultant"
    assert document.advert.location == "Manchester (hybrid)"
    assert document.advert.reference == "TR-4471"
    assert document.advert.fields.get("Start Date") == "Immediate"
    assert "<strong>building and owning your own desk</strong>" in document.advert.body_html

    assert [e.order for e in document.emails] == [1, 2, 3]
    assert document.emails[0].subject == "Senior Recruitment Consultant - Manchester"
    assert document.emails[1].delay_days == 3
    assert document.emails[2].delay_days == 7
    assert "<ul>" in document.emails[1].body_html


# ---------------------------------------------------------------------------
# build_context: fixed-slot platforms (noon) must see email-channel steps only,
# and get a usable role name even when the document has no advert. Regression
# for the multi-channel parser shifting emails[0] onto a LinkedIn step.
# ---------------------------------------------------------------------------
def _multichannel_document() -> ParsedDocument:
    from app.models import Block

    def h(t):
        return Block("heading", 2, t, f"<h2>{t}</h2>")

    def b(t):
        return Block("body", 0, t, f"<p>{t}</p>")

    return parser.parse_document([
        h("LinkedIn Connection"), b("Hi {first_name}, connecting."),
        h("Subject"), b("Staff Platform Engineer / up to $350k"),
        h("Email1"), b("Hi {first_name}, first email."),
        h("Email2"), b("Hi {first_name}, second email."),
        h("Email3"), b("Hi {first_name}, third email."),
        h("InMail"), b("Hi {first_name}, an InMail."),
    ])


def test_build_context_emails_are_email_channel_only():
    from app.platforms.engine import build_context

    ctx = build_context(_multichannel_document(), None, {})

    # emails[] is what a noon slot indexes into: the three real emails, in order,
    # with the LinkedIn note and InMail excluded so no index is shifted.
    assert ctx["email_count"] == 3
    bodies = [e["body_text"] for e in ctx["emails"]]
    assert bodies == [
        "Hi {first_name}, first email.",
        "Hi {first_name}, second email.",
        "Hi {first_name}, third email.",
    ]
    # The full sequence stays reachable for a recipe that wants the other channels.
    assert len(ctx["steps"]) == 5
    assert [s["channel"] for s in ctx["steps"]] == [
        "linkedin", "email", "email", "email", "inmail",
    ]


def test_build_context_role_name_falls_back_for_emails_only_document():
    from app.models import NotionRow
    from app.platforms.engine import build_context

    doc = _multichannel_document()
    assert doc.advert is None  # emails-only: no advert title to name a role from

    # No row: the shared subject stands in, never the LinkedIn greeting.
    assert build_context(doc, None, {})["role_name"] == "Staff Platform Engineer / up to $350k"

    # With a row, its title is the role name.
    row = NotionRow(
        page_id="p", title="Abundant - Staff Platform Engineer - SF-DUB",
        url="", document_url="", status="", platforms=[], raw_properties={},
    )
    assert build_context(doc, row, {})["role_name"] == "Abundant - Staff Platform Engineer - SF-DUB"


def test_channel_slots_use_document_copy_with_email_fallback():
    """noon's InMail slot and connection message take the document's own
    channel sections when present, and fall back to email 1 for the
    emails-only documents that posted before channels existed."""
    from app.platforms.engine import build_context

    ctx = build_context(_multichannel_document(), None, {})
    assert ctx["inmail"]["body_text"] == "Hi {first_name}, an InMail."
    assert ctx["connection_note"]["body_text"] == "Hi {first_name}, connecting."

    from app.models import Block

    emails_only = parser.parse_document([
        Block("heading", 2, "Email 1", "<h2>Email 1</h2>"),
        Block("body", 0, "Hi there, only email.", "<p>Hi there, only email.</p>"),
    ])
    ctx2 = build_context(emails_only, None, {})
    assert ctx2["inmail"]["body_text"] == "Hi there, only email."
    assert ctx2["connection_note"]["body_text"] == "Hi there, only email."
