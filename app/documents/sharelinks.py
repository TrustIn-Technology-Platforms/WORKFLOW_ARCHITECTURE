"""Turn a shared document link into a direct-download URL.

The `final_document` column holds an "anyone with the link" URL, which serves a
viewer page rather than the file. Microsoft, Google and Dropbox each expose a
different way to reach the bytes, so this module produces an ordered list of
candidate URLs and the fetcher tries them until one returns a real document.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ONEDRIVE_SHARES_API = "https://api.onedrive.com/v1.0/shares/{token}/root/content"

_MS_HOST_HINTS = ("sharepoint.com", "onedrive.live.com", "1drv.ms", "-my.sharepoint.com")
_GOOGLE_DOC_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")
_GOOGLE_FILE_RE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")


@dataclass(frozen=True, slots=True)
class Candidate:
    url: str
    strategy: str


def encode_sharing_url(url: str) -> str:
    """Microsoft's documented share-link encoding: u! + unpadded base64url."""
    encoded = base64.b64encode(url.encode("utf-8")).decode("ascii")
    return "u!" + encoded.rstrip("=").replace("/", "_").replace("+", "-")


def is_microsoft(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(hint in host for hint in _MS_HOST_HINTS)


def with_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        query[key] = [value]
    flat = urlencode({k: v[-1] for k, v in query.items()})
    return urlunparse(parsed._replace(query=flat))


def _site_path(parts: list[str]) -> str:
    """The site collection a document belongs to.

    /personal/<user> for OneDrive for Business, /sites/<name> for a team site.
    Everything under `_layouts` hangs off this, so getting it wrong produces a
    404 that looks exactly like a permissions failure.
    """
    for marker in ("personal", "sites", "teams"):
        if marker in parts:
            index = parts.index(marker)
            return "/" + "/".join(parts[index : index + 2])
    return ""


def sharepoint_download_url(url: str) -> str | None:
    """Rewrite a SharePoint viewer link to its download.aspx equivalent.

    Two link shapes are in circulation and they need different handling:

      short token   /:w:/g/personal/<user>/EaBc123?e=xyz
      Doc.aspx      /:w:/r/personal/<user>/_layouts/15/Doc.aspx?sourcedoc={GUID}

    The second is what the Word web app puts in the address bar, so it is the
    one people copy most often.
    """
    parsed = urlparse(url)
    if "sharepoint.com" not in (parsed.hostname or "").lower():
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None

    site_path = _site_path(parts)
    query = parse_qs(parsed.query, keep_blank_values=True)

    # Doc.aspx carries the document's own id, which download.aspx accepts
    # directly - there is no share token in this shape at all.
    if parts[-1].lower() == "doc.aspx":
        sourcedoc = (query.get("sourcedoc") or [""])[0]
        if not sourcedoc:
            return None
        return urlunparse(
            parsed._replace(
                path=f"{site_path}/_layouts/15/download.aspx",
                query=urlencode({"sourcedoc": sourcedoc}),
            )
        )

    if not parts[0].startswith(":"):
        return None

    share_token = parts[-1]
    if not share_token or share_token.startswith(":") or "." in share_token:
        return None

    return urlunparse(
        parsed._replace(
            path=f"{site_path}/_layouts/15/download.aspx",
            query=urlencode({"share": share_token}),
        )
    )


def sharepoint_source_url(url: str) -> str | None:
    """download.aspx by file path, for links that name the file but not its id."""
    parsed = urlparse(url)
    if "sharepoint.com" not in (parsed.hostname or "").lower():
        return None

    query = parse_qs(parsed.query, keep_blank_values=True)
    filename = (query.get("file") or [""])[0]
    if not filename:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    site_path = _site_path(parts)
    if not site_path:
        return None

    # Documents is the default library name on a personal site.
    source = f"{site_path}/Documents/{filename}"
    return urlunparse(
        parsed._replace(
            path=f"{site_path}/_layouts/15/download.aspx",
            query=urlencode({"SourceUrl": source}),
        )
    )


def google_export_url(url: str) -> str | None:
    """Export a Google Doc or Drive file as .docx."""
    doc = _GOOGLE_DOC_RE.search(url)
    if doc:
        return (
            f"https://docs.google.com/document/d/{doc.group(1)}/export?format=docx"
        )
    drive = _GOOGLE_FILE_RE.search(url)
    if drive:
        return f"https://drive.google.com/uc?export=download&id={drive.group(1)}"
    return None


def dropbox_direct_url(url: str) -> str | None:
    if "dropbox.com" not in (urlparse(url).hostname or "").lower():
        return None
    return with_query(url, dl="1")


def candidates(url: str) -> list[Candidate]:
    """Ordered download attempts, most reliable first.

    Order matters: the shares API returns clean bytes when it works, whereas a
    plain GET on a viewer link returns an HTML page that only looks like success.
    """
    url = (url or "").strip()
    if not url:
        return []

    found: list[Candidate] = []

    def add(candidate_url: str | None, strategy: str) -> None:
        if not candidate_url:
            return
        if any(c.url == candidate_url for c in found):
            return
        found.append(Candidate(candidate_url, strategy))

    if is_microsoft(url):
        add(ONEDRIVE_SHARES_API.format(token=encode_sharing_url(url)), "shares-api")
        add(sharepoint_download_url(url), "sharepoint-download")
        add(sharepoint_source_url(url), "sharepoint-source")
        add(with_query(url, download="1"), "download-param")

    add(google_export_url(url), "google-export")
    add(dropbox_direct_url(url), "dropbox-direct")
    add(url, "direct")
    return found
