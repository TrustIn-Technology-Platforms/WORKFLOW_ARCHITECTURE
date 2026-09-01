"""Template filters that platforms depend on."""

from __future__ import annotations

import pytest

from app.utils.templating import render


def test_noon_tokens_rewrites_document_placeholders():
    context = {"email": {"body_html": "<p>Hi {{name}},</p><p>Your work at {{job_company}} and {{ other }}.</p>"}}
    out = render("{{ email.body_html | noon_tokens }}", context)
    assert out == "<p>Hi {first_name},</p><p>Your work at {company} and {other}.</p>"


def test_noon_tokens_leaves_plain_text_alone():
    assert render("{{ email.subject | noon_tokens }}", {"email": {"subject": "Platform Engineer / SF"}}) == "Platform Engineer / SF"


# ----------------------------------------------------------------------
# Wellfound-shaped filters: Markdown description, salary split
# ----------------------------------------------------------------------


def test_markdown_keeps_bold_labels_and_bullets():
    """The docx reader's HTML becomes EasyMDE Markdown with structure intact.

    Wellfound's description box is a Markdown editor, so a pasted <ul> would
    show its tags. Bold labels, bullets and paragraphs are what make an advert
    readable, and they are exactly what has to survive.
    """
    from app.utils.templating import html_to_markdown

    html = (
        "<p><strong>About Company:</strong></p>"
        "<p>An early-stage AI company with <em>real</em> traction.</p>"
        "<p><strong>What you'll do:</strong></p>"
        "<ul><li>Build the platform</li><li>Run <strong>AWS</strong> infra</li></ul>"
        "<h2>Requirements</h2><ol><li>Kubernetes</li><li>Terraform</li></ol>"
    )
    assert html_to_markdown(html) == (
        "**About Company:**\n\n"
        "An early-stage AI company with *real* traction.\n\n"
        "**What you'll do:**\n\n"
        "- Build the platform\n- Run **AWS** infra\n\n"
        "## Requirements\n\n"
        "1. Kubernetes\n2. Terraform"
    )


def test_markdown_handles_loose_text_links_and_nested_lists():
    from app.utils.templating import html_to_markdown

    html = "Intro text <a href='https://x.io'>site</a><ul><li>a<ul><li>b</li></ul></li></ul>"
    assert html_to_markdown(html) == "Intro text [site](https://x.io)\n\n- a\n  - b"
    assert html_to_markdown("") == ""
    assert html_to_markdown("<p>  </p>") == ""


def test_markdown_filter_is_reachable_from_a_recipe():
    ctx = {"advert": {"body_html": "<p><strong>Role</strong></p><ul><li>x</li></ul>"}}
    assert render("{{ advert.body_html | markdown }}", ctx) == "**Role**\n\n- x"


@pytest.mark.parametrize(
    "text, low, high",
    [
        ("$180k-$220k", "180000", "220000"),
        ("$180-220k", "180000", "220000"),
        ("180,000 - 220,000 USD", "180000", "220000"),
        ("Up to $350k", "350000", "350000"),
        ("£90k", "90000", "90000"),
        ("Competitive", "", ""),
        ("", "", ""),
        # Wellfound caps the spread at 80k: narrowed from the bottom, not rejected.
        ("$150k - $300k", "220000", "300000"),
        ("$600/day", "", ""),
    ],
)
def test_salary_bounds(text, low, high):
    ctx = {"advert": {"salary": text}}
    assert render("{{ advert.salary | salary_min }}", ctx) == low
    assert render("{{ advert.salary | salary_max }}", ctx) == high


@pytest.mark.parametrize(
    "text, code",
    [("£90k", "GBP"), ("$180k", "USD"), ("€70,000", "EUR"), ("180k gbp", "GBP"), ("180k", "")],
)
def test_salary_currency(text, code):
    assert render("{{ advert.salary | salary_currency }}", {"advert": {"salary": text}}) == code


# ----------------------------------------------------------------------
# years_min - Wellfound's "Work experience" dropdown
# ----------------------------------------------------------------------


def test_years_min_reads_a_plus_figure():
    assert render("{{ a | years_min }}", {"a": "15+ years in engineering"}) == "10"
    assert render("{{ a | years_min }}", {"a": "3+ years of Python"}) == "3"


def test_years_min_takes_the_low_end_of_a_range():
    """`5-8 years` is a floor of five, not eight."""
    assert render("{{ a | years_min }}", {"a": "5-8 years' experience"}) == "5"


def test_years_min_takes_the_headline_figure_across_several_mentions():
    advert = "3+ years with Kubernetes. 12+ years in engineering overall."
    assert render("{{ a | years_min }}", {"a": advert}) == "10"


def test_years_min_ignores_numbers_that_are_not_years():
    """A bare number in an advert is a salary or a funding round far more often
    than a length of service, so the unit has to be there."""
    assert render("{{ a | years_min }}", {"a": "Backed by a $120M round"}) == ""
    assert render("{{ a | years_min }}", {"a": "A team of 40 engineers"}) == ""


def test_years_min_is_empty_when_the_advert_states_nothing():
    assert render("{{ a | years_min }}", {"a": "We move fast."}) == ""


# ----------------------------------------------------------------------
# {ai_intro} - noon's token, nobody else's
# ----------------------------------------------------------------------


def test_juicebox_removes_the_ai_intro_paragraph_entirely():
    """Title-casing it would invent an {{Ai Intro}} field Juicebox rejects, and
    leaving it sends '{{ai_intro}}' as the first line of a real email."""
    from app.utils.templating import juicebox_tokens

    html = ("<p>Hi {{first_name}},</p>\n<p>{{ai_intro}}</p>\n"
            "<p>I am recruiting for a Series A startup.</p>")
    out = juicebox_tokens(html)
    assert "ai_intro" not in out.lower()
    assert "Ai Intro" not in out
    assert "<p>Hi {{First Name}},</p>" in out
    assert "Series A startup" in out
    assert "<p></p>" not in out, "the emptied paragraph goes with it"


def test_drop_ai_intro_handles_the_inline_form_too():
    from app.utils.templating import drop_ai_intro

    assert drop_ai_intro("Hi {first_name}, {{ai_intro}} I saw your work.") == \
        "Hi {first_name}, I saw your work."
    assert drop_ai_intro("<p>{ai intro}</p><p>Body.</p>") == "<p>Body.</p>"


def test_noon_keeps_ai_intro_as_its_own_single_brace_token():
    assert render("{{ a | noon_tokens }}", {"a": "<p>{{ai_intro}}</p>"}) == "<p>{ai_intro}</p>"
