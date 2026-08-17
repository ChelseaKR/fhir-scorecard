"""The probe contract, as tests that can fail rather than sentences that can be believed.

README.md, SECURITY.md, `docs/ci-action.md` and the site all promise the same three things: this
project never authenticates, never requests patient data, and never probes beyond `/metadata`
and `/.well-known/smart-configuration`. `docs/RESPONSIBLE-TECH-AUDITS.md` used to say the
guarantee was "enforced by having no code path that does", and that was the whole problem. There
was such a code path, it was three words long, and it was in the standard library.

`urllib.request.build_opener()` returns a chain containing `HTTPRedirectHandler`, which follows a
`Location` header wherever it points. A graded server answering `/metadata` with
`302 Location: /Patient?_count=50` would have had that request issued, its body read, parsed as a
CapabilityStatement, fingerprinted into `data/history.json` and uploaded as a probe artifact. The
same handler follows an `https` to `http` downgrade and copies every request header onto the
plaintext hop, so "HTTPS is enforced before any connection is attempted (fail closed)" was true
of the first request only.

Two of the tests below use a loopback HTTP server. That is deliberate and it is the point: an
assertion about which requests were *not* made is worth very little unless something was
listening to find out. Nothing here reaches a real FHIR endpoint, and nothing here leaves the
machine.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fhir_scorecard import cli
from fhir_scorecard.fetch import (
    DISCOVERY_PATHS,
    MAX_REDIRECTS,
    USER_AGENT,
    DiscoveryRedirectHandler,
    FetchResult,
    RedirectRefused,
    build_default_opener,
    fetch_json,
    is_discovery_url,
)
from fhir_scorecard.grading import NOT_OBSERVED

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "fhir_scorecard"

#: Anything that would turn an observation of a public document into an authenticated request.
#: None of these is ever set; the test exists so that the day one is, it is not a silent day.
CREDENTIAL_HEADERS = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "apikey",
    "x-auth-token",
)

#: A resource type that returns patient data. A redirect naming it is the case the contract is
#: written about, so it is the case the tests drive.
PATIENT_PATH = "/Patient?_count=50"


class _Probed(http.server.BaseHTTPRequestHandler):
    """Records every path and header set it is asked for, and redirects `/metadata` onward."""

    requests: ClassVar[list[tuple[str, dict[str, str]]]] = []
    redirect_to: ClassVar[str] = PATIENT_PATH

    # `do_GET` is the spelling BaseHTTPRequestHandler dispatches on; it is not ours to rename.
    def do_GET(self) -> None:
        type(self).requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if self.path == "/metadata":
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.end_headers()
            return
        body = json.dumps({"resourceType": "Bundle", "entry": ["synthetic patient row"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture()
def redirecting_server() -> Iterator[tuple[str, list[tuple[str, dict[str, str]]]]]:
    """A loopback server whose `/metadata` points somewhere this project must not follow."""
    _Probed.requests = []
    _Probed.redirect_to = PATIENT_PATH
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Probed)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Probed.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310


def _redirect(handler: urllib.request.HTTPRedirectHandler, frm: str, to: str) -> str | None:
    """Ask `handler` whether a 302 from `frm` to `to` should be followed, and where to."""
    headers = Message()
    headers["Location"] = to
    new = handler.redirect_request(_request(frm), BytesIO(b""), 302, "Found", headers, to)  # type: ignore[arg-type]
    return None if new is None else new.full_url


class TestARedirectCannotWalkOffTheDiscoverySurface:
    """The headline. A real server, a real socket, and the same opener `fetch_json` builds."""

    def test_the_stock_opener_does_request_patient_data_when_told_to(
        self, redirecting_server: tuple[str, list[tuple[str, dict[str, str]]]]
    ) -> None:
        """What this project shipped before the guard existed, demonstrated rather than asserted.

        If a future change reverts to `urllib.request.build_opener()`, the guarded test below
        starts failing; this one is here so the reason is not in doubt.
        """
        base, seen = redirecting_server
        with urllib.request.build_opener().open(_request(f"{base}/metadata"), timeout=10) as page:
            body = page.read(4096)

        assert [path for path, _ in seen] == ["/metadata", PATIENT_PATH]
        assert b"synthetic patient row" in body

    def test_the_shipped_opener_refuses_and_never_makes_the_second_request(
        self, redirecting_server: tuple[str, list[tuple[str, dict[str, str]]]]
    ) -> None:
        base, seen = redirecting_server
        with pytest.raises(urllib.error.HTTPError) as raised:
            build_default_opener().open(_request(f"{base}/metadata"), timeout=10)

        assert isinstance(raised.value, RedirectRefused)
        # The load-bearing assertion: the server was never asked for the patient resource.
        assert [path for path, _ in seen] == ["/metadata"]

    def test_no_request_it_does_make_carries_a_credential(
        self, redirecting_server: tuple[str, list[tuple[str, dict[str, str]]]]
    ) -> None:
        """Asserted against what a server actually received, not against what the code intends."""
        base, seen = redirecting_server
        with build_default_opener().open(
            _request(f"{base}{DISCOVERY_PATHS[1]}"), timeout=10
        ) as page:
            page.read(64)

        assert seen, "the server recorded no request; this scan would pass over nothing"
        for path, headers in seen:
            for name in CREDENTIAL_HEADERS:
                assert name not in headers, f"{path} was sent a {name} header"
        assert "fhir-scorecard" in seen[0][1]["user-agent"]


class TestTheRedirectRulesThemselves:
    """Scheme and path, over the real `urllib` handler contract rather than a re-implementation."""

    def test_an_https_to_http_downgrade_is_refused(self) -> None:
        with pytest.raises(RedirectRefused, match="HTTPS only, on every hop"):
            _redirect(
                DiscoveryRedirectHandler(),
                "https://payer.example/fhir/metadata",
                "http://payer.example/fhir/metadata",
            )

    def test_the_stock_handler_would_have_followed_that_downgrade(self) -> None:
        """The behaviour being replaced, pinned so the delta is documented and not folklore."""
        assert (
            _redirect(
                urllib.request.HTTPRedirectHandler(),
                "https://payer.example/fhir/metadata",
                "http://payer.example/fhir/metadata",
            )
            == "http://payer.example/fhir/metadata"
        )

    @pytest.mark.parametrize(
        "target",
        [
            "https://payer.example/fhir/Patient?_count=50",
            "https://payer.example/fhir/Patient/123",
            "https://auth.payer.example/oauth2/authorize?client_id=x",
            "https://payer.example/",
            "https://payer.example",
            "https://payer.example/fhir/metadata.zip",
            "ftp://payer.example/fhir/metadata",
        ],
    )
    def test_a_target_outside_the_two_discovery_paths_is_refused(self, target: str) -> None:
        with pytest.raises(RedirectRefused):
            _redirect(DiscoveryRedirectHandler(), "https://payer.example/fhir/metadata", target)

    @pytest.mark.parametrize(
        "target",
        [
            "https://payer.example/fhir/R4/metadata",
            "https://cdn.payer.example/fhir/metadata",
            "https://payer.example/fhir/metadata/",
            "https://payer.example/fhir/metadata?_format=json",
            "https://payer.example/.well-known/smart-configuration",
        ],
    )
    def test_a_redirect_that_still_points_at_a_discovery_document_is_followed(
        self, target: str
    ) -> None:
        """Refusing these would break ordinary, honest servers, which is its own kind of wrong."""
        assert _redirect(DiscoveryRedirectHandler(), "https://payer.example/metadata", target) == (
            target
        )

    def test_a_path_that_merely_ends_in_the_word_is_not_a_discovery_path(self) -> None:
        assert not is_discovery_url("https://payer.example/notmetadata")
        assert not is_discovery_url("https://payer.example/fhir/smart-configuration")
        assert is_discovery_url("https://payer.example/fhir/metadata")

    def test_the_right_path_on_the_wrong_scheme_is_still_not_a_discovery_url(self) -> None:
        """Scheme is checked first, so a plaintext `/metadata` never counts as in-scope."""
        assert not is_discovery_url("http://payer.example/fhir/metadata")
        assert not is_discovery_url("ftp://payer.example/.well-known/smart-configuration")

    def test_the_redirect_budget_is_bounded_well_below_urllibs_default(self) -> None:
        """A server can otherwise turn one probe into eleven requests to itself."""
        assert DiscoveryRedirectHandler.max_redirections == MAX_REDIRECTS
        assert urllib.request.HTTPRedirectHandler.max_redirections > MAX_REDIRECTS


class TestTheOpenerHasNothingThatCouldAuthenticate:
    def test_the_only_redirect_handler_is_the_guarded_one(self) -> None:
        handlers = build_default_opener().handlers
        redirecting = [h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        assert len(redirecting) == 1
        assert isinstance(redirecting[0], DiscoveryRedirectHandler)

    def test_no_credential_carrying_handler_is_installed(self) -> None:
        forbidden = (
            urllib.request.HTTPCookieProcessor,
            urllib.request.HTTPBasicAuthHandler,
            urllib.request.HTTPDigestAuthHandler,
            urllib.request.ProxyBasicAuthHandler,
            urllib.request.ProxyDigestAuthHandler,
        )
        for handler in build_default_opener().handlers:
            assert not isinstance(handler, forbidden), type(handler).__name__

    def test_fetch_json_uses_that_opener_and_not_a_stock_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise every assertion above would be about an object nothing calls."""
        built: list[urllib.request.OpenerDirector] = []
        real = build_default_opener

        def spy() -> urllib.request.OpenerDirector:
            opener = real()
            built.append(opener)
            return opener

        monkeypatch.setattr("fhir_scorecard.fetch.build_default_opener", spy)
        monkeypatch.setattr(
            urllib.request.OpenerDirector,
            "open",
            lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()),
        )
        fetch_json("https://payer.example/fhir/metadata")
        assert len(built) == 1


