"""Command-line entry point: grade a registry of endpoints, online or from offline fixtures."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.drift import load_history, observe, save_history
from fhir_scorecard.fetch import FetchResult, fetch_json
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.registry import Endpoint, load_registry, version_prefix
from fhir_scorecard.report import render_html, to_json
from fhir_scorecard.reprobe import format_report, load_candidates, reprobe


def _offline_fetch(fixtures: Path, endpoint_id: str, filename: str, url: str) -> FetchResult:
    path = fixtures / endpoint_id / filename
    if not path.is_file():
        return FetchResult(url=url, ok=False, status=None, elapsed_ms=0, body=b"",
                           error="no fixture")
    return FetchResult(url=url, ok=True, status=200, elapsed_ms=1,
                       body=path.read_bytes(), error=None)


def _grade_endpoint(endpoint: Endpoint, *, offline: bool, fixtures: Path | None,
                    history: dict[str, Any], today: str, vantage: str) -> Scorecard:
    metadata_url = f"{endpoint.base_url}/metadata"
    smart_url = f"{endpoint.base_url}/.well-known/smart-configuration"
    if offline and fixtures is not None:
        metadata = _offline_fetch(fixtures, endpoint.endpoint_id, "metadata.json", metadata_url)
        smart = _offline_fetch(fixtures, endpoint.endpoint_id, "smart.json", smart_url)
    else:
        metadata = fetch_json(metadata_url)
        smart = fetch_json(smart_url)
    facts = parse_capability(metadata.body) if metadata.ok else parse_capability(b"")
    smart_facts = parse_smart(smart.body) if smart.ok else parse_smart(b"")
    drift = observe(history, endpoint.endpoint_id, facts, today)
    return build_scorecard(endpoint.endpoint_id, endpoint.name, metadata, facts, smart_facts,
                           kind=endpoint.kind, vantage=vantage,
                           version_prefix=version_prefix(endpoint.expects),
                           observed_since=drift.first_seen,
                           drift_events=drift.recorded_events)


def _recheck(candidates_path: Path) -> int:
    try:
        candidates = load_candidates(candidates_path)
    except (OSError, ValueError) as exc:
        print(f"candidates error: {exc}", file=sys.stderr)
        return 2
    results = [reprobe(c) for c in candidates]
    print(format_report(results))
    # Exit 0 either way: a candidate that starts answering is news, not a failure.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fhir-scorecard")
    sub = parser.add_subparsers(dest="command", required=True)
    grade = sub.add_parser("grade", help="grade every enabled endpoint in the registry")
    grade.add_argument("--registry", type=Path, default=Path("data/registry.json"))
    grade.add_argument("--out", type=Path, default=Path("site"))
    grade.add_argument("--offline", action="store_true",
                       help="read fixtures instead of the network")
    grade.add_argument("--fixtures", type=Path, default=None)
    grade.add_argument("--history", type=Path, default=Path("data/history.json"),
                       help="capability drift history file (read and updated each run)")
    grade.add_argument("--vantage", default="unspecified",
                       help="label for where this run measured from; latency is single-vantage "
                            "and a network path difference must not be read as a server change")
    recheck = sub.add_parser(
        "recheck",
        help="re-probe previously rejected candidates; reports only, never edits the registry")
    recheck.add_argument("--candidates", type=Path, default=Path("data/rejected.json"))
    args = parser.parse_args(argv)

    if args.command == "recheck":
        return _recheck(args.candidates)

    if args.offline and args.fixtures is None:
        print("--offline requires --fixtures", file=sys.stderr)
        return 2

    try:
        endpoints = [e for e in load_registry(args.registry) if e.enabled]
    except (OSError, ValueError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    today = time.strftime("%Y-%m-%d", time.gmtime())
    history = load_history(args.history)
    scorecards = [_grade_endpoint(e, offline=args.offline, fixtures=args.fixtures,
                                  history=history, today=today, vantage=args.vantage)
                  for e in endpoints]
    save_history(args.history, history)

    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scorecards.json").write_text(
        to_json(scorecards, generated_at=generated_at, vantage=args.vantage), encoding="utf-8")
    (args.out / "index.html").write_text(
        render_html(scorecards, generated_at=generated_at, vantage=args.vantage),
        encoding="utf-8")
    for s in scorecards:
        print(f"{s.grade}  {s.endpoint_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
