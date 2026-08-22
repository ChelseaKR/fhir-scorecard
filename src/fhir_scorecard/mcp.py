"""Read-only MCP server over the published dataset.

Exposes the scorecard to an assistant without giving it network reach: the server reads the
committed dataset files and answers questions about them. There is no tool here that probes an
endpoint, because a model deciding to fetch arbitrary URLs is a different and much larger
security surface than one reading a file this project already publishes.

Speaks JSON-RPC 2.0 over stdio, stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "io.github.chelseakr/fhir-scorecard", "version": "0.1.0"}

_TOOLS = [
    {
        "name": "list_endpoints",
        "description": (
            "List graded FHIR endpoints, optionally filtered by kind "
            "(payer, payer_provider_directory, provider, ehr, reference) "
            "or by grade."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Filter by endpoint category"},
                "grade": {"type": "string", "description": "Filter by letter grade A-F"},
            },
        },
    },
    {
        "name": "get_endpoint",
        "description": (
            "Full scorecard for one endpoint: every dimension, every finding with "
            "its spec citation, availability, and drift history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"endpoint_id": {"type": "string"}},
            "required": ["endpoint_id"],
        },
    },
    {
        "name": "cited_passages",
        "description": (
            "For one endpoint, each finding together with the passages of the FHIR, "
            "SMART App Launch, or US Core specification page it cites, quoted verbatim "
            "from the copies retained under corpus/. No model is called; use these "
            "passages to explain a finding in the specification's own words."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"endpoint_id": {"type": "string"}},
            "required": ["endpoint_id"],
        },
    },
    {
        "name": "grading_method",
        "description": (
            "How grades are computed, what each finding code checks, and the "
            "documented limits of the dataset. Read this before characterizing "
            "any grade."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_METHOD_NOTE = {
    "dimensions": {
        "reachability": "35% weight. Does /metadata answer, over HTTPS, in reasonable time.",
        "transparency": "35% weight. What the CapabilityStatement declares about itself.",
        "interop": "30% weight. Declared profiles and authorization surface.",
    },
    "not_observed": (
        "An endpoint whose documents no vantage retrieved on a run is published with grade "
        "'not observed' and empty dimension scores, not F. Nothing on such a record describes "
        "what the endpoint publishes, and it must not be characterized as a low grade, a "
        "failure, or an absence of declared capability. F means the endpoint answered and its "
        "documents scored below the D threshold."
    ),
    "comparability": (
        "Grades are comparable within a kind only. A payer Patient Access API and "
        "an EHR vendor sandbox answer to different implementation guides and are "
        "never ranked against each other."
    ),
    "not_applicable": (
        "Provider Directory APIs are required to be reachable without "
        "authentication and are not scored on SMART discovery or OAuth."
    ),
    "version_awareness": (
        "Each endpoint declares the FHIR release it intends to serve and is "
        "checked against that, not against R4 unconditionally."
    ),
    "limits": [
        "Observational snapshot of public surfaces; not an audit, not a compliance "
        "determination, not a statement about care quality.",
        "Latency is a median across the vantages that answered, and those vantages are three "
        "GitHub-hosted runner images on one provider's network rather than three independent "
        "networks; bands are deliberately coarse for that reason.",
        "A run in which no vantage reached an endpoint says the endpoint was not reached from "
        "that network on that day. It does not establish that the endpoint is down.",
        "Small sample. Do not generalize a handful of endpoints to an industry.",
        "Absence from this dataset means no public base URL was found, not that no API exists.",
    ],
}


def _load(site_dir: Path) -> dict[str, Any]:
    index_path = site_dir / "api" / "index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"no dataset at {index_path}; run 'fhir-scorecard grade' first")
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _text(payload: object) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def call_tool(
    site_dir: Path, name: str, arguments: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    if name == "grading_method":
        return _text(_METHOD_NOTE)

    if name == "list_endpoints":
        index = _load(site_dir)
        rows = [e for e in index.get("endpoints", []) if isinstance(e, dict)]
        kind = arguments.get("kind")
        grade = arguments.get("grade")
        if kind:
            rows = [e for e in rows if e.get("kind") == kind]
        if grade:
            rows = [e for e in rows if str(e.get("grade", "")).upper() == str(grade).upper()]
        return _text(
            {
                "count": len(rows),
                "generated_at": index.get("generated_at"),
                "vantage": index.get("vantage"),
                "endpoints": rows,
                "note": _METHOD_NOTE["comparability"],
            }
        )

    if name in {"get_endpoint", "cited_passages"}:
        endpoint_id = str(arguments.get("endpoint_id") or "").strip()
        # Path traversal guard: only a bare identifier ever becomes a filename.
        if not endpoint_id or "/" in endpoint_id or "\\" in endpoint_id or ".." in endpoint_id:
            return _text({"error": "endpoint_id must be a bare identifier"})
        path = site_dir / "api" / "endpoint" / f"{endpoint_id}.json"
        if not path.is_file():
            return _text({"error": f"unknown endpoint {endpoint_id!r}"})
        record = json.loads(path.read_text(encoding="utf-8"))
        if name == "get_endpoint":
            return _text(record)
        return _text(cited_passages(record, site_dir.parent if root is None else root))

    return _text({"error": f"unknown tool {name!r}"})


def cited_passages(record: dict[str, Any], root: Path) -> dict[str, Any]:
    """Each finding with the verbatim specification passages its citation points at.

    Deterministic: lexical retrieval over the retained pages under ``corpus/``;
    no model is involved. The passages are the specification's own words, so a
    client that explains a finding can quote rather than recall.
    """
    from fhir_scorecard.ai.corpus import CorpusError, CorpusIndex
    from fhir_scorecard.ai.narrate import _findings, grounding_passages

    try:
        corpus = CorpusIndex.load(root)
    except CorpusError as exc:
        return {"error": f"corpus unavailable: {exc}"}
    findings = _findings(record)
    rows = []
    for finding in findings:
        passages, unresolved = grounding_passages([finding], corpus)
        rows.append(
            {
                "code": finding.get("code"),
                "dimension": finding.get("dimension"),
                "ok": finding.get("ok"),
                "observed": finding.get("observed", True),
                "message": finding.get("message"),
                "citation": finding.get("citation"),
                "passages": [
                    {
                        "passage_id": p.passage_id,
                        "source": corpus.documents[p.source_id].label,
                        "heading": p.heading,
                        "text": p.text,
                    }
                    for p in passages
                ],
                "citation_not_retained": unresolved,
            }
        )
    # The per-endpoint API file nests identity under "endpoint"; scorecards.json does not.
    nested = record.get("endpoint")
    endpoint: dict[str, Any] = nested if isinstance(nested, dict) else record
    return {
        "endpoint_id": endpoint.get("endpoint_id"),
        "grade": endpoint.get("grade"),
        "findings": rows,
        "note": (
            "Passages are quoted verbatim from retained copies of the cited pages "
            "(corpus/SOURCES.json). They are retrieval matches, not a determination; "
            "the finding message is the graded fact."
        ),
    }


def handle(
    site_dir: Path, request: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": _TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        arguments = params.get("arguments") or {}
        result = call_tool(site_dir, str(params.get("name") or ""), arguments, root=root)
    elif request_id is None:
        return None  # a notification we do not act on
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method!r}"},
        }

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(
    site_dir: Path,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    *,
    root: Path | None = None,
) -> int:
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            sink.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    }
                )
                + "\n"
            )
            sink.flush()
            continue
        try:
            response = handle(site_dir, request if isinstance(request, dict) else {}, root=root)
        except Exception as exc:  # a bad request must not kill the server
            response = {
                "jsonrpc": "2.0",
                "id": (request or {}).get("id"),
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if response is not None:
            sink.write(json.dumps(response) + "\n")
            sink.flush()
    return 0