class TestWhatTheRunActuallyAsksFor:
    def test_the_only_headers_sent_are_an_accept_and_an_identifying_user_agent(self) -> None:
        captured: list[urllib.request.Request] = []

        class _Recorder(urllib.request.OpenerDirector):
            def open(self, fullurl: Any, data: Any = None, timeout: Any = None) -> Any:
                captured.append(fullurl)
                raise TimeoutError

            def close(self) -> None:
                return

        fetch_json("https://payer.example/fhir/metadata", opener=_Recorder())
        (request,) = captured
        assert {name.lower() for name, _ in request.header_items()} == {"accept", "user-agent"}
        assert "contact:" in request.get_header("User-agent")

    def test_a_single_endpoint_check_requests_the_two_discovery_documents_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []

        def fake(url: str, **_: Any) -> FetchResult:
            asked.append(url)
            return FetchResult(url=url, ok=False, status=None, elapsed_ms=0, body=b"", error="x")

        monkeypatch.setattr("fhir_scorecard.cli.fetch_json", fake)
        cli.main(["check", "https://payer.example/fhir"])

        assert asked == [
            "https://payer.example/fhir/metadata",
            "https://payer.example/fhir/.well-known/smart-configuration",
        ]
        assert all(is_discovery_url(url) for url in asked)

    def test_every_network_call_in_the_package_goes_through_the_guarded_fetcher(self) -> None:
        """A source scan, and named as one: it cannot prove behaviour, only that no other module
        opens its own connection and so bypasses everything the tests above establish."""
        modules = sorted(p for p in SRC.glob("*.py") if p.name != "fetch.py")
        assert len(modules) >= 10, "the scan found almost no modules; it would pass over nothing"
        opens = re.compile(
            r"\b(urlopen|build_opener|HTTPSConnection|HTTPConnection|create_connection)\b"
        )
        for path in modules:
            text = path.read_text(encoding="utf-8")
            assert not opens.search(text), (
                f"{path.name} opens its own connection; the probe contract is enforced in "
                "fetch.py and a second door around it is not covered by any of it"
            )


