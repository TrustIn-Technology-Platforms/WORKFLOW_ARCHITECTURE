"""Wellfound pitch template: which message becomes the pitch, and how it is cleaned."""

from __future__ import annotations

from app.models import Advert, EmailStep, ParsedDocument
from app.platforms.wellfound_pitch import clean_body, match_template, pitch_from


def _step(channel: str, body: str, *, order: int = 1, subject: str = "", label: str = "") -> EmailStep:
    return EmailStep(order=order, subject=subject, body_text=body, body_html="", channel=channel, label=label)


def _document(*steps: EmailStep, title: str = "Platform Engineer / Series A") -> ParsedDocument:
    return ParsedDocument(advert=Advert(title=title, body_text="", body_html=""), emails=list(steps))


def test_greeting_and_sign_off_are_dropped_and_paragraphs_kept():
    body = "Hi {first_name},\n\nWe're hiring an SRE.\n\nOpen to a call?\n\nBest,\nMarcus"
    assert clean_body(body) == "We're hiring an SRE.\n\nOpen to a call?"


def test_a_sign_off_with_no_name_under_it_is_dropped():
    assert clean_body("Hello,\n\nA role.\n\nYours Sincerely,") == "A role."


def test_a_first_line_that_is_the_message_is_kept():
    # Only a short salutation counts as a greeting.
    assert clean_body("Hiring a platform lead at a Series A insurtech.\n\nMore.").startswith("Hiring a platform lead")


def test_the_inmail_is_the_pitch_when_the_document_has_one():
    document = _document(
        _step("linkedin", "Hi {first_name},\n\nShort note.", label="LinkedIn Connection"),
        _step("email", "Hi {first_name},\n\nEmail one.", label="Email1"),
        _step("inmail", "Hi {first_name},\n\nThe full cold message.", label="InMail"),
    )
    pitch = pitch_from(document)
    assert pitch is not None
    assert pitch.body == "The full cold message."
    assert pitch.source == "InMail"
    assert pitch.title == "Platform Engineer / Series A"
    assert pitch.warnings == []


def test_a_document_without_an_inmail_falls_back_and_says_so():
    document = _document(_step("email", "Hi there,\n\nEmail one.", label="Email1"))
    pitch = pitch_from(document)
    assert pitch is not None and pitch.body == "Email one."
    assert any("no InMail" in warning for warning in pitch.warnings)


def test_a_merge_token_left_in_the_message_is_warned_about():
    document = _document(_step("inmail", "Hi {first_name},\n\nYour time at {company} stood out."))
    pitch = pitch_from(document)
    assert pitch is not None
    assert any("{company}" in warning for warning in pitch.warnings)


def test_an_advert_only_document_has_no_pitch():
    assert pitch_from(_document()) is None


def test_the_board_adverts_title_names_the_template():
    document = _document(_step("inmail", "A message."), title="General title")
    document.platform_adverts["wellfound"] = Advert(title="Wellfound title", body_text="", body_html="")
    assert pitch_from(document).title == "Wellfound title"


TEMPLATES = [
    {"id": "1", "kind": "PITCH", "title": "SRE / Series A", "updatedAt": 10},
    {"id": "2", "kind": "PITCH", "title": "sre / series a", "updatedAt": 20},
    {"id": "3", "kind": "REJECT", "title": "SRE / Series A", "updatedAt": 30},
]


def test_a_template_is_found_by_title_among_pitch_templates_only_newest_first():
    assert match_template(TEMPLATES, title="SRE / Series A")["id"] == "2"


def test_the_recorded_id_wins_over_the_title():
    assert match_template(TEMPLATES, title="something else", template_id="1")["id"] == "1"


def test_a_reject_template_is_never_matched_even_by_id():
    assert match_template(TEMPLATES, title="nothing", template_id="3") is None
