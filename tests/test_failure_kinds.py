"""Why an endpoint was not reached, kept apart from the fact that it was not (#117).

`describe_error` has always told a hostname that does not resolve apart from a certificate that
does not verify, a timeout, a refused connection and a refused redirect. All of it then collapsed
into one free-text sentence and one boolean, so a payer that gates `/metadata` behind
registration and a payer whose public record is broken published identically, and neither
population could be counted.

These tests hold the classification and, just as much, hold what it is *not*. It is data about a
condition. It makes no claim about whose choice the condition was, it moves no grade, and it
never files an unrecognised failure under the nearest label.

The loopback server is a real HTTP server reached through a real `urllib` opener chain, so the
status cases go through `fetch_json`'s own exception handling rather than a mock of it. The
causes urllib surfaces as `URLError` are driven straight at the ladder, because a test that
depends on a DNS lookup failing is a test that passes or fails on somebody's resolver.
"""

from __future__ import annotations

import http.client
import http.server
import json
import socket
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fhir_scorecard.capability import NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED
from fhir_scorecard.fetch import (
    FAILURE_KINDS,
    UNCLASSIFIED,
    FetchResult,
    classify_error,
    describe_error,
    failure_kind_for_status,
    fetch_json,
    normalise_failure_kind,
)
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.vantage import VantageProbe, load_probe_files, reconcile, write_probes

REPO = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------------
# A loopback server that answers with whatever status the test asks for
# ----------------------------------------------------------------------------------


class _Answers(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200
    headers_to_send: ClassVar[dict[str, str]] = {}

    # `do_GET` is the spelling BaseHTTPRequestHandler dispatches on; not ours to rename.
    def do_GET(self) -> None:
        body = json.dumps({"resourceType": "CapabilityStatement"}).encode()
        self.send_response(type(self).status)
        for name, value in type(self).headers_to_send.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _LoopbackHTTPSHandler(urllib.request.HTTPSHandler):
    """Answer an ``https://`` request from a plain loopback server.

    `fetch_json` refuses a non-https URL before it opens anything, which is a promise this
    project keeps and not one to work around by weakening. So the URL stays ``https://`` and this
    handler carries it to a local socket, which means the request goes through the real opener
    chain: `HTTPErrorProcessor` raises the real `HTTPError`, and `fetch_json` classifies a real
    exception rather than one a test constructed for it.

    Subclassing `HTTPSHandler` rather than `AbstractHTTPHandler` is load-bearing:
    `build_opener` only *replaces* a default handler when the one it is given is a subclass of
    it. A sibling class registers a second `https_open`, the stock TLS one wins, and every test
    below quietly measures a failed handshake to a plain HTTP server instead of the status it
    asked for -- which is exactly what happened on the first run of this file.
    """

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(http.client.HTTPConnection, req)


@pytest.fixture()
def answering_server() -> Iterator[str]:
    _Answers.status = 200
    _Answers.headers_to_send = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Answers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(base: str) -> FetchResult:
    opener = urllib.request.build_opener(_LoopbackHTTPSHandler())
    return fetch_json(f"{base}/metadata", opener=opener)


# ----------------------------------------------------------------------------------
# The statuses, end to end
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected", "headers"),
    [
        (401, "authentication_required", {"WWW-Authenticate": 'Bearer realm="fhir"'}),
        (403, "forbidden", {}),
        (404, "not_found", {}),
        (500, "server_error", {}),
        (503, "server_error", {}),
    ],
)
def test_each_named_status_produces_its_own_kind(
    answering_server: str, status: int, expected: str, headers: dict[str, str]
) -> None:
    _Answers.status = status
    _Answers.headers_to_send = headers
    result = _fetch(answering_server)
    assert not result.ok
    assert result.status == status
    assert result.failure_kind == expected
    # The sentence is unchanged by any of this. It is the evidence a reader is shown, and the
    # kind is a second reading of the same fact, never a replacement for it.
    assert result.error == f"HTTP {status}"


