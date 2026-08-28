"""The Juicebox driver's offline logic: token rewriting, naming, URL, dispatch.

The page-driving itself needs a live TinyMCE editor and is covered by hand
against the real app (docs/platforms/juicebox.md). Everything here runs without
a browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Advert, EmailStep, ParsedDocument, NotionRow, PipelineError
from app.platforms import get_adapter, load_recipe
from app.platforms.juicebox import JuiceboxAdapter, _sequence_name, _sequence_url
from app.utils.templating import juicebox_tokens


# -- token rewriting ---------------------------------------------------------


def test_single_brace_tokens_become_juicebox_labels():
    assert juicebox_tokens("Hi {first_name},") == "Hi {{First Name}},"
    assert juicebox_tokens("at {company} now") == "at {{Current Company}} now"


def test_double_brace_document_tokens_are_normalised_too():
    assert juicebox_tokens("Hi {{name}}, at {{job_company}}") == (
        "Hi {{First Name}}, at {{Current Company}}"
    )


def test_unmapped_token_keeps_its_words_spaced_and_titled():
    assert juicebox_tokens("your {weird_token}") == "your {{Weird Token}}"


def test_already_converted_output_is_not_double_processed():
    once = juicebox_tokens("Hi {first_name},")
    assert juicebox_tokens(once) == once


def test_plain_text_is_untouched():
    assert juicebox_tokens("Platform Engineer / SF") == "Platform Engineer / SF"


# -- sequence naming ---------------------------------------------------------


def _doc(advert_title: str, subject: str) -> ParsedDocument:
    advert = Advert(title=advert_title, body_text="", body_html="")
    email = EmailStep(order=1, subject=subject, body_text="x", body_html="<p>x</p>")
    return ParsedDocument(advert=advert, emails=[email])


def test_name_prefers_the_notion_row_title():
    doc = _doc("Hi {first_name},", "Cloud Infra Engineer / SF")
    row = NotionRow(
        page_id="p",
        title="Judgment Labs - Cloud Infra Eng",
        document_url=None,
        status="Working On",
    )
    assert _sequence_name(doc, row, doc.emails) == "Judgment Labs - Cloud Infra Eng"


def test_name_falls_back_to_subject_when_advert_is_a_greeting():
    # The parser uses the email opener as the advert when there is no heading,
    # so 'Hi {first_name},' must not become the sequence name.
    doc = _doc("Hi {first_name},", "Cloud Infra Engineer / SF")
    name = _sequence_name(doc, None, doc.emails)
    assert name == "Cloud Infra Engineer / SF"


def test_name_uses_a_real_advert_title_when_there_is_one():
    doc = _doc("Staff Platform Engineer", "some subject")
    assert _sequence_name(doc, None, doc.emails) == "Staff Platform Engineer"


# -- created-sequence URL ----------------------------------------------------


def test_sequence_url_points_at_the_created_sequence():
    url = "https://app.juicebox.ai/project/ABC/sequences?step=edit&templateId=1&createdSequenceId=XYZ9"
    assert _sequence_url(url) == "https://app.juicebox.ai/project/ABC/sequences/XYZ9"


def test_sequence_url_falls_back_to_the_raw_url():
    url = "https://app.juicebox.ai/project/ABC/sequences"
    assert _sequence_url(url) == url


# -- recipe wiring -----------------------------------------------------------


def test_driver_recipe_dispatches_to_the_juicebox_adapter():
    recipe = load_recipe(Path("platforms/juicebox.yaml"))
    assert recipe.driver == "juicebox"
    assert recipe.enabled is True
    adapter = get_adapter("juicebox", recipes={recipe.key: recipe}, dry_run=True)
    assert isinstance(adapter, JuiceboxAdapter)


def test_juicebox_has_no_signature():
    # The signature belongs to Loxo, not Juicebox (corrected 2026-08-28).
    recipe = load_recipe(Path("platforms/juicebox.yaml"))
    assert "signature_html" not in recipe.defaults


def test_unknown_driver_is_a_clear_error(tmp_path):
    path = tmp_path / "weird.yaml"
    path.write_text(
        "key: weird\nlabel: Weird\nkind: email_sequence\nenabled: true\n"
        "driver: nope\nlogin:\n  url: https://example.com/\n",
        encoding="utf-8",
    )
    recipe = load_recipe(path)  # a driver recipe skips step-shape validation
    with pytest.raises(PipelineError) as caught:
        get_adapter("weird", recipes={recipe.key: recipe})
    assert "nope" in str(caught.value)
