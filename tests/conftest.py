"""Shared fixtures."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output readable
        pass


@pytest.fixture(scope="session")
def page_url() -> str:
    """Serve the mock platform over HTTP.

    A file:// origin cannot call history.replaceState, and real platforms are
    served over HTTP anyway - so the page under test behaves like the real thing.
    """
    handler = partial(_QuietHandler, directory=str(FIXTURES / "pages"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mock-sequence.html"
    finally:
        server.shutdown()
        server.server_close()
