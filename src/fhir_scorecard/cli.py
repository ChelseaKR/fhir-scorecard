"""Command-line entry point: grade a registry of endpoints, online or from offline fixtures."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fhir_scorecard.accessibility import audit_accessibility
from fhir_scorecard.archive import (
    ARCHIVE_PATH,
    history_json,
    index_page,
    mode_of,
    record_page,
    records,
)
from fhir_scorecard.audit import audit_site
from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.ci_report import EndpointResult, to_junit, to_sarif
from fhir_scorecard.cohort import Cohort, load_cohort_dir
from fhir_scorecard.coverage import classify, read_frame, read_reviewed_rows_by_cohort
from fhir_scorecard.coverage import page as coverage_page
from fhir_scorecard.dataset import write_dataset
from fhir_scorecard.drift import ensure_mode, load_history, observe, save_history
from fhir_scorecard.fetch import TIMEOUT_S, FetchResult, fetch_json
from fhir_scorecard.gate import GRADE_ORDER, evaluate
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.intake import ClaimError, assess, claim_from_form
from fhir_scorecard.intake import build_proposal as build_claim_proposal
from fhir_scorecard.intake import format_comment as format_claim_comment
from fhir_scorecard.intake import format_report as format_claim_report
from fhir_scorecard.leaderboard import page as availability_page
from fhir_scorecard.operator import (
    OperatorEndpoint,
    OperatorRegistryError,
    load_operator_registry,
)
from fhir_scorecard.over_time import page as over_time_page
from fhir_scorecard.registry import EXPECTS, KINDS, Endpoint, load_registry, version_prefix
from fhir_scorecard.report import to_json
from fhir_scorecard.reprobe import format_report, load_candidates, reprobe
from fhir_scorecard.reverify import (
    accepted_blocks,
    apply_to_registry,
    build_proposal,
    load_proposal,
    reverify_one,
    select,
)
from fhir_scorecard.reverify import format_report as format_reverify_report
from fhir_scorecard.site import (
    DEFAULT_ORIGIN,
    Page,
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
from fhir_scorecard.snapshot import MANIFEST_NAME
from fhir_scorecard.snapshot import build as build_snapshot
from fhir_scorecard.snapshot import verify as verify_snapshot
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
        # Recorded whether or not the fetch produced a document: a refusal with a status is
        # still proof the endpoint answered, and the merge needs it to avoid describing a
        # running server as one it could not reach.
        status=metadata.status,
        # The /metadata result's classification (#117), carried alongside the sentence rather
        # than instead of it. Only ``metadata``: this probe's reachability is defined by that
        # document, and a SMART failure beside a retrieved CapabilityStatement is a different
        # finding that this field would silently absorb.
        failure_kind=metadata.failure_kind,
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
        # is an observation that it is absent or unusable, and grades as one -- unless another
        # vantage holds the document, in which case it demonstrably exists and this vantage's
        # failure to get it is a fact about this vantage. That is the asymmetry the whole module
        # rests on: one vantage retrieving something settles that it is there, and one vantage
        # missing it settles nothing.
        if smart.ok:
            smart_facts = parse_smart(smart.body)
        elif consensus is not None and consensus.smart is not None:
            smart_facts = parse_smart(consensus.smart.encode("utf-8"))
        else:
            smart_facts = parse_smart(b"")
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


def _grade_one(
    base_url: str,
    *,
    endpoint_id: str,
    name: str,
    kind: str,
    expects: str,
    vantage: str,
    timeout: float,
    fixtures: Path | None = None,
) -> tuple[Scorecard, FetchResult]:
    """Grade one endpoint from its two public documents, and hand back what was retrieved.

    Shared by the single-endpoint check and the operator-registry run so the two cannot drift:
    a registry entry is graded by the same code, with the same not-observed handling, as a
    single ``check`` of the same address.
    """
    metadata_url = f"{base_url}/metadata"
    smart_url = f"{base_url}/.well-known/smart-configuration"
    if fixtures is not None:
        metadata = _offline_fetch(fixtures, endpoint_id, "metadata.json", metadata_url)
        smart = _offline_fetch(fixtures, endpoint_id, "smart.json", smart_url)
    else:
        metadata = fetch_json(metadata_url, timeout=timeout)
        smart = fetch_json(smart_url, timeout=timeout)
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
        endpoint_id,
        name,
        metadata,
        facts,
        smart_facts,
        kind=kind,
        vantage=vantage,
        version_prefix=version_prefix(expects),
        # No drift, no availability, no first-seen date. One observation is not a record of
        # one, and a check must not write into the record the daily run keeps.
    )
    return card, metadata


def _cmd_check(args: argparse.Namespace) -> int:
    """Grade one endpoint, or every endpoint in an operator's own registry.

    Registry-free by design in both modes. :mod:`fhir_scorecard.registry` records how each
    listed endpoint was verified and who it may be attributed to; a CI check of endpoints the
    caller already operates has no such record to make, and synthesizing one would put a
    verification claim in the artifact that nobody performed. Nothing here is written to
    ``data/``, no history is opened, and no page is rendered: a check observes, reports, and
    exits.
    """
    if args.registry is not None:
        return _cmd_check_registry(args)
    if not args.base_url:
        print("check: give a BASE_URL or --registry", file=sys.stderr)
        return 2
    base_url = str(args.base_url).rstrip("/")
    if not base_url.startswith("https://"):
        print("check: base URL must be https", file=sys.stderr)
        return 2

    card, metadata = _grade_one(
        base_url,
        endpoint_id=_check_slug(base_url),
        name=args.name or urlsplit(base_url).netloc,
        kind=args.kind,
        expects=args.expects,
        vantage=args.vantage,
        timeout=args.timeout,
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


def _write_artifact(path: Path, text: str, label: str) -> str:
    """Write one CI artifact, or return the message explaining why it could not be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"check: could not write {label} to {path}: {exc}"
    return ""


