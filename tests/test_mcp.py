from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from fhir_scorecard.mcp import PROTOCOL_VERSION, call_tool, handle, serve


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    api = tmp_path / "api" / "endpoint"
    api.mkdir(parents=True)
    (tmp_path / "api" / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-05",
                "vantage": "test",
                "count": 2,
                "endpoints": [
                    {"endpoint_id": "alpha", "name": "Alpha", "kind": "payer", "grade": "A"},
                    {"endpoint_id": "beta", "name": "Beta", "kind": "ehr", "grade": "C"},
                ],
            }
        )
    )
    (api / "alpha.json").write_text(json.dumps({"endpoint": {"grade": "A"}, "dimensions": []}))
    return tmp_path


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_initialize_reports_the_packaged_version(site: Path) -> None:
    """``serverInfo.version`` is the version this package actually is.

    It was a hand-typed ``"0.1.0"`` literal that nothing read. A client asking this
    server what it is talking to would have kept being told ``0.1.0`` after the package
    moved on, and no gate in the repository could have noticed -- the packaged version
    lives in ``pyproject.toml`` and this string was never compared to it.

    The comparison is against the packaging metadata rather than ``__version__``, so a
    future change that stops deriving one from the other is caught here too.
    """
    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as handle_:
        declared = str(tomllib.load(handle_)["project"]["version"])

    init = handle(site, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init is not None
    assert init["result"]["serverInfo"]["version"] == declared


def test_initialize_and_tools_list(site: Path) -> None:
    init = handle(site, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init is not None
    assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
    tools = handle(site, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools is not None
    names = {t["name"] for t in tools["result"]["tools"]}
    assert names == {"list_endpoints", "get_endpoint", "cited_passages", "grading_method"}


def test_list_filters_and_carries_the_comparability_caveat(site: Path) -> None:
    everything = _payload(call_tool(site, "list_endpoints", {}))
    assert everything["count"] == 2
    assert "comparable within a kind only" in everything["note"]

    payers = _payload(call_tool(site, "list_endpoints", {"kind": "payer"}))
    assert payers["count"] == 1
    assert payers["endpoints"][0]["endpoint_id"] == "alpha"

    by_grade = _payload(call_tool(site, "list_endpoints", {"grade": "c"}))
    assert by_grade["count"] == 1 and by_grade["endpoints"][0]["endpoint_id"] == "beta"


def test_get_endpoint_and_unknown(site: Path) -> None:
    assert (
        _payload(call_tool(site, "get_endpoint", {"endpoint_id": "alpha"}))["endpoint"]["grade"]
        == "A"
    )
    assert (
        "unknown endpoint"
        in _payload(call_tool(site, "get_endpoint", {"endpoint_id": "nope"}))["error"]
    )


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "a\\b", "", ".."])
def test_get_endpoint_refuses_path_traversal(site: Path, bad: str) -> None:
    """An identifier from a model must never become an arbitrary filesystem path."""
    assert (
        "bare identifier"
        in _payload(call_tool(site, "get_endpoint", {"endpoint_id": bad}))["error"]
    )


def test_grading_method_states_the_limits(site: Path) -> None:
    method = _payload(call_tool(site, "grading_method", {}))
    assert "not an audit" in " ".join(method["limits"])
    # An assistant reading this must not be able to call three runner images three networks,
    # nor read a failed run as a statement that the endpoint is down.
    assert any("one provider's network" in limit for limit in method["limits"])
    assert any(
        "does not establish that the endpoint is down" in limit for limit in method["limits"]
    )
    assert any("no public base URL was found" in limit for limit in method["limits"])


def test_unknown_tool_and_method(site: Path) -> None:
    assert "unknown tool" in _payload(call_tool(site, "nope", {}))["error"]
    bad = handle(site, {"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert bad is not None and bad["error"]["code"] == -32601


def test_notifications_get_no_response(site: Path) -> None:
    assert handle(site, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_missing_dataset_is_an_error_not_a_crash(tmp_path: Path) -> None:
    out = io.StringIO()
    serve(
        tmp_path,
        io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            '"params":{"name":"list_endpoints","arguments":{}}}\n'
        ),
        out,
    )
    assert "FileNotFoundError" in out.getvalue()


def test_serve_survives_malformed_input(site: Path) -> None:
    out = io.StringIO()
    serve(site, io.StringIO('not json\n\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["result"]["tools"]
