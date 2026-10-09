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


# -- the funding stage the filters rest on -----------------------------------


AXLE_JD_NO_STAGE = """
Axle Insurance is hiring a Platform Engineer in New York. We run our
infrastructure on AWS and Kubernetes and are scaling the team this year.
Five years of experience with Terraform expected.
"""

AXLE_JD_STATED = AXLE_JD_NO_STAGE + "\nWe are a Series A insurtech.\n"


def _sourcing_run(
    monkeypatch, tmp_path, jd: str, *, dry_run: bool
) -> tuple[list[str], dict]:
    """Drive the real `_set_up_sourcing` with the drafters and the page writer
    stubbed, and return the row's warnings plus the kwargs the writer got.

    The stubs sit where the Claude calls sit now: the shared profile's own
    draft (`sourcing_profile.draft_profile`) and the company draft it still
    routes through `targeting_ai`. Everything between them - `build_profile`,
    the stated-stage detection, `stage_for_filter` - runs for real.
    """
    import asyncio

    from app.config import Settings
    from app.platforms import load_recipes, resolve
    from app.platforms.engine import RunReport
    from app.platforms.juicebox_sourcing import SourcingReport
    from app.platforms.sourcing_profile import DraftProfile
    from app.platforms.targeting_ai import CompanyTargeting

    async def fake_profile(*args, **kwargs):
        return DraftProfile(similar_titles=["Platform Engineer"],
                            must_have_skills=["AWS"],
                            min_years=5, candidate_location="New York")

    async def fake_companies(*args, **kwargs):
        # What the 2026-09-22 run got back: a stage nobody wrote down.
        return CompanyTargeting(stage="Series A", stage_basis="inferred",
                                companies=["Ramp", "Newfront"])

    written: dict = {}

    async def fake_set_up(page, **kwargs):
        written.update(kwargs)
        return SourcingReport(search_url="https://app.juicebox.ai/project/p/search?search_id=S1",
                              saved=True)

    monkeypatch.setattr("app.platforms.sourcing_profile.draft_profile", fake_profile)
    monkeypatch.setattr("app.platforms.targeting_ai.draft_companies", fake_companies)
    monkeypatch.setattr("app.platforms.juicebox_sourcing.set_up_sourcing", fake_set_up)

    settings = Settings(artifact_dir=str(tmp_path))
    adapter = JuiceboxAdapter(
        resolve("juicebox", load_recipes(settings)), settings=settings, dry_run=dry_run
    )
    document = ParsedDocument(
        advert=Advert(title="Platform Engineer", body_text="Join us.", body_html="<p>Join us.</p>"),
        emails=[],
        source_name="Axle Insurance - Platform Engineer",
        client_jd=jd,
    )
    report = RunReport()
    asyncio.run(adapter._set_up_sourcing(None, document, None, report))
    return report.warnings, written


def test_an_inferred_stage_never_reaches_the_funding_stage_filter(monkeypatch, tmp_path):
    """The 2026-09-22 Axle run: the document named no round, Claude inferred
    Series A, and the saved search came back with two Company Funding Stages
    chosen off that guess - under a row that said OK. The guess may still draw
    the Companies list (D-020); it may not set the select (D-022)."""
    warnings, written = _sourcing_run(monkeypatch, tmp_path, AXLE_JD_NO_STAGE, dry_run=False)

    assert written["stage"] is None
    # The company list is still built from the inference - that half is decided.
    assert written["companies"] == ["Ramp", "Newfront"]

    note = next(w for w in warnings if "funding" in w)
    assert "inferred Series A" in note
    # The recruiter is told which filter rests on the guess, which one does
    # not, and what to write in the document to have it set.
    assert "Companies" in note
    assert "left as Juicebox set it" in note
    assert "Client JD" in note


def test_a_stated_stage_does_set_the_funding_stage_filter(monkeypatch, tmp_path):
    """The other half. A stage the client wrote is not a guess, so it sets the
    select - and is not reported on the row at all."""
    warnings, written = _sourcing_run(monkeypatch, tmp_path, AXLE_JD_STATED, dry_run=False)

    assert written["stage"] == "Series A"
    assert not [w for w in warnings if "funding" in w]


def test_the_dry_run_says_which_stages_it_would_set(monkeypatch, tmp_path):
    """The dry run is what a supervised session reads before the live one, so
    it has to name the same two outcomes."""
    inferred, _ = _sourcing_run(monkeypatch, tmp_path, AXLE_JD_NO_STAGE, dry_run=True)
    line = next(w for w in inferred if w.startswith("dry run"))
    assert "stages left as Juicebox set them" in line
    # The companies are still drafted at the inferred stage.
    assert "2 company(ies) at Series A" in line

    stated, _ = _sourcing_run(monkeypatch, tmp_path, AXLE_JD_STATED, dry_run=True)
    line = next(w for w in stated if w.startswith("dry run"))
    assert "stages seed/series_a" in line


# -- the 2026-10-08 editor: read-back comparison and the autosave tap ---------

from app.platforms import juicebox as jb  # noqa: E402