def _grade_registry(
    enabled: list[OperatorEndpoint], args: argparse.Namespace, fixtures: Path | None
) -> list[EndpointResult]:
    """Grade every enabled entry, in id order so the artifacts are stable."""
    results: list[EndpointResult] = []
    for entry in sorted(enabled, key=lambda e: e.endpoint_id):
        card, metadata = _grade_one(
            entry.base_url,
            endpoint_id=entry.endpoint_id,
            name=entry.name,
            kind=entry.kind,
            expects=entry.expects,
            vantage=args.vantage,
            timeout=args.timeout,
            fixtures=fixtures,
        )
        # The per-entry threshold wins where it is set. An operator whose sandbox and whose
        # production Patient Access API sit in one file should not have to run the tool twice
        # to hold them to different bars.
        min_grade = entry.min_grade or args.min_grade
        results.append(
            EndpointResult(
                entry=entry,
                card=card,
                outcome=evaluate(card, min_grade=min_grade, detail=metadata.error or ""),
                min_grade=min_grade,
                vantage=args.vantage,
            )
        )
    return results


def _write_registry_artifacts(
    args: argparse.Namespace, results: list[EndpointResult], cards: list[Scorecard]
) -> str:
    """Write whichever artifacts were asked for, or the message saying why one could not be."""
    wanted: list[tuple[Path | None, str, str]] = []
    if args.json_out is not None:
        payload = to_json(
            cards,
            generated_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            vantage=args.vantage,
        )
        wanted.append((Path(args.json_out), payload, "the result JSON"))
    if args.junit is not None:
        wanted.append((Path(args.junit), to_junit(results), "the JUnit report"))
    if args.sarif is not None:
        wanted.append((Path(args.sarif), to_sarif(results), "the SARIF report"))
    for path, text, label in wanted:
        if path is None:
            continue
        problem = _write_artifact(path, text, label)
        if problem:
            return problem
    return ""


def _print_registry_results(results: list[EndpointResult]) -> None:
    for result in results:
        print(f"{result.card.grade}  {result.entry.endpoint_id}  {result.entry.base_url}")
        for dimension in result.card.dimensions:
            measured = (
                "not observed on this run" if dimension.score is None else f"{dimension.score}"
            )
            print(f"  {dimension.title}: {measured}")


