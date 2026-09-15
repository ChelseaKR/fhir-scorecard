"""Tests for fetch.fetch_bytes and fetch.NoRedirects: the general-purpose (non-FHIR-discovery)
guarded fetch added for the compliance report bundle's logo fetch (bundle.py). Kept in fetch.py
rather than in bundle.py because tests/test_probe_contract.py requires this module to be the
only one in the package that opens a connection.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from fhir_scorecard import fetch


class _OKHandler(http.server.BaseHTTPRequestHandler):
    body = b"hello"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def _http_server() -> Iterator[tuple[str, type]]:
    """A real loopback HTTP server (not HTTPS -- fetch_bytes refuses non-https by URL scheme
    before ever opening a socket, so a plain HTTP server is enough to prove that refusal without
    standing up TLS)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/asset", server
    finally:
        server.shutdown()
        thread.join()


def test_fetch_bytes_refuses_a_non_https_url_before_any_connection(_http_server: tuple) -> None:
    url, _server = _http_server
    with pytest.raises(ValueError, match="non-https URL refused"):
        fetch.fetch_bytes(url, max_bytes=1024)
    # If a connection had been attempted, the handler would have served the body; it never ran.


def test_fetch_bytes_streams_a_response_under_the_cap(monkeypatch) -> None:
    import io

    class _FakeResponse(io.BytesIO):
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    class _FakeOpener:
        def open(self, request: object, timeout: float) -> _FakeResponse:
            return _FakeResponse(b"a-small-logo")

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *_: _FakeOpener())
    result = fetch.fetch_bytes("https://cdn.example.test/logo.png", max_bytes=1024)
    assert result == b"a-small-logo"


def test_fetch_bytes_refuses_a_response_over_the_cap(monkeypatch) -> None:
    import io

    class _FakeResponse(io.BytesIO):
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    class _FakeOpener:
        def open(self, request: object, timeout: float) -> _FakeResponse:
            return _FakeResponse(b"x" * 2000)

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *_: _FakeOpener())
    with pytest.raises(ValueError, match="exceeds the 1024 byte read limit"):
        fetch.fetch_bytes("https://cdn.example.test/logo.png", max_bytes=1024)


def test_no_redirects_refuses_every_redirect_even_to_a_discovery_path() -> None:
    """Negative control: DiscoveryRedirectHandler would allow a redirect that still lands on
    /metadata; NoRedirects must refuse it anyway; there is no such thing as a safe redirect for
    this fetcher."""
    handler = fetch.NoRedirects()
    with pytest.raises(fetch.RedirectRefused):
        handler.redirect_request(
            req=fetch.urllib.request.Request("https://a.test/asset"),
            fp=None,  # type: ignore[arg-type]
            code=302,
            msg="Found",
            headers={},  # type: ignore[arg-type]
            newurl="https://a.test/metadata",
        )
