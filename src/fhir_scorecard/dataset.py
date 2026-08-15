"""Dataset exports: flat CSV, per-endpoint JSON, and a schema, so the data is reusable.

The site is one way to read this; the dataset is the other. A researcher citing availability or
an engineer picking an endpoint should not have to scrape HTML, so every graded fact is
published in a flat, documented, stable shape alongside the pages.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fhir_scorecard.grading import Scorecard
from fhir_scorecard.registry import Endpoint

SCHEMA_VERSION = 1

_COLUMNS = [
    ("endpoint_id", "Stable identifier for this endpoint within the registry"),
    ("name", "Human-readable name of the endpoint as published"),
    ("kind", "Category: payer, payer_provider_directory, provider, ehr, or reference. "
             "Grades are comparable within a kind only"),
    ("base_url", "FHIR base URL; the CapabilityStatement is at <base_url>/metadata"),
    ("grade", "Letter grade A-F for an endpoint whose documents were retrieved and graded, or "
              "the literal 'not observed' when no vantage retrieved them on this run. 'not "
              "observed' is a statement about the run, not about the endpoint; F is a statement "
              "about the endpoint"),
    ("reachable", "Whether /metadata answered on this run, from any vantage"),
    ("reachability_score", "0-100 for the reachability dimension"),
    ("transparency_score", "0-100 for the capability transparency dimension, empty when no "
                           "CapabilityStatement was retrieved on this run"),
    ("interop_score", "0-100 for the interoperability readiness dimension, empty when no "
                      "CapabilityStatement was retrieved on this run"),
    ("expects_fhir", "FHIR release this endpoint is registered as intending to serve"),
    ("availability", "Rolling reachability across recorded runs, as published text"),
    ("observed_since", "First date this endpoint was observed"),
    ("verified_method", "How the entry was verified before entering the registry"),
    ("verified_date", "Date of that verification"),
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
        "observed_since": card.observed_since or "",
        "verified_method": endpoint.verified_method if endpoint else "",
        "verified_date": endpoint.verified_date if endpoint else "",
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
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "name": "fhir-scorecard",
        "title": "Public FHIR endpoint grades",
        "homepage": origin,
        "licenses": [{"name": "Apache-2.0",
                      "path": "https://www.apache.org/licenses/LICENSE-2.0"}],
        "description": (
            "Grades for publicly observable FHIR endpoint discovery surfaces. Derived only from "
            "unauthenticated /metadata and SMART discovery documents. Observational, not an "
            "audit or a compliance determination. Grades are comparable within a kind only."
        ),
        "resources": [{
            "name": "endpoints",
            "path": "dataset.csv",
            "format": "csv",
            "schema": {"fields": [
                {"name": name, "type": "integer" if name.endswith("_score") else "string",
                 "description": description}
                for name, description in _COLUMNS
            ]},
        }],
    }, indent=2)


def write_dataset(out: Path, cards: list[Scorecard], endpoints: list[Endpoint], *,
                  origin: str, generated_at: str, vantage: str) -> None:
    """Write dataset.csv, its schema, and a static per-endpoint JSON API."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset.csv").write_text(to_csv(cards, endpoints), encoding="utf-8")
    (out / "dataset.schema.json").write_text(schema_doc(origin), encoding="utf-8")

    by_id = {e.endpoint_id: e for e in endpoints}
    api_dir = out / "api" / "endpoint"
    api_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, object]] = []
    for card in cards:
        endpoint = by_id.get(card.endpoint_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "vantage": vantage,
            "endpoint": _row(card, endpoint),
            "dimensions": [
                {"key": d.key, "title": d.title, "score": d.score,
                 "findings": [{"code": f.code, "ok": f.ok, "points": f.points,
                               "max_points": f.max_points, "message": f.message,
                               "citation": f.citation} for f in d.findings]}
                for d in card.dimensions
            ],
            "drift_events": list(card.drift_events),
        }
        (api_dir / f"{card.endpoint_id}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        index.append({
            "endpoint_id": card.endpoint_id,
            "name": card.name,
            "kind": card.kind,
            "grade": card.grade,
            "url": f"{origin}/api/endpoint/{card.endpoint_id}.json",
            "page": f"{origin}/endpoint/{card.endpoint_id}/",
        })
    (out / "api" / "index.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "vantage": vantage,
        # Two different facts, published as two numbers so neither can stand in for the other:
        # how many endpoints the registry lists and this run graded, and how many of them
        # answered a probe during it.
        "count": len(index),
        "endpoints_listed": len(index),
        "answered_on_this_run": sum(1 for card in cards if card.reachable),
        "dataset_csv": f"{origin}/dataset.csv",
        "schema": f"{origin}/dataset.schema.json",
        "endpoints": sorted(index, key=lambda e: str(e["endpoint_id"])),
    }, indent=2), encoding="utf-8")
