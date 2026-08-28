"""Template filters that platforms depend on."""

from __future__ import annotations

from app.utils.templating import render


def test_noon_tokens_rewrites_document_placeholders():
    context = {"email": {"body_html": "<p>Hi {{name}},</p><p>Your work at {{job_company}} and {{ other }}.</p>"}}
    out = render("{{ email.body_html | noon_tokens }}", context)
    assert out == "<p>Hi {first_name},</p><p>Your work at {company} and {other}.</p>"


def test_noon_tokens_leaves_plain_text_alone():
    assert render("{{ email.subject | noon_tokens }}", {"email": {"subject": "Platform Engineer / SF"}}) == "Platform Engineer / SF"