@pytest.mark.parametrize("status", [400, 415, 429, 451])
def test_a_status_the_vocabulary_does_not_name_is_published_as_unclassified(
    answering_server: str, status: int
) -> None:
    """Not `forbidden`, which is a specific claim, and not `server_error`, which is another.

    A 429 is a rate limit and a 415 is a content-type refusal. Filing either under the
    nearest-looking label is the defect this classification exists to remove: it would put an
    endpoint into a population it is not in, and the population is the whole point.
    """
    _Answers.status = status
    result = _fetch(answering_server)
    assert result.failure_kind == UNCLASSIFIED
    # Unclassified is not information-free: the status this run actually received survives
    # beside it, so a reader can see what the classifier declined to name.
    assert result.status == status


def test_a_document_that_is_retrieved_carries_no_kind_at_all(answering_server: str) -> None:
    """`None`, not `unclassified`. There is no failure here to be unable to classify."""
    result = _fetch(answering_server)
    assert result.ok
    assert result.failure_kind is None


def test_a_refused_connection_is_classified_against_a_real_closed_port() -> None:
    """One live end-to-end case for the URLError family, with no resolver involved.

    A port nothing is listening on is a refusal the kernel produces locally, so this exercises
    the same `except (URLError, ...)` clause a dead payer endpoint would, deterministically.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    opener = urllib.request.build_opener(_LoopbackHTTPSHandler())
    result = fetch_json(f"https://127.0.0.1:{port}/metadata", opener=opener, timeout=2.0)
    assert not result.ok
    assert result.failure_kind in {"connection_refused", "timeout"}, result.error


# ----------------------------------------------------------------------------------
# The causes urllib reports as URLError
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "dns"),
        (socket.gaierror(-2, "Name or service not known"), "dns"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (TimeoutError("timed out"), "timeout"),
        (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "connection_refused"),
        (urllib.error.URLError(ssl.SSLError("handshake failure")), "tls"),
        (ssl.SSLCertVerificationError("self signed certificate"), "tls"),
        (urllib.error.URLError(OSError(65, "No route to host")), UNCLASSIFIED),
        (RuntimeError("something nobody anticipated"), UNCLASSIFIED),
    ],
)
def test_the_ladder_names_each_cause(exc: BaseException, expected: str) -> None:
    kind, sentence = classify_error(exc)
    assert kind == expected
    assert sentence, "every branch has to yield a sentence as well as a kind"
    assert kind in FAILURE_KINDS


def test_the_sentence_is_exactly_what_describe_error_still_returns() -> None:
    """The classification must not change one published word.

    `describe_error`'s output is quoted verbatim into R1's message, into the vantage consensus
    detail and into the rejection log. One ladder returning both halves is what makes a
    disagreement between them impossible rather than merely unlikely.
    """
    for exc in (
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
        urllib.error.URLError(TimeoutError("timed out")),
        urllib.error.URLError(ConnectionRefusedError(61, "refused")),
        ssl.SSLCertVerificationError("self signed certificate"),
        RuntimeError("unanticipated"),
    ):
        assert describe_error(exc) == classify_error(exc)[1]


def test_every_kind_the_status_mapper_can_return_is_in_the_vocabulary() -> None:
    """A closed vocabulary that something can produce a member outside of is not closed."""
    for status in range(100, 600):
        assert failure_kind_for_status(status) in FAILURE_KINDS


# ----------------------------------------------------------------------------------
# Reading a kind that came from somewhere else
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [None, "", "gated", "GATED", 403, True, ["dns"], {"kind": "dns"}, "dns "]
)
def test_a_kind_from_outside_that_is_not_in_the_vocabulary_reads_as_unclassified(
    value: object,
) -> None:
    assert normalise_failure_kind(value) == UNCLASSIFIED


@pytest.mark.parametrize("value", list(FAILURE_KINDS))
def test_every_published_kind_survives_the_round_trip(value: str) -> None:
    assert normalise_failure_kind(value) == value


def test_a_probe_file_from_a_foreign_vantage_cannot_invent_a_kind(tmp_path: Path) -> None:
    """#100's input path: a probe file this project did not write.

    `unclassified` here is the honest third state, not a coercion. It is strictly weaker than
    any label, it can never raise a grade, and it says what is true -- that this run has no
    classification for that failure -- rather than picking the nearest one.
    """
    (tmp_path / "foreign.json").write_text(
        json.dumps(
            {
                "vantage": "somebody-else/host",
                "probes": {
                    "made-up-label": {
                        "vantage": "somebody-else/host",
                        "reachable": False,
                        "elapsed_ms": 120,
                        "error": "blocked",
                        "failure_kind": "definitely_gated",
                    },
                    "no-label-at-all": {
                        "vantage": "somebody-else/host",
                        "reachable": False,
                        "elapsed_ms": 120,
                        "error": "blocked",
                    },
                    "reached-but-claims-a-kind": {
                        "vantage": "somebody-else/host",
                        "reachable": True,
                        "elapsed_ms": 120,
                        "failure_kind": "forbidden",
                    },
                    "a-real-one": {
                        "vantage": "somebody-else/host",
                        "reachable": False,
                        "elapsed_ms": 120,
                        "failure_kind": "authentication_required",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_probe_files([tmp_path / "foreign.json"])
    assert loaded["made-up-label"][0].failure_kind == UNCLASSIFIED
    assert loaded["no-label-at-all"][0].failure_kind == UNCLASSIFIED
    # A probe that says it reached the endpoint has no failure, whatever else the file claims.
    assert loaded["reached-but-claims-a-kind"][0].failure_kind is None
    assert loaded["a-real-one"][0].failure_kind == "authentication_required"


def test_a_kind_survives_being_written_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    write_probes(
        path,
        "here/host",
        {
            "gated": VantageProbe(
                vantage="here/host",
                reachable=False,
                elapsed_ms=90,
                error="HTTP 401",
                status=401,
                failure_kind="authentication_required",
            )
        },
    )
    assert load_probe_files([path])["gated"][0].failure_kind == "authentication_required"


# ----------------------------------------------------------------------------------
# Reconciliation: publish the disagreement, never resolve it
# ----------------------------------------------------------------------------------


def _failed(vantage: str, kind: str, error: str, status: int | None = None) -> VantageProbe:
    return VantageProbe(
        vantage=vantage,
        reachable=False,
        elapsed_ms=0,
        error=error,
        status=status,
        failure_kind=kind,
    )


def test_three_vantages_reporting_three_kinds_publish_all_three() -> None:
    consensus = reconcile(
        [
            _failed("a/one", "authentication_required", "HTTP 401", 401),
            _failed("b/two", "dns", "DNS did not resolve"),
            _failed("c/three", "tls", "TLS error: SSLError"),
        ]
    )
    assert not consensus.reachable
    assert consensus.failure_kinds == ("authentication_required", "dns", "tls")
    # Said in the sentence as well, because that is what a reader of one endpoint page sees.
    assert "did not agree on why" in consensus.detail
    for kind in consensus.failure_kinds:
        assert kind in consensus.detail


def test_vantages_that_agree_report_one_kind_and_claim_no_disagreement() -> None:
    consensus = reconcile(
        [
            _failed("a/one", "forbidden", "HTTP 403", 403),
            _failed("b/two", "forbidden", "HTTP 403", 403),
            _failed("c/three", "forbidden", "HTTP 403", 403),
        ]
    )
    assert consensus.failure_kinds == ("forbidden",)
    assert "did not agree" not in consensus.detail


def test_one_vantage_reaching_leaves_no_failure_population_at_all() -> None:
    """The asymmetry, one level down. A reached endpoint is in no failure population.

    Attaching a blocked vantage's 403 to an endpoint another vantage retrieved a document from
    is the 2026-08-05 misdiagnosis with a new field to express itself through: a working
    endpoint filed under a condition it is not in.
    """
    consensus = reconcile(
        [
            _failed("a/one", "forbidden", "HTTP 403", 403),
            VantageProbe(vantage="b/two", reachable=True, elapsed_ms=140, capability="{}"),
        ]
    )
    assert consensus.reachable
    assert consensus.failure_kinds == ()


def test_no_vantage_reporting_is_not_a_population_either() -> None:
    """An endpoint nobody probed is in no condition. Empty, never `('unclassified',)`."""
    assert reconcile([]).failure_kinds == ()


def test_one_vantage_whose_samples_disagree_establishes_no_condition() -> None:
    """Two samples of one label giving two answers have not established one of them.

    `unclassified` here is the absence of a classification and not the nearest one; the joined
    error sentence still names both conditions the samples reported.
    """
    consensus = reconcile(
        [
            _failed("a/one", "forbidden", "HTTP 403", 403),
            _failed("a/one", "dns", "DNS did not resolve"),
        ]
    )
    assert consensus.vantages == 1
    assert consensus.failure_kinds == (UNCLASSIFIED,)
    assert "DNS did not resolve" in consensus.detail
    assert "HTTP 403" in consensus.detail


# ----------------------------------------------------------------------------------
# No grade moves
# ----------------------------------------------------------------------------------

#: Every fixture endpoint's published numbers, read off `main` at 6235d71 -- the commit before
#: any of this existed -- and pinned here as literals. The classification is data beside the
#: grade and must never become an input to it, and the only way to hold that is to state what
#: the grades were and fail when they are not that.
#:
#: The two unreachable rows were added later (#137). Before them this pinned three endpoints,
#: all of which answered, so the one test in the suite that reads a *published card* could not
#: see the unreachable path at all -- and #135 shipped a `reachability_score` of **0** on 14 live
#: endpoints underneath it. On the code that was live on 2026-09-12 these two rows would read
#: ``("not observed", 0, "", "")``; the empty string is what a dimension with no score publishes,
#: and the point of pinning them is that no later change can put a number back without saying so
#: here.
PINNED_GRADES = {
    "aspirus-patient-access": ("not observed", "", "", ""),
    "bcbs-arizona-patient-access": ("not observed", "", "", ""),
    "cms-blue-button-2": ("B", 100, 100, 60),
    "inferno-reference": ("A", 100, 80, 100),
    "oracle-health-open": ("C", 100, 80, 40),
}


def test_no_published_grade_moved(tmp_path: Path) -> None:
    from fhir_scorecard.cli import main as cli_main

    out = tmp_path / "site"
    assert (
        cli_main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(REPO / "tests" / "fixtures"),
                "--registry",
                str(REPO / "tests" / "fixtures" / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    seen = {}
    for path in sorted((out / "api" / "endpoint").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))["endpoint"]
        seen[record["endpoint_id"]] = (
            record["grade"],
            record["reachability_score"],
            record["transparency_score"],
            record["interop_score"],
        )
    assert seen == PINNED_GRADES


def _published_cards(tmp_path: Path) -> list[dict]:
    """Build the offline site once and hand back every published per-endpoint card."""
    from fhir_scorecard.cli import main as cli_main

    out = tmp_path / "site"
    assert (
        cli_main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(REPO / "tests" / "fixtures"),
                "--registry",
                str(REPO / "tests" / "fixtures" / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((out / "api" / "endpoint").glob("*.json"))
    ]


def test_the_offline_fixtures_cover_both_populations(tmp_path: Path) -> None:
    """The gate's own coverage, asserted rather than left for a reader to discover.

    This test used to be called ``test_the_offline_fixtures_all_reach_so_they_prove_nothing_
    about_a_failure`` and it was right: every fixture endpoint answered, so the published-card
    pin above exercised **3 of 81** registry endpoints and **0 of the 14** that were failing.
    Under it, `grade_reachability` published a 0 for an unmeasured dimension for as long as
    anyone cared to look (#135).

    Both populations now have to be present. A future fixture refresh that quietly drops the
    unreachable captures -- or one where those endpoints start answering and the capture is
    updated without thought -- fails here rather than silently restoring the blind spot.
    """
    cards = _published_cards(tmp_path)
    reached = [c["endpoint"] for c in cards if c["endpoint"]["reachable"] == "true"]
    refused = [c["endpoint"] for c in cards if c["endpoint"]["reachable"] != "true"]
    assert len(reached) >= 3, "the fixture set must still grade endpoints that answered"
    assert len(refused) >= 2, (
        "the fixture set must carry at least two endpoints no vantage reached, or no test in "
        "this suite reads a published card for the unreachable path"
    )
    # Both branches of the classification, not two of the same: one condition that produced an
    # HTTP status and one that never completed a connection.
    kinds = {k for e in refused for k in e["failure_kinds"]}
    assert len(kinds) >= 2, f"the refused fixtures name only {kinds}"
    assert any(c["endpoint"]["failure_kinds"] == ["tls"] for c in cards)
    assert any(c["endpoint"]["failure_kinds"] == ["forbidden"] for c in cards)


def test_a_published_card_never_scores_what_it_did_not_measure(tmp_path: Path) -> None:
    """The invariants #135 and #136 were each one half of, held on the serialized artifact.

    Every assertion the suite had about the unreachable path was on an in-process
    ``DimensionScore`` or a rendered HTML fragment. Nothing read ``api/endpoint/<id>.json``,
    which is the file ``dataset.csv``, ``api/index.json`` and every downstream consumer are
    built from, and which was the one surface publishing a check that was never made as
    ``"ok": false`` with no way to tell.
    """
    for card in _published_cards(tmp_path):
        record = card["endpoint"]
        eid = record["endpoint_id"]
        unreached = record["reachable"] != "true"

        # `failure_kinds` is non-empty exactly when the endpoint was not reached. An empty tuple
        # is not a population, and a kind beside a reachable endpoint files a working service
        # under a failure.
        assert bool(record["failure_kinds"]) is unreached, eid

        for dimension in card["dimensions"]:
            for finding in dimension["findings"]:
                # #136: both fields travel with the verdict they qualify, on every surface.
                assert "observed" in finding, (eid, finding["code"])
                assert "withheld_points" in finding, (eid, finding["code"])
                if not finding["observed"]:
                    # A check nobody made may not read as a passed one either. `ok` is only
                    # meaningful beside `observed`, which is the whole reason it is published.
                    assert finding["ok"] is False, (eid, finding["code"])
                    assert finding["points"] == 0, (eid, finding["code"])

            if unreached:
                # #135: no dimension of an endpoint nobody reached carries a score. This is the
                # assertion that fails on the code that was live on 2026-09-12, where
                # reachability published 0 while the other two published null.
                assert dimension["score"] is None, (eid, dimension["key"])
                assert all(not f["observed"] for f in dimension["findings"]), (
                    eid,
                    dimension["key"],
                )

        if unreached:
            for column in ("reachability_score", "transparency_score", "interop_score"):
                assert record[column] == "", (eid, column)
            assert record["grade"] == "not observed", eid


def test_an_unreached_endpoint_with_no_consensus_still_names_its_condition() -> None:
    """A single-vantage run has no consensus object, and the kind must survive that path."""
    card = build_scorecard(
        "gated-example",
        "Gated Example",
        FetchResult(
            url="https://example.invalid/metadata",
            ok=False,
            status=401,
            elapsed_ms=40,
            body=b"",
            error="HTTP 401",
            failure_kind="authentication_required",
        ),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
    )
    assert card.failure_kinds == ("authentication_required",)


def test_an_unreached_endpoint_whose_failure_was_never_classified_says_so() -> None:
    card = build_scorecard(
        "mystery",
        "Mystery",
        FetchResult(
            url="https://example.invalid/metadata",
            ok=False,
            status=None,
            elapsed_ms=0,
            body=b"",
            error="something",
            failure_kind=None,
        ),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
    )
    assert card.failure_kinds == (UNCLASSIFIED,)
