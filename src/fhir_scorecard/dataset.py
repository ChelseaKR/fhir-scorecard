"""Dataset exports: flat CSV, per-endpoint JSON, and a schema, so the data is reusable.

The site is one way to read this; the dataset is the other. A researcher citing availability or
an engineer picking an endpoint should not have to scrape HTML, so every graded fact is
published in a flat, documented, stable shape alongside the pages.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from fhir_scorecard.grading import Scorecard
from fhir_scorecard.matrix import API_DIR as DECLARATIONS_DIR
from fhir_scorecard.matrix import SCHEMA_FILE as DECLARATIONS_SCHEMA
from fhir_scorecard.registry import Endpoint

SCHEMA_VERSION = 2

_COLUMNS = [
    ("endpoint_id", "Stable identifier for this endpoint within the registry"),
    ("name", "Human-readable name of the endpoint as published"),
    (
        "kind",
        "Category: payer, payer_provider_directory, provider, ehr, or reference. "
        "Grades are comparable within a kind only",
    ),
    ("base_url", "FHIR base URL; the CapabilityStatement is at <base_url>/metadata"),
    (
        "grade",
        "Letter grade A-F for an endpoint whose documents were retrieved and graded, or "
        "the literal 'not observed' when no vantage retrieved them on this run. 'not "
        "observed' is a statement about the run, not about the endpoint; F is a statement "
        "about the endpoint",
    ),
    ("reachable", "Whether /metadata answered on this run, from any vantage"),
    (
        "reachability_score",
        "0-100 for the reachability dimension, empty when no vantage reached the endpoint on "
        "this run. It was previously 0 in that case, which read as a measured zero against the "
        "organization; whether /metadata answers 2xx is not established by a run whose vantages "
        "all sit on one network",
    ),
    (
        "transparency_score",
        "0-100 for the capability transparency dimension, empty when no "
        "CapabilityStatement was retrieved on this run",
    ),
    (
        "interop_score",
        "0-100 for the interoperability readiness dimension, empty when no "
        "CapabilityStatement was retrieved on this run",
    ),
    ("expects_fhir", "FHIR release this endpoint is registered as intending to serve"),
    ("availability", "Rolling reachability across recorded runs, as published text"),
    (
        "last_answered",
        "Most recent recorded date this endpoint answered, empty when no observation in the "
        "recorded window is a success. The window is bounded, so empty means 'not in the "
        "recorded window', never 'not ever'",
    ),
    (
        "vantages_reached",
        "How many reporting vantages reached the endpoint. Read with vantages_reporting: this "
        "project publishes both numbers and never a verdict that hides their disagreement",
    ),
    (
        "vantages_reporting",
        "How many vantages reported on this endpoint at all. Zero means nobody looked, which is "
        "a different fact from every vantage looking and none being answered",
    ),
    ("observed_since", "First date this endpoint was observed"),
    ("verified_method", "How the entry was verified before entering the registry"),
    ("verified_date", "Date of that verification"),
    (
        "verification_basis",
        "live_capability when a conformance document was retrieved and the publisher "
        "established from it; publisher_documented when the organization publishes this "
        "base URL in its own materials and the document was not retrievable on that date",
    ),
    (
        "reverified_date",
        "Date this entry was last re-checked against the live endpoint, empty when it "
        "has not been re-checked since it was curated. Empty is not 'today'",
    ),
    (
        "failure_kinds",
        "Why no vantage reached this endpoint, from the closed vocabulary "
        "authentication_required, forbidden, not_found, dns, tls, timeout, "
        "connection_refused, server_error, redirect_refused, unclassified. Empty when the "
        "endpoint was reached. Space-separated and sorted when vantages disagreed, which is "
        "published as the disagreement it is and never resolved to one. 'unclassified' means "
        "this project has no label for what happened and is never the nearest guess. This "
        "column reports a condition and makes no claim about whose choice it was",
    ),
]


def _dimension(card: Scorecard, key: str) -> int | str:
    """A dimension's score, or an empty cell when it was not observed.

    Never 0 for an absent measurement: a consumer summing this column must not be handed a zero
    that no run produced.
    """
    for dim in card.dimensions:
        if dim.key == key:
            return dim.score if dim.score is not None else ""
    return ""


def _row(card: Scorecard, endpoint: Endpoint | None) -> dict[str, object]:
    return {
        "endpoint_id": card.endpoint_id,
        "name": card.name,
        "kind": card.kind,
        "base_url": endpoint.base_url if endpoint else "",
        "grade": card.grade,
        "reachable": "true" if card.reachable else "false",
        "reachability_score": _dimension(card, "reachability"),
        "transparency_score": _dimension(card, "transparency"),
        "interop_score": _dimension(card, "interop"),
        "expects_fhir": endpoint.expects if endpoint else "",
        "availability": card.availability,
        # Empty, not a placeholder date and not "never": the record is a bounded window and the
        # column description says so.
        "last_answered": card.last_answered or "",
        # Two numbers, always. A single "reachable" boolean is the endpoint-level claim and it is
        # correct -- one vantage reaching settles that it is up -- but on its own it hides
        # whether that was 3 of 3 or 1 of 3, and on a reachability scorecard that is the
        # interesting part.
        "vantages_reached": sum(1 for r in card.vantage_reports if r.reachable),
        "vantages_reporting": len(card.vantage_reports),
        "observed_since": card.observed_since or "",
        "verified_method": endpoint.verified_method if endpoint else "",
        "verified_date": endpoint.verified_date if endpoint else "",
        "verification_basis": endpoint.verification_basis if endpoint else "",
        "reverified_date": endpoint.reverified_date if endpoint else "",
        "failure_kinds": " ".join(card.failure_kinds),
    }


def to_csv(cards: list[Scorecard], endpoints: list[Endpoint]) -> str:
    by_id = {e.endpoint_id: e for e in endpoints}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[c for c, _ in _COLUMNS], lineterminator="\n")
    writer.writeheader()
    for card in sorted(cards, key=lambda c: c.endpoint_id):
        writer.writerow(_row(card, by_id.get(card.endpoint_id)))
    return buf.getvalue()


def schema_doc(origin: str) -> str:
    """Table Schema style description of the CSV, so a consumer knows what each column means."""
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "name": "fhir-scorecard",
            "title": "Public FHIR endpoint grades",
            "homepage": origin,
            "licenses": [
                {"name": "Apache-2.0", "path": "https://www.apache.org/licenses/LICENSE-2.0"}
            ],
            "description": (
                "Grades for publicly observable FHIR endpoint discovery surfaces. Derived only from "
                "unauthenticated /metadata and SMART discovery documents. Observational, not an "
                "audit or a compliance determination. Grades are comparable within a kind only."
            ),
            "resources": [
                {
                    "name": "endpoints",
                    "path": "dataset.csv",
                    "format": "csv",
                    "schema": {
                        "fields": [
                            {
                                "name": name,
                                "type": "integer" if name.endswith("_score") else "string",
                                "description": description,
                            }
                            for name, description in _COLUMNS
                        ]
                    },
                }
            ],
        },
        indent=2,
    )


def write_dataset(
    out: Path,
    cards: list[Scorecard],
    endpoints: list[Endpoint],
    *,
    origin: str,
    generated_at: str,
    vantage: str,
    feeds: Sequence[str] = (),
    declarations: Sequence[str] = (),
    app_to_server: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """Write dataset.csv, its schema, and a static per-endpoint JSON API.

    ``feeds`` is the site-relative path of every Atom feed the site build reported having
    written. A feed URL is published here only when its path is in that list: an index naming a
    file the build did not write is the same defect as a sitemap entry no file answers, and the
    only way to be sure is to be told what was written rather than to assume it.

    ``declarations`` is the same contract for the declared-capability files (#102): the endpoint
    ids whose ``api/capabilities/<id>.json`` the site build reported having written.
    """
    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset.csv").write_text(to_csv(cards, endpoints), encoding="utf-8")
    (out / "dataset.schema.json").write_text(schema_doc(origin), encoding="utf-8")

    by_id = {e.endpoint_id: e for e in endpoints}
    written_feeds = set(feeds)
    declared_ids = set(declarations)
    api_dir = out / "api" / "endpoint"
    api_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, object]] = []
    for card in cards:
        endpoint = by_id.get(card.endpoint_id)
        record = _row(card, endpoint)
        # The one field whose encoding differs between the two surfaces. CSV has no arrays, so
        # the row joins the kinds with a space; JSON does, so it carries them as one. Same
        # field, same order, encoded natively in each -- rather than making a JSON consumer
        # split a string on whitespace to find out whether the vantages disagreed.
        record["failure_kinds"] = list(card.failure_kinds)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "vantage": vantage,
            "endpoint": record,
            "dimensions": [
                {
                    "key": d.key,
                    "title": d.title,
                    "score": d.score,
                    # `observed` and `withheld_points` travel with `ok` and `max_points`, never
                    # apart from them. Dropping the two meant a check that was never made
                    # published as `"ok": false` -- a failing verdict about a named payer -- to
                    # every consumer of this file, while the site's own reader saw "○ Not
                    # observed" for the same finding. The HTML surface has honoured both fields
                    # since they existed and `ci_report.py` says in its docstring that "nothing
                    # here reads `ok` without reading `observed` first"; this writer was the one
                    # surface where a reader could not.
                    #
                    # Neither is recoverable from what was published. `max_points == 0` is not a
                    # proxy for `observed`: `site._finding_mark` already uses that condition for
                    # the *note* state -- "not applicable to a Provider Directory API" -- so it
                    # conflates a check nobody could make with one deliberately not scored. And
                    # without `withheld_points` a null score cannot be explained at all: a
                    # consumer cannot tell a dimension where nothing was observed from one where
                    # some checks ran and some did not, nor reconstruct the denominator.
                    "findings": [
                        {
                            "code": f.code,
                            "ok": f.ok,
                            "observed": f.observed,
                            # The third state (#135 follow-up). `observed: false` alone cannot
                            # separate "every vantage asked and none was answered" from "nobody
                            # asked", and only the first is information.
                            "unanswered": f.unanswered,
                            "points": f.points,
                            "max_points": f.max_points,
                            "withheld_points": f.withheld_points,
                            "message": f.message,
                            "citation": f.citation,
                        }
                        for f in d.findings
                    ],
                }
                for d in card.dimensions
            ],
            # Published rows, one per reporting vantage, never resolved to a winner. See
            # `vantage.VantageReport`: measured 2026-09-12, vantage disagreement runs in both
            # directions, so there is no vantage this project could elect without relocating the
            # misdiagnosis it exists to prevent.
            "vantages": [
                {
                    "vantage": r.vantage,
                    "network": r.network,
                    "reachable": r.reachable,
                    "status": r.status,
                    "failure_kind": r.failure_kind,
                    "elapsed_ms": r.elapsed_ms,
                    "error": r.error,
                }
                for r in card.vantage_reports
            ],
            "drift_events": list(card.drift_events),
            # Kept out of drift_events so a consumer counting capability changes counts changes.
            # A return to a declaration already on record is a fact about the address, not a
            # fact about the publisher shipping something.
            "drift_alternations": list(card.drift_alternations),
        }
        # The declared app-to-server block (#97), observed and never graded. Present only
        # where the build kept the facts it was built from.
        if app_to_server is not None and card.endpoint_id in app_to_server:
            payload["app_to_server"] = dict(app_to_server[card.endpoint_id])
        (api_dir / f"{card.endpoint_id}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        entry: dict[str, object] = {
            "endpoint_id": card.endpoint_id,
            "name": card.name,
            "kind": card.kind,
            "grade": card.grade,
            "url": f"{origin}/api/endpoint/{card.endpoint_id}.json",
            "page": f"{origin}/endpoint/{card.endpoint_id}/",
        }
        feed_path = f"endpoint/{card.endpoint_id}/feed.xml"
        if feed_path in written_feeds:
            entry["feed"] = f"{origin}/{feed_path}"
        if card.endpoint_id in declared_ids:
            entry["capabilities"] = f"{origin}/{DECLARATIONS_DIR}/{card.endpoint_id}.json"
        index.append(entry)
    site_feed = {"feed": f"{origin}/feed.xml"} if "feed.xml" in written_feeds else {}
    if declared_ids:
        site_feed["capabilities_schema"] = f"{origin}/{DECLARATIONS_SCHEMA}"
    (out / "api" / "index.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": generated_at,
                "vantage": vantage,
                **site_feed,
                # Two different facts, published as two numbers so neither can stand in for the other:
                # how many endpoints the registry lists and this run graded, and how many of them
                # answered a probe during it.
                "count": len(index),
                "endpoints_listed": len(index),
                "answered_on_this_run": sum(1 for card in cards if card.reachable),
                "dataset_csv": f"{origin}/dataset.csv",
                "schema": f"{origin}/dataset.schema.json",
                "endpoints": sorted(index, key=lambda e: str(e["endpoint_id"])),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
