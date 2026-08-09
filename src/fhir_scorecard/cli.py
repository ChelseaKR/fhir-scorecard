"""Command-line entry point: grade a registry of endpoints, online or from offline fixtures."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cohort import Cohort, load_cohort_dir
from fhir_scorecard.dataset import write_dataset
from fhir_scorecard.drift import load_history, observe, save_history
from fhir_scorecard.fetch import FetchResult, fetch_json
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.registry import Endpoint, load_registry, version_prefix
from fhir_scorecard.report import render_html, to_json
from fhir_scorecard.reprobe import format_report, load_candidates, reprobe
from fhir_scorecard.site import (
    DEFAULT_ORIGIN,
    claim_page,
    cohort_page,
    endpoint_page,
    home_page,
    how_we_grade_page,
    kind_page,
    org_display_name,
    org_page,
    org_slug,
    robots,
    sitemap,
    status_badge,
    write_page,
)
from fhir_scorecard.vantage import VantageProbe, load_probe_files, reconcile, write_probes


def _offline_fetch(fixtures: Path, endpoint_id: str, filename: str, url: str) -> FetchResult:
    path = fixtures / endpoint_id / filename
    if not path.is_file():
        return FetchResult(url=url, ok=False, status=None, elapsed_ms=0, body=b"",
                           error="no fixture")
    return FetchResult(url=url, ok=True, status=200, elapsed_ms=1,
                       body=path.read_bytes(), error=None)


def _grade_endpoint(endpoint: Endpoint, *, offline: bool, fixtures: Path | None,
                    history: dict[str, Any], today: str, vantage: str,
                    other_probes: dict[str, list[VantageProbe]],
                    probes_seen: dict[str, VantageProbe]) -> Scorecard:
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

    mine = VantageProbe(
        vantage=vantage, reachable=metadata.ok, elapsed_ms=metadata.elapsed_ms,
        error=metadata.error,
        capability=metadata.body.decode("utf-8", "replace") if metadata.ok else None,
        smart=smart.body.decode("utf-8", "replace") if smart.ok else None)
    probes_seen[endpoint.endpoint_id] = mine
    all_probes = [mine, *other_probes.get(endpoint.endpoint_id, [])]
    consensus = reconcile(all_probes) if len(all_probes) > 1 else None

    # Availability reflects the reconciled view: an endpoint another vantage reached was up,
    # whatever this network saw.
    was_up = consensus.reachable if consensus is not None else metadata.ok

    # If this vantage was blocked but another retrieved the documents, grade their content
    # rather than scoring zero for material we simply never received.
    if not metadata.ok and consensus is not None and consensus.capability:
        facts = parse_capability(consensus.capability.encode("utf-8"))
        if consensus.smart:
            smart_facts = parse_smart(consensus.smart.encode("utf-8"))

    drift = observe(history, endpoint.endpoint_id, facts, today, reachable=was_up)
    return build_scorecard(endpoint.endpoint_id, endpoint.name, metadata, facts, smart_facts,
                           kind=endpoint.kind, vantage=vantage, consensus=consensus,
                           version_prefix=version_prefix(endpoint.expects),
                           observed_since=drift.first_seen,
                           drift_events=drift.recorded_events,
                           availability=drift.availability.summary())


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
    grade.add_argument("--origin", default=DEFAULT_ORIGIN,
                       help="canonical site origin, used for canonical URLs and the sitemap")
    grade.add_argument("--probes-out", type=Path, default=None,
                       help="write this run's per-endpoint probe results for later merging")
    grade.add_argument("--probes-in", type=Path, nargs="*", default=None,
                       help="probe files from other vantages to reconcile with this run")
    grade.add_argument("--vantage", default="unspecified",
                       help="label for where this run measured from; latency is single-vantage "
                            "and a network path difference must not be read as a server change")
    grade.add_argument("--cohorts", type=Path, default=Path("data/cohorts"),
                       help="directory of curated cohort files, each published as its own page; "
                            "an absent directory means no cohorts, a file that fails validation "
                            "fails the build")
    mcp = sub.add_parser(
        "mcp", help="serve the published dataset over MCP (stdio, read-only)")
    mcp.add_argument("--site", type=Path, default=Path("site"),
                     help="directory containing a generated api/index.json")
    recheck = sub.add_parser(
        "recheck",
        help="re-probe previously rejected candidates; reports only, never edits the registry")
    recheck.add_argument("--candidates", type=Path, default=Path("data/rejected.json"))
    args = parser.parse_args(argv)

    if args.command == "recheck":
        return _recheck(args.candidates)

    if args.command == "mcp":
        from fhir_scorecard.mcp import serve
        return serve(args.site)

    if args.offline and args.fixtures is None:
        print("--offline requires --fixtures", file=sys.stderr)
        return 2

    try:
        endpoints = [e for e in load_registry(args.registry) if e.enabled]
    except (OSError, ValueError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    # Validated before any probe leaves this machine: a cohort that references an endpoint the
    # graded registry does not carry should fail the build here, not after a network run.
    try:
        cohorts = load_cohort_dir(args.cohorts, frozenset(e.endpoint_id for e in endpoints))
    except (OSError, ValueError) as exc:
        print(f"cohort error: {exc}", file=sys.stderr)
        return 2

    today = time.strftime("%Y-%m-%d", time.gmtime())
    history = load_history(args.history)
    other_probes = load_probe_files(list(args.probes_in or []))
    probes_seen: dict[str, VantageProbe] = {}
    scorecards = [_grade_endpoint(e, offline=args.offline, fixtures=args.fixtures,
                                  history=history, today=today, vantage=args.vantage,
                                  other_probes=other_probes, probes_seen=probes_seen)
                  for e in endpoints]
    save_history(args.history, history)
    if args.probes_out is not None:
        write_probes(args.probes_out, args.vantage, probes_seen)

    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scorecards.json").write_text(
        to_json(scorecards, generated_at=generated_at, vantage=args.vantage), encoding="utf-8")
    (args.out / "index.html").write_text(
        render_html(scorecards, generated_at=generated_at, vantage=args.vantage),
        encoding="utf-8")
    _write_site(scorecards, endpoints, args.out, args.origin, generated_at, cohorts)
    write_dataset(args.out, scorecards, endpoints, origin=args.origin.rstrip("/"),
                  generated_at=generated_at, vantage=args.vantage)

    for s in scorecards:
        print(f"{s.grade}  {s.endpoint_id}")
    return 0


def _write_site(scorecards: list[Scorecard], endpoints: list[Endpoint], out: Path,
                origin: str, generated_at: str, cohorts: tuple[Cohort, ...] = ()) -> None:
    """One indexable page per endpoint, organization, category, and cohort, plus sitemap."""
    origin = origin.rstrip("/")
    by_id = {e.endpoint_id: e for e in endpoints}
    pages = [home_page(scorecards, origin, cohorts), how_we_grade_page(origin),
             claim_page(origin)]
    cards_by_id = {card.endpoint_id: card for card in scorecards}
    pages.extend(cohort_page(cohort, cards_by_id, origin) for cohort in cohorts)

    for card in scorecards:
        entry = by_id.get(card.endpoint_id)
        pages.append(endpoint_page(
            card,
            base_url=entry.base_url if entry else "",
            verified=(f"{entry.verified_method} (recorded {entry.verified_date})"
                      if entry else "verification record unavailable"),
            origin=origin,
        ))

    by_kind: dict[str, list[Scorecard]] = {}
    for card in scorecards:
        by_kind.setdefault(card.kind, []).append(card)
    pages.extend(kind_page(kind, cards, origin) for kind, cards in by_kind.items())

    # Organization pages only where an organization actually has more than one surface;
    # a page that duplicates a single endpoint page is thin content, not a search surface.
    by_org: dict[str, list[Scorecard]] = {}
    for card in scorecards:
        by_org.setdefault(org_slug(card.name), []).append(card)
    for cards in by_org.values():
        if len(cards) > 1:
            pages.append(org_page(org_display_name([c.name for c in cards]), cards, origin))

    for page in pages:
        write_page(out, page, origin, generated_at)
    badge_dir = out / "badge"
    badge_dir.mkdir(parents=True, exist_ok=True)
    for card in scorecards:
        (badge_dir / f"{card.endpoint_id}.svg").write_text(
            status_badge(card), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(pages, origin), encoding="utf-8")
    (out / "robots.txt").write_text(robots(origin), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