def _cmd_check_registry(args: argparse.Namespace) -> int:
    """Grade every enabled endpoint in an operator's own registry.

    Exit 2 covers everything that stopped the run from being made: an unreadable registry, an
    entry this tool refuses (a non-https base URL among them, refused at load time and so before
    any request), or an artifact that could not be written. Exit 1 means endpoints were graded
    and a threshold the caller set was not met. A run where nothing was gradeable is never 0.
    """
    try:
        entries = load_operator_registry(Path(args.registry))
    except OperatorRegistryError as exc:
        print(f"check: {exc}", file=sys.stderr)
        return 2
    enabled = [e for e in entries if e.enabled]
    if not enabled:
        print("check: every entry in the operator registry is disabled", file=sys.stderr)
        return 2
    fixtures = Path(args.fixtures) if args.offline else None
    if args.offline and fixtures is not None and not fixtures.is_dir():
        print(
            f"check: --offline needs a fixtures directory; {fixtures} is not one", file=sys.stderr
        )
        return 2

    results = _grade_registry(enabled, args, fixtures)
    cards = [r.card for r in results]

    problem = _write_registry_artifacts(args, results, cards)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    _print_registry_results(results)
    failed = [r for r in results if not r.outcome.passed]
    for result in failed:
        print(f"gate: {result.entry.endpoint_id}: {result.outcome.reason}", file=sys.stderr)

    # The roll-up separates what was graded from what was not reached. "3 endpoints checked, 0
    # below the threshold" over three endpoints nothing answered is a clean-looking summary of
    # no measurement at all, which is the same absence-as-a-value this grader refuses
    # everywhere else.
    graded = [r for r in results if r.observed]
    unreached = [r for r in results if not r.observed]
    summary = (
        f"{len(results)} endpoint(s) in the registry: {len(graded)} graded, "
        f"{len(unreached)} not reached on this run"
    )
    if unreached:
        summary += f" ({', '.join(sorted(r.entry.endpoint_id for r in unreached))})"
    print(summary)
    if not graded:
        # Said loudly, and still not turned into a failure on its own. The exit code answers
        # only the thresholds the caller set, exactly as the single-endpoint check does for the
        # same situation: a run that reached nothing is a fact about this network path, and a
        # build that goes red for it is blaming the endpoint for the runner. A caller who does
        # want that outcome has an explicit way to ask -- `--min-grade F` cannot be evaluated
        # without a grade, so it fails -- and `docs/ci-action.md` says so.
        print(
            "check: no endpoint in this registry was reached on this run, so nothing was "
            "graded. That is a fact about this run's network path as much as about these "
            "endpoints, and it is not a clean bill of health. Pass --min-grade F to make an "
            "unreached registry fail the build.",
            file=sys.stderr,
        )
    else:
        print(f"{len(failed)} of the {len(graded)} graded fell below the threshold set for them")
    return 1 if failed else 0


