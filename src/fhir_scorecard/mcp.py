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
        "description": ("List graded FHIR endpoints, optionally filtered by kind "
                        "(payer, payer_provider_directory, provider, ehr, reference) "
                        "or by grade."),
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
        "description": ("Full scorecard for one endpoint: every dimension, every finding with "
                        "its spec citation, availability, and drift history."),
        "inputSchema": {
            "type": "object",
            "properties": {"endpoint_id": {"type": "string"}},
            "required": ["endpoint_id"],
        },
    },
    {
        "name": "grading_method",
        "description": ("How grades are computed, what each finding code checks, and the "
                        "documented limits of the dataset. Read this before characterizing "
                        "any grade."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_METHOD_NOTE = {
    "dimensions": {
        "reachability": "35% weight. Unreachable is an F regardless of anything else.",
        "transparency": "35% weight. What the CapabilityStatement declares about itself.",
        "interop": "30% weight. Declared profiles and authorization surface.",
    },
    "comparability": ("Grades are comparable within a kind only. A payer Patient Access API and "
                      "an EHR vendor sandbox answer to different implementation guides and are "
                      "never ranked against each other."),
    "not_applicable": ("Provider Directory APIs are required to be reachable without "
                       "authentication and are not scored on SMART discovery or OAuth."),
    "version_awareness": ("Each endpoint declares the FHIR release it intends to serve and is "
                          "checked against that, not against R4 unconditionally."),
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


def call_tool(site_dir: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
        return _text({
            "count": len(rows),
            "generated_at": index.get("generated_at"),
            "vantage": index.get("vantage"),
            "endpoints": rows,
            "note": _METHOD_NOTE["comparability"],
        })

    if name == "get_endpoint":
        endpoint_id = str(arguments.get("endpoint_id") or "").strip()
        # Path traversal guard: only a bare identifier ever becomes a filename.
        if not endpoint_id or "/" in endpoint_id or "\\" in endpoint_id or ".." in endpoint_id:
            return _text({"error": "endpoint_id must be a bare identifier"})
        path = site_dir / "api" / "endpoint" / f"{endpoint_id}.json"
        if not path.is_file():
            return _text({"error": f"unknown endpoint {endpoint_id!r}"})
        return _text(json.loads(path.read_text(encoding="utf-8")))

    return _text({"error": f"unknown tool {name!r}"})


def handle(site_dir: Path, request: dict[str, Any]) -> dict[str, Any] | None:
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
        result = call_tool(site_dir, str(params.get("name") or ""), arguments)
    elif request_id is None:
        return None  # a notification we do not act on
    else:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"unknown method {method!r}"}}

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(site_dir: Path, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            sink.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "parse error"}}) + "\n")
            sink.flush()
            continue
        try:
            response = handle(site_dir, request if isinstance(request, dict) else {})
        except Exception as exc:  # a bad request must not kill the server
            response = {"jsonrpc": "2.0", "id": (request or {}).get("id"),
                        "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        if response is not None:
            sink.write(json.dumps(response) + "\n")
            sink.flush()
    return 0
