"""Command-line entry point: grade a registry of endpoints, online or from offline fixtures."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fhir_scorecard.accessibility import audit_accessibility
from fhir_scorecard.audit import audit_site
from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cohort import Cohort, load_cohort_dir
from fhir_scorecard.dataset import write_dataset
from fhir_scorecard.drift import ensure_mode, load_history, observe, save_history
from fhir_scorecard.fetch import TIMEOUT_S, FetchResult, fetch_json
from fhir_scorecard.gate import GRADE_ORDER, evaluate
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.registry import EXPECTS, KINDS, Endpoint, load_registry, version_prefix
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
    write_assets,
    write_page,
)
from fhir_scorecard.vantage import VantageProbe, load_probe_files, reconcile, write_probes
from fhir_scorecard.weight import audit_weight


def _offline_fetch(fixtures: Path, endpoint_id: str, filename: str, url: str) -> FetchResult:
    path = fixtures / endpoint_id / filename
    if not path.is_file():
        return FetchResult(
            url=url, ok=False, status=None, elapsed_ms=0, body=b"", error="no fixture"
        )
    return FetchResult(
        url=url, ok=True, status=200, elapsed_ms=1, body=path.read_bytes(), error=None
    )


def _grade_from_probes(
    endpoint: Endpoint,
    *,
    history: dict[str, Any],
    today: str,
    other_probes: dict[str, list[VantageProbe]],
) -> Scorecard:
    """Grade from probe files alone, making no request of this run's own.

    The publishing run used to re-probe every endpoint that the probing runs had just probed.
    That added a fourth request to each endpoint's day for nothing new, and it merged the
    publishing run's probe under a label one of the artifacts already carried, so every card
    reported one more vantage than had actually reported. A run that has three vantages'
    documents in hand has nothing left to observe.
    """
    probes = other_probes.get(endpoint.endpoint_id, [])
    consensus = reconcile(probes) if probes else None
    reachable = consensus is not None and consensus.reachable
    # ``is not None``, not truthiness: a vantage that reached the endpoint and got back an
    # empty body retrieved a document, just an empty one. Gating on the encoded body's
    # truthiness treated that the same as no vantage having retrieved anything, so an endpoint
    # that genuinely answered with nothing published as "not observed" instead of the
    # unparseable-document finding a directly-probed run gives the same response.
    capability_retrieved = consensus is not None and consensus.capability is not None
    smart_retrieved = consensus is not None and consensus.smart is not None
    capability_body = (consensus.capability or "").encode("utf-8") if consensus else b""
    smart_body = (consensus.smart or "").encode("utf-8") if consensus else b""
    metadata = FetchResult(
        url=f"{endpoint.base_url}/metadata",
        ok=reachable,
        status=200 if reachable else None,
        elapsed_ms=consensus.elapsed_ms if consensus is not None else 0,
        body=capability_body,
        error=None
        if reachable
        else (consensus.detail if consensus is not None else "no vantage reported"),
    )
    facts = parse_capability(capability_body) if capability_retrieved else NO_CAPABILITY_RETRIEVED
    smart_facts = parse_smart(smart_body) if smart_retrieved else NO_SMART_RETRIEVED
    drift = observe(history, endpoint.endpoint_id, facts, today, reachable=reachable)
    # Name the vantages that did report, so a single-vantage merge does not attribute the
    # measurement to a run that never made one.
    reported = ", ".join(sorted({p.vantage for p in probes})) or "no vantage reported"
    return build_scorecard(
        endpoint.endpoint_id,
        endpoint.name,
        metadata,
        facts,
        smart_facts,
        kind=endpoint.kind,
        vantage=reported,
        consensus=consensus,
        version_prefix=version_prefix(endpoint.expects),
        observed_since=drift.first_seen,
        drift_events=drift.recorded_events,
        drift_alternations=drift.alternations,
        availability=drift.availability.summary(),
    )


def _grade_endpoint(
    endpoint: Endpoint,
    *,
    offline: bool,
    fixtures: Path | None,
    history: dict[str, Any],
    today: str,
    vantage: str,
    other_probes: dict[str, list[VantageProbe]],
    probes_seen: dict[str, VantageProbe],
) -> Scorecard:
    metadata_url = f"{endpoint.base_url}/metadata"
    smart_url = f"{endpoint.base_url}/.well-known/smart-configuration"
    if offline and fixtures is not None:
        metadata = _offline_fetch(fixtures, endpoint.endpoint_id, "metadata.json", metadata_url)
        smart = _offline_fetch(fixtures, endpoint.endpoint_id, "smart.json", smart_url)
    else:
        metadata = fetch_json(metadata_url)
        smart = fetch_json(smart_url)
    mine = VantageProbe(
        vantage=vantage,
        reachable=metadata.ok,
        elapsed_ms=metadata.elapsed_ms,
        error=metadata.error,
        capability=metadata.body.decode("utf-8", "replace") if metadata.ok else None,
        smart=smart.body.decode("utf-8", "replace") if smart.ok else None,
    )
    probes_seen[endpoint.endpoint_id] = mine
    all_probes = [mine, *other_probes.get(endpoint.endpoint_id, [])]
    consensus = reconcile(all_probes) if len(all_probes) > 1 else None

    # Availability reflects the reconciled view: an endpoint another vantage reached was up,
    # whatever this network saw.
    was_up = consensus.reachable if consensus is not None else metadata.ok

    if metadata.ok:
        facts = parse_capability(metadata.body)
        # This run reached the host, so it did ask for the SMART document: a failed SMART fetch
        # is an observation that it is absent or unusable, and grades as one.
        smart_facts = parse_smart(smart.body) if smart.ok else parse_smart(b"")
    elif consensus is not None and consensus.capability is not None:
        # This vantage was blocked but another retrieved the documents: grade their content
        # rather than scoring zero for material we simply never received. ``is not None``, not
        # truthiness: a peer vantage that reached the endpoint and got back an empty body
        # retrieved a document, just an empty one, and that must still be graded rather than
        # falling through to "nothing was retrieved by anyone".
        facts = parse_capability(consensus.capability.encode("utf-8"))
        smart_facts = (
            parse_smart(consensus.smart.encode("utf-8"))
            if consensus.smart is not None
            else NO_SMART_RETRIEVED
        )
    else:
        # Nothing was retrieved by anyone. The content dimensions are not scored, because every
        # finding in them would be a claim about a document this run never saw.
        facts = NO_CAPABILITY_RETRIEVED
        smart_facts = NO_SMART_RETRIEVED

    drift = observe(history, endpoint.endpoint_id, facts, today, reachable=was_up)
    return build_scorecard(
        endpoint.endpoint_id,
        endpoint.name,
        metadata,
        facts,
        smart_facts,
        kind=endpoint.kind,
        vantage=vantage,
        consensus=consensus,
        version_prefix=version_prefix(endpoint.expects),
        observed_since=drift.first_seen,
        drift_events=drift.recorded_events,
        drift_alternations=drift.alternations,
        availability=drift.availability.summary(),
    )


def _check_slug(base_url: str) -> str:
    """A stable, non-attributive identifier for a one-off check.

    Derived from the host the caller named, never from a path segment and never from an
    organization name this run has no verification record for. The registry's attribution rules
    do not apply to a check that publishes nothing, and inventing an entry that looked as though
    they did is the thing to avoid.
    """
    host = urlsplit(base_url).netloc.casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")[:64]
    return slug if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", slug) else "checked-endpoint"


def _cmd_check(args: argparse.Namespace) -> int:
    """Grade one endpoint from its own public documents and apply the caller's threshold.

    Registry-free by design. The registry records how each listed endpoint was verified and who
    it may be attributed to; a CI check of an endpoint the caller already operates has no such
    record to make, and synthesizing one would put a verification claim in the artifact that
    nobody performed. Nothing here is written to ``data/``, no history is opened, and no page is
    rendered: a check observes, reports, and exits.
    """
    base_url = str(args.base_url).rstrip("/")
    if not base_url.startswith("https://"):
        print("check: base URL must be https", file=sys.stderr)
        return 2

    metadata = fetch_json(f"{base_url}/metadata", timeout=args.timeout)
    smart = fetch_json(f"{base_url}/.well-known/smart-configuration", timeout=args.timeout)
    if metadata.ok:
        facts = parse_capability(metadata.body)
        # This run reached the host, so it did ask for the SMART document: a failed fetch is an
        # observation that it is absent or unusable, and grades as one.
        smart_facts = parse_smart(smart.body) if smart.ok else parse_smart(b"")
    else:
        # Nothing was retrieved. Every content finding would be a claim about a document this
        # run never saw, so the content dimensions are not scored at all.
        facts = NO_CAPABILITY_RETRIEVED
        smart_facts = NO_SMART_RETRIEVED

    card = build_scorecard(
        _check_slug(base_url),
        args.name or urlsplit(base_url).netloc,
        metadata,
        facts,
        smart_facts,
        kind=args.kind,
        vantage=args.vantage,
        version_prefix=version_prefix(args.expects),
        # No drift, no availability, no first-seen date. One observation is not a record of
        # one, and a check must not write into the record the daily run keeps.
    )

    payload = to_json(
        [card],
        generated_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        vantage=args.vantage,
    )
    if args.json_out is not None:
        # Written before the threshold is applied, so a failing gate still leaves behind the
        # complete evidence a reader needs to disagree with it.
        try:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(f"check: could not write {args.json_out}: {exc}", file=sys.stderr)
            return 2

    print(f"{card.grade}  {base_url}")
    for dimension in card.dimensions:
        measured = "not observed on this run" if dimension.score is None else f"{dimension.score}"
        print(f"  {dimension.title}: {measured}")

    outcome = evaluate(card, min_grade=args.min_grade, detail=metadata.error or "")
    if not outcome.passed:
        print(f"gate: {outcome.reason}", file=sys.stderr)
        return 1
    return 0


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fhir-scorecard")
    sub = parser.add_subparsers(dest="command", required=True)
    grade = sub.add_parser("grade", help="grade every enabled endpoint in the registry")
    grade.add_argument("--registry", type=Path, default=Path("data/registry.json"))
    grade.add_argument("--out", type=Path, default=Path("site"))
    grade.add_argument(
        "--offline", action="store_true", help="read fixtures instead of the network"
    )
    grade.add_argument("--fixtures", type=Path, default=None)
    grade.add_argument(
        "--history",
        type=Path,
        default=None,
        help="capability drift history file (read and updated each run); "
        "defaults to data/history.json for a live run and to "
        ".cache/offline-history.json under --offline, so a fixture run "
        "cannot write observations into the real availability record",
    )
    grade.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        help="canonical site origin, used for canonical URLs and the sitemap",
    )
    grade.add_argument(
        "--probes-out",
        type=Path,
        default=None,
        help="write this run's per-endpoint probe results for later merging",
    )
    grade.add_argument(
        "--probes-in",
        type=Path,
        nargs="*",
        default=None,
        help="probe files from other vantages to reconcile with this run",
    )
    grade.add_argument(
        "--from-probes",
        action="store_true",
        help="grade from --probes-in alone and make no requests of this run's "
        "own; for a publishing run whose vantages have already probed",
    )
    grade.add_argument(
        "--vantage",
        default="unspecified",
        help="label for where this run measured from; latency is single-vantage "
        "and a network path difference must not be read as a server change",
    )
    grade.add_argument(
        "--cohorts",
        type=Path,
        default=None,
        help="directory of curated cohort files, each published as its own page; "
        "an absent directory means no cohorts, a file that fails validation "
        "fails the build. Defaults to data/cohorts for a live run, and to "
        "no cohorts under --offline, whose fixture registry is a subset the "
        "shipped cohorts do not match",
    )
    mcp = sub.add_parser("mcp", help="serve the published dataset over MCP (stdio, read-only)")
    mcp.add_argument(
        "--site",
        type=Path,
        default=Path("site"),
        help="directory containing a generated api/index.json",
    )
    mcp.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="repository root holding corpus/ for the cited_passages tool",
    )
    narrate = sub.add_parser(
        "narrate",
        help="explain one published scorecard in plain language with citations verified "
        "against corpus/ (calls a model; needs the `ai` extra and FHIR_AI_PROVIDER)",
    )
    narrate.add_argument(
        "--scorecards", type=Path, default=Path("site/scorecards.json"), help="published dataset"
    )
    narrate.add_argument("--endpoint", required=True, help="endpoint_id to narrate")
    narrate.add_argument("--language", choices=("en", "es"), default="en")
    narrate.add_argument(
        "--root", type=Path, default=Path("."), help="repository root with corpus/"
    )
    narrate.add_argument("--json", action="store_true", help="emit the full record")
    recheck = sub.add_parser(
        "recheck",
        help="re-probe previously rejected candidates; reports only, never edits the registry",
    )
    recheck.add_argument("--candidates", type=Path, default=Path("data/rejected.json"))
    check = sub.add_parser(
        "check",
        help="grade one endpoint from its own public documents and optionally gate a build; "
        "publishes nothing and touches no registry, history, or site",
    )
    check.add_argument("base_url", metavar="BASE_URL", help="FHIR base URL, https only")
    check.add_argument(
        "--name",
        default="",
        help="display name for the report; defaults to the host, because a check has no "
        "verification record and must not put a name behind an address on a guess",
    )
    check.add_argument("--kind", choices=sorted(KINDS), default="reference")
    check.add_argument("--expects", choices=EXPECTS, default="r4")
    check.add_argument(
        "--min-grade",
        choices=GRADE_ORDER,
        default="",
        help="fail with exit 1 when the measured grade is below this letter. Omit it and the "
        "check is informational: it reports what it saw and exits 0",
    )
    check.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="write the complete result, with its disclaimer, before the threshold is applied",
    )
    check.add_argument(
        "--vantage",
        default="unspecified",
        help="label for where this run measured from; one run is one network path, and a "
        "latency or reachability difference must not be read as a server change",
    )
    check.add_argument("--timeout", type=float, default=TIMEOUT_S)
    audit = sub.add_parser(
        "audit-site",
        help="check a built site: the contract in fhir_scorecard.audit (sitemap completeness, "
        "canonical correctness, structured data, internal links, orphans), the mechanical "
        "accessibility rules in fhir_scorecard.accessibility, and the transfer-size budgets "
        "in fhir_scorecard.weight",
    )
    audit.add_argument("directory", metavar="DIR", type=Path, help="a built site directory")
    audit.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        help="the origin the site was built for; canonical URLs and sitemap entries are "
        "checked against it, so auditing a build under the wrong origin fails",
    )
    return parser


def _history_path(args: argparse.Namespace) -> Path:
    """Where this run's observations go.

    An offline run defaults to a scratch path. The README's own offline command used to write a
    ``{"up": false}`` for every endpoint in the registry into ``data/history.json``, on a date
    that had none, and exit 0.
    """
    if args.history is not None:
        return Path(args.history)
    return Path(".cache/offline-history.json") if args.offline else Path("data/history.json")


def _cohorts_path(args: argparse.Namespace) -> Path | None:
    """Cohort directory, or None when an offline run did not ask for one.

    The shipped cohorts reference registry ids a fixture registry does not carry, and a cohort
    that references an endpoint the graded registry lacks fails the build by design.
    """
    if args.cohorts is not None:
        return Path(args.cohorts)
    return None if args.offline else Path("data/cohorts")


def _prepare_history(args: argparse.Namespace) -> tuple[Path, dict[str, Any]] | str:
    """Open the run's history file, or return why this run must not write to it.

    An offline run must never append "did not answer" to a record of live observations, and a
    live run must never continue one a fixture run started.
    """
    path = _history_path(args)
    history = load_history(path)
    try:
        ensure_mode(history, offline=args.offline)
    except ValueError as exc:
        return str(exc)
    if args.offline and args.history is None:
        print(f"offline run: history goes to {path}, not data/history.json", file=sys.stderr)
    return path, history


def _flag_conflict(args: argparse.Namespace) -> str | None:
    """Reject flag combinations that could only produce a claim the run cannot support."""
    if args.offline and args.fixtures is None:
        return "--offline requires --fixtures"
    if args.from_probes and not args.probes_in:
        return "--from-probes requires --probes-in"
    if args.from_probes and args.probes_out is not None:
        # A run that makes no observation has none to publish, and writing an empty or borrowed
        # probe file under this run's label is how a vantage gets counted that never reported.
        return "--from-probes makes no observation of its own; --probes-out has nothing to write"
    return None


def _run_standalone(args: argparse.Namespace) -> int | None:
    """Run the commands that need no registry, cohorts, or history, or return None.

    ``grade`` is the only command that opens the curated data; keeping the others out of its
    setup path is what lets ``check`` be genuinely registry-free rather than registry-free by
    remembering to skip a step.
    """
    if args.command == "recheck":
        return _recheck(args.candidates)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "mcp":
        from fhir_scorecard.mcp import serve

        return serve(args.site, root=args.root)
    if args.command == "narrate":
        return _cmd_narrate(args)
    if args.command == "audit-site":
        return _cmd_audit_site(args)
    return None


def _cmd_audit_site(args: argparse.Namespace) -> int:
    """Report every way a built site breaks its contract, and exit nonzero if it does.

    Three families run, and all three run every time: the site contract (sitemap, canonical,
    structured data, links, orphans), the mechanical accessibility rules, and the transfer-size
    budgets. They are not separately switchable on purpose - a publish that could skip one is a
    publish that will.

    Exit 2 is reserved for "there was nothing to audit", which is a usage error and must not
    read as a clean site. Exit 1 means the site was read and found wanting.
    """
    if not args.directory.is_dir():
        print(f"audit error: {args.directory} is not a directory", file=sys.stderr)
        return 2
    findings = (
        audit_site(args.directory, args.origin.rstrip("/"))
        + audit_accessibility(args.directory)
        + audit_weight(args.directory)
    )
    for finding in sorted(findings, key=lambda f: (f.where, f.code, f.detail)):
        print(finding)
    if findings:
        print(f"{len(findings)} site finding(s) against {args.origin}", file=sys.stderr)
        return 1
    print(f"site contract, accessibility and weight budgets: clean against {args.origin}")
    return 0


def _cmd_narrate(args: argparse.Namespace) -> int:
    """Narrate one published scorecard (ADR 0003). Imports the `ai` extra lazily so every
    other command keeps the standard-library-only boundary."""
    from fhir_scorecard.ai.corpus import CorpusError, CorpusIndex
    from fhir_scorecard.ai.narrate import NarrationError, narrate
    from fhir_scorecard.ai.provider import ProviderError, provider_from_env

    try:
        payload = json.loads(args.scorecards.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"narrate: cannot read {args.scorecards}: {exc}", file=sys.stderr)
        return 2
    records = payload.get("scorecards", []) if isinstance(payload, dict) else []
    record = next((r for r in records if r.get("endpoint_id") == args.endpoint), None)
    if record is None:
        print(f"narrate: unknown endpoint {args.endpoint!r}", file=sys.stderr)
        return 2
    try:
        narration = narrate(
            record,
            corpus=CorpusIndex.load(args.root),
            provider=provider_from_env(),
            language=args.language,
        )
    except (CorpusError, NarrationError, ProviderError) as exc:
        print(f"narrate: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(narration.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"{narration.name} ({narration.endpoint_id}): grade {narration.grade}")
    if not narration.model_called:
        # The documented outcome for a record with nothing to cite: the model
        # was never invoked, and the receipt says so rather than showing a
        # narration whose every claim was withheld.
        print(
            f"Not narrated: {narration.not_narrated_reason}. The record offers no "
            "specification passage a claim could cite, so the model was not called "
            "(0 input tokens, 0 output tokens)."
        )
        print(f"Provider: {narration.provider}. Prompt version: {narration.prompt_version}.")
        return 0
    print(narration.label)
    print()
    for number, claim in enumerate(narration.claims, start=1):
        print(f"{number}. {claim.text}")
        for citation in claim.citations:
            print(f'   - {citation.source_label} ({citation.passage_id}): "{citation.quote}"')
    if narration.withheld_count:
        print()
        print(
            f"{narration.withheld_count} statement(s) withheld because a citation did not "
            "verify against the retained specification text."
        )
    print(f"Model: {narration.model}. Prompt version: {narration.prompt_version}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    standalone = _run_standalone(args)
    if standalone is not None:
        return standalone

    conflict = _flag_conflict(args)
    if conflict is not None:
        print(conflict, file=sys.stderr)
        return 2

    try:
        endpoints = [e for e in load_registry(args.registry) if e.enabled]
    except (OSError, ValueError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    # Validated before any probe leaves this machine: a cohort that references an endpoint the
    # graded registry does not carry should fail the build here, not after a network run.
    cohorts_path = _cohorts_path(args)
    try:
        cohorts = (
            load_cohort_dir(cohorts_path, frozenset(e.endpoint_id for e in endpoints))
            if cohorts_path is not None
            else ()
        )
    except (OSError, ValueError) as exc:
        print(f"cohort error: {exc}", file=sys.stderr)
        return 2

    today = time.strftime("%Y-%m-%d", time.gmtime())
    prepared = _prepare_history(args)
    if isinstance(prepared, str):
        print(f"history error: {prepared}", file=sys.stderr)
        return 2
    history_path, history = prepared
    other_probes = load_probe_files(list(args.probes_in or []))
    probes_seen: dict[str, VantageProbe] = {}
    run_vantage = args.vantage
    if args.from_probes:
        # The published "vantage" must name where the measurement came from. A run that only
        # reconciles has no vantage of its own, so it reports the ones that reported to it.
        labels = sorted({p.vantage for probes in other_probes.values() for p in probes})
        run_vantage = "reconciled from " + ", ".join(labels) if labels else "no vantage reported"
        scorecards = [
            _grade_from_probes(e, history=history, today=today, other_probes=other_probes)
            for e in endpoints
        ]
    else:
        scorecards = [
            _grade_endpoint(
                e,
                offline=args.offline,
                fixtures=args.fixtures,
                history=history,
                today=today,
                vantage=args.vantage,
                other_probes=other_probes,
                probes_seen=probes_seen,
            )
            for e in endpoints
        ]
    save_history(history_path, history)
    if args.probes_out is not None:
        write_probes(args.probes_out, args.vantage, probes_seen)

    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scorecards.json").write_text(
        to_json(scorecards, generated_at=generated_at, vantage=run_vantage), encoding="utf-8"
    )
    (args.out / "index.html").write_text(
        render_html(scorecards, generated_at=generated_at, vantage=run_vantage), encoding="utf-8"
    )
    _write_site(scorecards, endpoints, args.out, args.origin, generated_at, cohorts)
    write_dataset(
        args.out,
        scorecards,
        endpoints,
        origin=args.origin.rstrip("/"),
        generated_at=generated_at,
        vantage=run_vantage,
    )

    for s in scorecards:
        print(f"{s.grade}  {s.endpoint_id}")
    return 0


def _verification_sentence(entry: Endpoint | None) -> str:
    """What the provenance section says, including how old the newest check actually is.

    The date used to be the curation date and nothing else, so an entry curated once and never
    looked at again read exactly like one re-checked this morning. Both dates are printed when
    both exist, and an entry with no re-check says that in words rather than by omission.
    """
    if entry is None:
        return "verification record unavailable"
    if entry.verification_basis == "publisher_documented":
        listed = (
            f"Listed on the organization's own publication of this base URL, not on a retrieved "
            f"conformance document: {entry.verified_method} "
            f"(recorded {entry.verified_date}). Published at {entry.verification_source}. "
            f"On the verification date this probe observed: {entry.verification_observed}."
        )
    else:
        listed = f"{entry.verified_method} (recorded {entry.verified_date})."
    if entry.reverified_date:
        return f"{listed} Re-checked {entry.reverified_date}: {entry.reverified_method}."
    return (
        f"{listed} No later re-check is recorded, so the date above is the last time anyone "
        "checked this entry against the live endpoint."
    )


def _organizations(
    scorecards: list[Scorecard],
) -> tuple[dict[str, list[Scorecard]], dict[str, tuple[str, str]]]:
    """Endpoints grouped by organization, and the org page each endpoint should link to.

    The second mapping covers only organizations with more than one surface, which are the
    only ones that get an /org/ page. It is computed before the endpoint pages are built, not
    after: building the pages first and the groups afterwards is how twelve /org/ pages came to
    be published and listed in the sitemap with no page on the site linking to any of them.
    """
    by_org: dict[str, list[Scorecard]] = {}
    for card in scorecards:
        by_org.setdefault(org_slug(card.name), []).append(card)
    org_of: dict[str, tuple[str, str]] = {}
    for slug, cards in by_org.items():
        if len(cards) > 1:
            display = org_display_name([c.name for c in cards])
            for card in cards:
                org_of[card.endpoint_id] = (display, slug)
    return by_org, org_of


def _write_site(
    scorecards: list[Scorecard],
    endpoints: list[Endpoint],
    out: Path,
    origin: str,
    generated_at: str,
    cohorts: tuple[Cohort, ...] = (),
) -> None:
    """One indexable page per endpoint, organization, category, and cohort, plus sitemap."""
    origin = origin.rstrip("/")
    by_id = {e.endpoint_id: e for e in endpoints}
    pages = [home_page(scorecards, origin, cohorts), how_we_grade_page(origin), claim_page(origin)]
    cards_by_id = {card.endpoint_id: card for card in scorecards}
    pages.extend(cohort_page(cohort, cards_by_id, origin) for cohort in cohorts)

    by_org, org_of = _organizations(scorecards)
    for card in scorecards:
        entry = by_id.get(card.endpoint_id)
        pages.append(
            endpoint_page(
                card,
                base_url=entry.base_url if entry else "",
                verified=_verification_sentence(entry),
                origin=origin,
                organization=org_of.get(card.endpoint_id),
            )
        )

    by_kind: dict[str, list[Scorecard]] = {}
    for card in scorecards:
        by_kind.setdefault(card.kind, []).append(card)
    pages.extend(kind_page(kind, cards, origin) for kind, cards in by_kind.items())

    # Organization pages only where an organization actually has more than one surface;
    # a page that duplicates a single endpoint page is thin content, not a search surface.
    for cards in by_org.values():
        if len(cards) > 1:
            pages.append(org_page(org_display_name([c.name for c in cards]), cards, origin))

    for page in pages:
        write_page(out, page, origin, generated_at)
    badge_dir = out / "badge"
    badge_dir.mkdir(parents=True, exist_ok=True)
    for card in scorecards:
        (badge_dir / f"{card.endpoint_id}.svg").write_text(status_badge(card), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(pages, origin), encoding="utf-8")
    (out / "robots.txt").write_text(robots(origin), encoding="utf-8")
    write_assets(out)


if __name__ == "__main__":
    raise SystemExit(main())