def _recheck(candidates_path: Path, json_out: Path | None = None) -> int:
    try:
        candidates = load_candidates(candidates_path)
    except (OSError, ValueError) as exc:
        print(f"candidates error: {exc}", file=sys.stderr)
        return 2
    results = [reprobe(c) for c in candidates]
    print(format_report(results))
    if json_out is not None:
        # A structured result, so the quarterly workflow can decide whether to open an issue
        # without grepping the human report for a marker. It used to do exactly that, which put
        # a third-party server's `software.name` in charge of the branch: a value containing
        # "NOW ANSWERS" forced a false revival issue. What a payer publishes is evidence, never
        # control flow.
        try:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(
                json.dumps(
                    {
                        "revived": sum(1 for r in results if r.now_answers),
                        "checked": len(results),
                        "candidates": [
                            {"id": r.candidate.candidate_id, "now_answers": r.now_answers}
                            for r in results
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"write error: {exc}", file=sys.stderr)
            return 2
    # Exit 0 either way: a candidate that starts answering is news, not a failure.
    return 0


def _reverify(args: argparse.Namespace) -> int:
    """Re-check registry entries, or apply a proposal a person has marked up.

    Two modes, deliberately not one. Producing a proposal reaches the network and writes only
    the proposal; applying one touches only the registry and reaches nothing. A single mode
    that fetched and wrote in one pass would put the decision and the edit in the same step,
    and the decision is the part that belongs to a person.
    """
    if args.apply is not None:
        try:
            proposal = load_proposal(args.apply)
            blocks = accepted_blocks(proposal)
            moved = apply_to_registry(args.registry, blocks)
        except (OSError, ValueError) as exc:
            print(f"reverify error: {exc}", file=sys.stderr)
            return 2
        if not moved:
            print(f"no row in {args.apply} is marked accepted; {args.registry} is unchanged")
        else:
            print(f"applied {moved} accepted re-check(s) to {args.registry}")
        return 0

    today = args.today or _dt.date.today().isoformat()
    try:
        entries = load_registry(args.registry)
        chosen = select(
            entries,
            older_than=args.older_than,
            endpoint_id=args.endpoint,
            today=today,
        )
    except (OSError, ValueError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    rows = [reverify_one(entry, today=today) for entry in chosen]
    print(format_reverify_report(rows))
    out = args.out or Path(f"reverify-{today}.json")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                build_proposal(rows, today=today, registry_path=args.registry),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"write error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote the proposal to {out}; nothing was written to {args.registry}")
    # Exit 0 either way. An entry that stopped answering is news for a person, not a build
    # failure, and this verb publishes nothing that a red exit would protect.
    return 0


def _claim(args: argparse.Namespace) -> int:
    """Read one add-endpoint submission and write a proposal and a comment.

    Exit 0 whether the claim was accepted or refused, for ``_reverify``'s reason: a refusal is
    this verb working, not a build failing, and the outcome is in the proposal file where a
    caller can read it as data. Exit 2 is reserved for a submission that could not be read at
    all, or a registry that could not be opened.

    Nothing here writes to ``--registry``. It is opened to refuse a duplicate base URL and to
    keep the suggested id from colliding, and for nothing else.
    """
    today = args.today or _dt.date.today().isoformat()
    try:
        body = args.issue.read_text(encoding="utf-8")
        claim = claim_from_form(body)
    except (OSError, ClaimError) as exc:
        print(f"claim error: {exc}", file=sys.stderr)
        return 2
    try:
        registry = load_registry(args.registry) if args.registry.exists() else []
    except (OSError, ValueError) as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    verdict = assess(claim, today=today, registry=registry)
    print(format_claim_report(verdict))
    out = args.out or Path(f"claim-{today}.json")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                build_claim_proposal(verdict, today=today, issue=args.issue_ref),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if args.comment_out is not None:
            args.comment_out.parent.mkdir(parents=True, exist_ok=True)
            args.comment_out.write_text(format_claim_comment(verdict) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"write error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote the proposal to {out}; nothing was written to {args.registry}")
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
    recheck.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="also write a machine-readable result here, for a caller that must branch on it",
    )
    reverify = sub.add_parser(
        "reverify",
        help="re-check registry entries and propose dated reverified blocks; writes a proposal "
        "file for a person to accept and never edits the registry on its own",
    )
    reverify.add_argument("--registry", type=Path, default=Path("data/registry.json"))
    reverify.add_argument(
        "--older-than",
        default=None,
        metavar="DAYS",
        help="only entries last checked at least this many days ago, e.g. 90d. Counted from "
        "the reverification date where there is one and the curation date otherwise, so an "
        "entry nobody has ever re-checked is selected rather than skipped",
    )
    reverify.add_argument("--endpoint", default=None, help="re-check one endpoint_id only")
    reverify.add_argument(
        "--out", type=Path, default=None, help="proposal path (default: reverify-<date>.json)"
    )
    reverify.add_argument(
        "--apply",
        type=Path,
        default=None,
        metavar="PROPOSAL",
        help="merge the rows a person marked accepted in this proposal into the registry, and "
        "reach no network. Rows that observed no document can never be applied",
    )
    reverify.add_argument(
        "--today",
        default=None,
        help=argparse.SUPPRESS,  # tests pin the date
    )
    claim = sub.add_parser(
        "claim",
        help="read an add-endpoint submission, retrieve the two discovery documents, and write "
        "a proposal and a comment; never edits the registry and never opens a pull request",
    )
    claim.add_argument(
        "issue",
        type=Path,
        metavar="ISSUE_BODY",
        help="a file holding the rendered issue-form body",
    )
    claim.add_argument(
        "--registry",
        type=Path,
        default=Path("data/registry.json"),
        help="read-only: used to refuse a base URL that is already registered and to keep the "
        "suggested id from colliding. Never written",
    )
    claim.add_argument(
        "--out", type=Path, default=None, help="proposal path (default: claim-<date>.json)"
    )
    claim.add_argument(
        "--comment-out",
        type=Path,
        default=None,
        help="also write the issue comment text here, for a workflow to post verbatim",
    )
    claim.add_argument(
        "--issue-ref",
        default="",
        help="the issue this submission came from, recorded verbatim in the proposal so the "
        "artifact says what it was generated for. A local file path is deliberately not used",
    )
    claim.add_argument(
        "--today",
        default=None,
        help=argparse.SUPPRESS,  # tests pin the date
    )
    check = sub.add_parser(
        "check",
        help="grade one endpoint from its own public documents and optionally gate a build; "
        "publishes nothing and touches no registry, history, or site",
    )
    check.add_argument(
        "base_url",
        metavar="BASE_URL",
        nargs="?",
        default="",
        help="FHIR base URL, https only. Omit it and pass --registry to check several at once",
    )
    check.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="an operator's own list of endpoints to check, in the same shape as "
        "data/registry.json minus the verification blocks. Each entry may set its own "
        "min_grade. Nothing under data/ is read or written",
    )
    check.add_argument(
        "--junit",
        type=Path,
        default=None,
        help="write JUnit XML here: a testsuite per kind, a testcase per endpoint and "
        "dimension. A check this run could not make is a skipped testcase naming the vantage, "
        "never a failure",
    )
    check.add_argument(
        "--sarif",
        type=Path,
        default=None,
        help="write SARIF 2.1.0 here: one result per finding, each carrying its citation. "
        "Carries no timestamp, so two runs over the same documents produce the same bytes",
    )
    check.add_argument(
        "--offline",
        action="store_true",
        help="read fixtures instead of the network, for exercising a registry run in a test",
    )
    check.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures"),
        help="fixture directory for --offline",
    )
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
    snapshot = sub.add_parser(
        "snapshot",
        help="copy a built site's machine-readable dataset files into a dated directory with a "
        "SHA-256 manifest. Writes an artifact and nothing else: it does not tag, sign, or "
        "publish, because a release here is cut only from an SSH-signed tag",
    )
    snapshot.add_argument("site", metavar="SITE", type=Path, help="a built site directory")
    snapshot.add_argument("--out", type=Path, required=True, help="the dated snapshot directory")
    snapshot.add_argument(
        "--date",
        required=True,
        help="the date this snapshot is of, recorded in the manifest. Stated rather than taken "
        "from the clock: a snapshot of yesterday's build built today is dated by the build",
    )
    check_snapshot = sub.add_parser(
        "verify-snapshot",
        help="check a snapshot against its own manifest: every file present, at the recorded "
        "size and digest, and nothing present the manifest does not name",
    )
    check_snapshot.add_argument("snapshot", metavar="DIR", type=Path, help="a snapshot directory")

    diff = sub.add_parser(
        "diff",
        help="say what changed between two retrieved documents or two runs. Reads what it is "
        "given, requests nothing, stores nothing, and exits 0 whatever it finds unless "
        "--fail-on-regression is passed",
    )
    diff.add_argument("before", metavar="A", type=Path, help="the earlier document or run")
    diff.add_argument("after", metavar="B", type=Path, help="the later document or run")
    diff.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default text)"
    )
    diff.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 when the later side no longer has something the earlier side had: a "
        "resource, an interaction, a declared profile, or a check that used to pass. An "
        "addition never counts, and neither does a measurement that stopped being available, "
        "because treating a lost measurement as a fall would score an absence",
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
    live run must never continue one a fixture run started. A record that exists and cannot be
    read stops the run for the same reason: see :func:`fhir_scorecard.drift.load_history`.
    """
    path = _history_path(args)
    try:
        history = load_history(path)
        ensure_mode(history, offline=args.offline)
    except (OSError, ValueError) as exc:
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
    # A table rather than a chain of comparisons. A chain grows one branch per verb and
    # eventually trips the complexity gate, at which point the tempting repair is to raise the
    # threshold; a table does not grow a branch at all. Built here rather than at module scope
    # so it does not have to be defined after every handler it names.
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "recheck": lambda args: _recheck(args.candidates, args.json_out),
        "reverify": _reverify,
        "claim": _claim,
        "check": _cmd_check,
        "mcp": _cmd_mcp,
        "narrate": _cmd_narrate,
        "audit-site": _cmd_audit_site,
        "snapshot": _cmd_snapshot,
        "verify-snapshot": _cmd_verify_snapshot,
        "diff": _cmd_diff,
    }
    handler = handlers.get(args.command)
    return handler(args) if handler is not None else None


def _cmd_mcp(args: argparse.Namespace) -> int:
    # Imported here rather than at module scope: the MCP server is only reachable through this
    # verb, and importing it for every `grade` run would load a surface that run never uses.
    from fhir_scorecard.mcp import serve

    return serve(args.site, root=args.root)


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare two artifacts and print what moved.

    Exit 2 is a usage error, meaning a file that is not there. Everything else is exit 0: a diff
    is an observation, and finding changes is what it is for. ``--fail-on-regression`` is the one
    exception, and it is opt-in because it is the operator's policy rather than this tool's
    judgement.

    A pair that could not be compared exits 0 and prints why. It deliberately does **not** trip
    ``--fail-on-regression``: "I could not read this document" is not "you removed something",
    and a build that went red on it would be reporting an absence as a finding.
    """
    from fhir_scorecard.diff import diff_paths, render_json, render_text

    for path in (args.before, args.after):
        if not path.is_file():
            print(f"diff error: {path} is not a file", file=sys.stderr)
            return 2
    try:
        report = diff_paths(args.before, args.after)
    except OSError as exc:
        print(f"diff error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_json(report) if args.format == "json" else render_text(report))
    if args.fail_on_regression and report.regressions:
        return 1
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Write one dated snapshot. Exit 2 for a usage error, never a partial artifact."""
    if not args.site.is_dir():
        print(f"snapshot error: {args.site} is not a directory", file=sys.stderr)
        return 2
    try:
        manifest = build_snapshot(args.site, args.out, args.date)
    except (FileExistsError, ValueError, OSError) as exc:
        print(f"snapshot error: {exc}", file=sys.stderr)
        return 2
    print(f"snapshot {args.date}: {len(manifest.files)} files under {args.out}")
    if manifest.missing:
        print(f"  not in this build, recorded in the manifest: {', '.join(manifest.missing)}")
    return 0


def _cmd_verify_snapshot(args: argparse.Namespace) -> int:
    """Check a snapshot against its manifest. Exit 1 if it disagrees, 2 if unreadable."""
    if not args.snapshot.is_dir():
        print(f"verify error: {args.snapshot} is not a directory", file=sys.stderr)
        return 2
    mismatches = verify_snapshot(args.snapshot)
    for mismatch in mismatches:
        print(mismatch)
    if mismatches:
        print(f"{len(mismatches)} snapshot mismatch(es)", file=sys.stderr)
        return 1
    print(f"snapshot verifies against {MANIFEST_NAME}")
    return 0


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
    # History is saved before the site is written, and stays there. The observation is a fact
    # about whether the endpoint answered, which is true whether or not this run manages to
    # render a page from it, and `_record_observation` replaces any existing row for the same
    # date, so a re-run after a failed write does not double-count the day.
    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    try:
        save_history(history_path, history)
        if args.probes_out is not None:
            write_probes(args.probes_out, args.vantage, probes_seen)

        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "scorecards.json").write_text(
            to_json(scorecards, generated_at=generated_at, vantage=run_vantage), encoding="utf-8"
        )
        _write_site(
            scorecards,
            endpoints,
            args.out,
            args.origin,
            generated_at,
            cohorts,
            history,
            cohorts_path,
        )
        write_dataset(
            args.out,
            scorecards,
            endpoints,
            origin=args.origin.rstrip("/"),
            generated_at=generated_at,
            vantage=run_vantage,
        )
    except OSError as exc:
        # Exit 2, not 1. `docs/ci-action.md` reserves 1 for "a threshold the caller set was not
        # met", so a full disk or an unwritable --out surfacing as 1 tells a CI consumer that a
        # graded endpoint failed its gate. It did not; the tool did.
        print(f"write error: {exc}", file=sys.stderr)
        return 2

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


#: The national frame the coverage tracker measures against, found beside the cohort directory.
FRAME_CSV_NAME = "qhp-landscape-py2026-individual-medical.csv"


def _coverage_page(
    cohorts_dir: Path | None,
    cohorts: tuple[Cohort, ...],
    endpoints: list[Endpoint],
    origin: str,
) -> Page | None:
    """The coverage tracker, or None when this build has no frame to track coverage against.

    Absent rather than empty on purpose. A coverage page whose denominator is missing would
    report zero organizations in every population, which reads as a measured result and is not
    one. An offline fixture build has no frame; the published build does.
    """
    if cohorts_dir is None or not cohorts:
        return None
    frame_csv = cohorts_dir.parent / "frames" / FRAME_CSV_NAME
    if not frame_csv.is_file():
        return None
    orgs = classify(
        read_frame(frame_csv), cohorts, endpoints, read_reviewed_rows_by_cohort(cohorts_dir)
    )
    return coverage_page(orgs, origin) if orgs else None


def _write_site(
    scorecards: list[Scorecard],
    endpoints: list[Endpoint],
    out: Path,
    origin: str,
    generated_at: str,
    cohorts: tuple[Cohort, ...] = (),
    history: dict[str, Any] | None = None,
    cohorts_dir: Path | None = None,
) -> None:
    """One indexable page per endpoint, organization, category, cohort and observation
    record, plus the sitemap and the machine-readable copies of each."""
    origin = origin.rstrip("/")
    by_id = {e.endpoint_id: e for e in endpoints}
    coverage = _coverage_page(cohorts_dir, cohorts, endpoints, origin)
    pages = [
        home_page(scorecards, origin, cohorts, coverage_link=coverage is not None),
        how_we_grade_page(origin),
        claim_page(origin),
    ]
    archive = records(history or {}, scorecards)
    pages.append(index_page(archive, origin, mode_of(history or {})))
    pages.append(availability_page(archive, origin))
    pages.append(over_time_page(archive, origin))
    if coverage is not None:
        pages.append(coverage)
    pages.extend(record_page(record, origin) for record in archive)
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

    # Two pages resolving to one file is a silent data loss, not a layout quirk: `write_page`
    # resolves `path=""` to `out_dir` itself, so a page added with an empty path overwrites the
    # home page -- which is exactly how `report.render_html`'s output was destroyed on every run
    # for the life of the site. Whichever page is written second wins, and nothing says so.
    collisions = sorted(path for path, n in Counter(page.path for page in pages).items() if n > 1)
    if collisions:
        raise ValueError(
            "two pages target the same output file, so one would silently overwrite the other: "
            + ", ".join(repr(path or "<site root>") for path in collisions)
        )
    for page in pages:
        write_page(out, page, origin, generated_at)
    badge_dir = out / "badge"
    badge_dir.mkdir(parents=True, exist_ok=True)
    for card in scorecards:
        (badge_dir / f"{card.endpoint_id}.svg").write_text(status_badge(card), encoding="utf-8")
    archive_dir = out / "api" / ARCHIVE_PATH
    archive_dir.mkdir(parents=True, exist_ok=True)
    for record in archive:
        (archive_dir / f"{record.endpoint_id}.json").write_text(
            history_json(record, generated_at), encoding="utf-8"
        )
    (out / "sitemap.xml").write_text(sitemap(pages, origin), encoding="utf-8")
    (out / "robots.txt").write_text(robots(origin), encoding="utf-8")
    write_assets(out)


if __name__ == "__main__":
    raise SystemExit(main())