class TestARefusalIsNotAFindingAgainstTheEndpoint:
    def test_it_is_reported_as_a_retrieval_error_with_its_reason(self) -> None:
        headers = Message()

        class _Refusing(urllib.request.OpenerDirector):
            def open(self, fullurl: Any, data: Any = None, timeout: Any = None) -> Any:
                raise RedirectRefused(
                    "https://payer.example/Patient",
                    302,
                    "redirect off the discovery surface refused",
                    headers,
                    BytesIO(b""),
                )

            def close(self) -> None:
                return

        result = fetch_json("https://payer.example/fhir/metadata", opener=_Refusing())
        assert not result.ok
        assert result.status is None
        assert result.error == "redirect off the discovery surface refused"

    def test_an_ordinary_http_error_still_reports_its_status_and_not_a_refusal(self) -> None:
        """`RedirectRefused` is an `HTTPError` subclass, so the two branches have to stay apart:
        a 404 must keep reading as a 404 rather than as something this project declined to do."""
        headers = Message()

        class _NotFound(urllib.request.OpenerDirector):
            def open(self, fullurl: Any, data: Any = None, timeout: Any = None) -> Any:
                raise urllib.error.HTTPError(
                    "https://payer.example/fhir/metadata", 404, "Not Found", headers, None
                )

            def close(self) -> None:
                return

        result = fetch_json("https://payer.example/fhir/metadata", opener=_NotFound())
        assert not result.ok
        assert result.status == 404
        assert result.error == "HTTP 404"

    def test_the_endpoint_is_published_as_not_observed_rather_than_graded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Nothing was retrieved, so nothing may be said about what the endpoint declares. An
        endpoint whose redirect we declined must not read the same as one that answered badly."""

        def refusing(url: str, **_: Any) -> FetchResult:
            return FetchResult(
                url=url,
                ok=False,
                status=None,
                elapsed_ms=4,
                body=b"",
                error="redirect off the discovery surface refused (https://payer.example/Patient)",
            )

        monkeypatch.setattr("fhir_scorecard.cli.fetch_json", refusing)
        out = tmp_path / "result.json"
        assert cli.main(["check", "https://payer.example/fhir", "--json-out", str(out)]) == 0
        card = json.loads(out.read_text())["scorecards"][0]
        assert card["grade"] == NOT_OBSERVED
        assert [d["score"] for d in card["dimensions"] if d["key"] != "reachability"] == [
            None,
            None,
        ]


def test_the_published_promise_names_the_paths_the_code_enforces() -> None:
    """The docs and `DISCOVERY_PATHS` have to be the same two paths, or one of them is wrong."""
    for name in ("README.md", "SECURITY.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for path in DISCOVERY_PATHS:
            assert path.lstrip("/") in text, f"{name} does not name {path}"