_SUBJECT = "Founding Platform Engineer / Seed / up to $275k + equity"
_BODIES = [
    "<p>Hi {{First Name}},</p><p><br></p><p>I am headhunting for a Platform Engineer at {{Current Company}}'s peer.</p>",
    "<p>Hi {{First Name}},</p><p><br></p><p>Following up with one detail: the team is 5 engineers.</p>",
]


def _stored(title="Acme - Founding Platform Engineer - SF", subject=_SUBJECT, bodies=None):
    """A sequence as GET /api/sequence returns it - the editor's own rewrites
    included: `<p><br></p>` blanks, newlines between blocks, tokens as text."""
    bodies = _BODIES if bodies is None else bodies
    steps = []
    for index, body in enumerate(bodies):
        steps.append({
            "id": f"step{index}", "type": "email", "delayDays": 0 if index == 0 else 2,
            "subject": subject if index == 0 else "",
            "body": body.replace("</p><p>", "</p>\n<p>").replace("&", "&amp;").replace("'", "&#39;"),
        })
    return {"id": "SEQ", "title": title, "subject": "", "steps": steps}


def test_a_sequence_that_saved_as_written_has_no_problems():
    assert jb._saved_problems(_stored(), "Acme - Founding Platform Engineer - SF",
                              _SUBJECT, _BODIES) == []


def test_the_lost_subject_is_named():
    """The first probe's failure: the subject shown in the editor, never saved."""
    problems = jb._saved_problems(_stored(subject=""), "Acme - Founding Platform Engineer - SF",
                                  _SUBJECT, _BODIES)
    assert problems == ["the subject did not save"]


def test_an_empty_or_short_body_and_a_missing_step_are_named():
    saved = _stored(bodies=["<p></p>"])
    problems = jb._saved_problems(saved, "Acme - Founding Platform Engineer - SF", _SUBJECT, _BODIES)
    assert "1 email step(s) saved, the document has 2" in problems
    assert "step 1's body saved empty" in problems

    half = _stored(bodies=[_BODIES[0], "<p>Hi {{First Name}},</p>"])
    problems = jb._saved_problems(half, "Acme - Founding Platform Engineer - SF", _SUBJECT, _BODIES)
    assert any(p.startswith("step 2's body saved incomplete") for p in problems)


def test_a_title_that_kept_the_auto_name_is_named():
    saved = _stored(title="Token - Infra Eng - SF - 10/08/2026")
    problems = jb._saved_problems(saved, "Acme - Founding Platform Engineer - SF", _SUBJECT, _BODIES)
    assert problems == [
        "the title reads 'Token - Infra Eng - SF - 10/08/2026', not "
        "'Acme - Founding Platform Engineer - SF'"
    ]


def test_a_lost_token_counts_as_lost_words():
    assert jb._coverage("<p>Hi ,</p>", "<p>Hi {{First Name}},</p>") < 0.9
    assert jb._coverage("<p>Hi {{First Name}},</p>", "<p>Hi {{First Name}},</p>") == 1.0


def test_records_ride_on_the_failure():
    from app.models import PlatformError
    from app.platforms.engine import RunReport

    report = RunReport()
    report.records["sequence"] = "SEQ"
    report.captures["post_url"] = "https://app.juicebox.ai/project/P/sequences/SEQ"
    exc = jb._with_records(PlatformError("x"), report)
    assert exc.records == {"sequence": "SEQ",
                           "post_url": "https://app.juicebox.ai/project/P/sequences/SEQ"}


class _Req:
    def __init__(self, method, url, token=None):
        self.method, self.url = method, url
        self.headers = {"fbauthorization": token} if token else {}


class _Resp:
    def __init__(self, request):
        self.request, self.url = request, request.url


class _Page:
    def on(self, *a):
        pass

    def remove_listener(self, *a):
        pass


def test_the_tap_keeps_the_latest_token_and_counts_saves_in_flight():
    tap = jb._ApiTap(_Page())
    now = [100.0]
    tap.clock = lambda: now[0]
    tap._on_request(_Req("GET", "https://app.juicebox.ai/api/user", token="t1"))
    tap._on_request(_Req("GET", "https://app.juicebox.ai/api/sequence/list", token="t2"))
    assert tap.token == "t2"

    save = _Req("PATCH", "https://app.juicebox.ai/api/sequence")
    tap._on_request(save)
    assert tap.in_flight() == 1 and not tap.quiet_for(1.5)
    now[0] += 0.5
    tap._on_response(_Resp(save))
    assert tap.in_flight() == 0 and not tap.quiet_for(1.5)
    now[0] += 2.0
    assert tap.quiet_for(1.5)

    # A create or a list read is not an autosave.
    tap._on_request(_Req("POST", "https://app.juicebox.ai/api/sequence"))
    tap._on_request(_Req("PATCH", "https://app.juicebox.ai/api/sequence/other"))
    assert tap.sent == 1
    assert jb._is_create(_Resp(_Req("POST", "https://app.juicebox.ai/api/sequence?x=1")))
