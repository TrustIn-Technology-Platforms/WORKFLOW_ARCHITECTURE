"""The fetcher's pure part: the filename a share link hands back.

It is the sequence, project and search name on every platform, so a raw
Content-Disposition value shows up in front of recruiters - as it did on
2026-09-03 with "Firecrawl %C2%B7 Backend Infrastructure Engineer ny".
"""

from __future__ import annotations

from app.documents.fetcher import _filename_from


class _Response:
    def __init__(self, disposition: str) -> None:
        self.headers = {"content-disposition": disposition} if disposition else {}


def test_percent_encoded_filename_is_decoded():
    response = _Response(
        'attachment; filename="Firecrawl %C2%B7 Backend Infrastructure Engineer ny.docx"'
    )
    assert _filename_from(response) == "Firecrawl · Backend Infrastructure Engineer ny.docx"


def test_rfc5987_filename_star_wins_over_the_ascii_fallback():
    response = _Response(
        'attachment; filename="Firecrawl _ Backend.docx"; '
        "filename*=UTF-8''Firecrawl%20%C2%B7%20Backend.docx"
    )
    assert _filename_from(response) == "Firecrawl · Backend.docx"


def test_plain_names_pass_through_and_no_header_is_none():
    assert _filename_from(_Response('inline; filename="Axle Insurance - Platform Eng.docx"')) == (
        "Axle Insurance - Platform Eng.docx"
    )
    assert _filename_from(_Response("")) is None
