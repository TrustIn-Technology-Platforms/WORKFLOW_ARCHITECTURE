"""Download the document behind a share link and prove it is really a document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.documents import sharelinks
from app.logging_conf import get_logger
from app.models import DocumentFetchError

log = get_logger(__name__)

# Magic bytes let us reject a login page that came back with HTTP 200.
_ZIP_MAGIC = b"PK\x03\x04"          # .docx is a zip container
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"    # legacy .doc
_PDF_MAGIC = b"%PDF"
_RTF_MAGIC = b"{\\rt"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
}


@dataclass(slots=True)
class FetchedDocument:
    content: bytes
    kind: str            # docx | doc | pdf | rtf | text
    source_url: str
    strategy: str
    filename: str | None = None

    @property
    def size(self) -> int:
        return len(self.content)


class DocumentFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedDocument: ...


def sniff_kind(content: bytes, content_type: str = "") -> str | None:
    """Identify the payload from its bytes, falling back to the declared type."""
    head = content[:8]
    if head.startswith(_ZIP_MAGIC):
        return "docx"
    if head.startswith(_OLE_MAGIC):
        return "doc"
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if head.startswith(_RTF_MAGIC):
        return "rtf"

    lowered = (content_type or "").lower()
    if "wordprocessingml" in lowered:
        return "docx"
    if "msword" in lowered:
        return "doc"
    if "pdf" in lowered:
        return "pdf"

    stripped = content[:512].lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<?xml")) or "text/html" in lowered:
        return None  # a viewer or sign-in page, not the file
    if "text/plain" in lowered or "markdown" in lowered:
        return "text"
    return None


class ShareLinkFetcher:
    """Fetches anonymous ("anyone with the link") documents over plain HTTPS.

    Each candidate URL from `sharelinks.candidates` is tried in turn; the first
    response whose *bytes* look like a document wins. HTML responses are treated
    as failures even at HTTP 200, because that is what a sign-in wall returns.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def fetch(self, url: str) -> FetchedDocument:
        attempts = sharelinks.candidates(url)
        if not attempts:
            raise DocumentFetchError("No document URL was provided on the row.")

        problems: list[str] = []
        limits = httpx.Limits(max_connections=4)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.settings.document_timeout_seconds,
            headers=BROWSER_HEADERS,
            limits=limits,
        ) as client:
            for candidate in attempts:
                try:
                    document = await self._try(client, candidate)
                except DocumentFetchError as exc:
                    problems.append(f"{candidate.strategy}: {exc}")
                    continue
                if document is not None:
                    log.info(
                        "document downloaded",
                        extra={
                            "strategy": candidate.strategy,
                            "kind": document.kind,
                            "bytes": document.size,
                        },
                    )
                    return document
                problems.append(f"{candidate.strategy}: response was not a document")

        raise DocumentFetchError(
            "Could not download the document. The link may not be shared with "
            '"anyone with the link", or it may have expired. Tried -> '
            + "; ".join(problems[:5])
        )

    async def _try(
        self, client: httpx.AsyncClient, candidate: sharelinks.Candidate
    ) -> FetchedDocument | None:
        try:
            response = await client.get(candidate.url)
        except httpx.HTTPError as exc:
            raise DocumentFetchError(str(exc)) from exc

        if response.status_code >= 400:
            raise DocumentFetchError(f"HTTP {response.status_code}")

        content = response.content
        if len(content) > self.settings.document_max_bytes:
            raise DocumentFetchError(
                f"Document is {len(content)} bytes, over the "
                f"{self.settings.document_max_bytes} byte limit."
            )

        kind = sniff_kind(content, response.headers.get("content-type", ""))
        if kind is None:
            return None

        return FetchedDocument(
            content=content,
            kind=kind,
            source_url=str(response.url),
            strategy=candidate.strategy,
            filename=_filename_from(response),
        )


def _filename_from(response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip().strip('"') or None
    return None


def build_fetcher(settings: Settings | None = None) -> DocumentFetcher:
    """Single place to swap in a Graph-API fetcher later without touching callers."""
    return ShareLinkFetcher(settings)
