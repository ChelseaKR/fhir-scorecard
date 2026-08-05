from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.dataset import schema_doc, to_csv
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.registry import Endpoint


def _card(eid: str = "acme", kind: str = "payer"):
    return build_scorecard(
        eid, "Acme Health",
        FetchResult(url="https://a.test/metadata", ok=True, status=200, elapsed_ms=10,
                    body=b"", error=None),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind=kind, availability="answered 5 of 5 checks", observed_since="2026-08-01")


def _endpoint(eid: str = "acme") -> Endpoint:
    return Endpoint(endpoint_id=eid, name="Acme Health", kind="payer",
                    base_url="https://a.test/r4", verified_method="live fetch",
                    verified_date="2026-08-05", expects="r4")


def test_csv_has_documented_columns_and_scores() -> None:
    text = to_csv([_card()], [_endpoint()])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint_id"] == "acme"
    assert row["grade"] == "A"
    assert row["reachable"] == "true"
    assert int(row["transparency_score"]) == 100
    assert row["base_url"] == "https://a.test/r4"
    assert row["verified_method"] == "live fetch"
    assert row["expects_fhir"] == "r4"


def test_csv_tolerates_a_card_with_no_registry_entry() -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv([_card()], []))))
    assert rows[0]["base_url"] == ""
    assert rows[0]["grade"] == "A"


def test_schema_documents_every_column() -> None:
    schema = json.loads(schema_doc("https://example.test"))
    fields = schema["resources"][0]["schema"]["fields"]
    header = to_csv([_card()], [_endpoint()]).splitlines()[0].split(",")
    assert [f["name"] for f in fields] == header
    assert all(f["description"] for f in fields)
    assert all(f["type"] == "integer" for f in fields if f["name"].endswith("_score"))


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"endpoints": [
        {"id": "alpha", "name": "Alpha Health Patient Access API", "kind": "payer",
         "base_url": "https://alpha.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-05"}}]}))
    return path


def test_cli_writes_dataset_and_static_api(tmp_path: Path) -> None:
    d = tmp_path / "fixtures" / "alpha"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(good_capability()))
    (d / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"
    assert main(["grade", "--registry", str(_registry(tmp_path)), "--offline",
                 "--fixtures", str(tmp_path / "fixtures"), "--out", str(out),
                 "--history", str(tmp_path / "h.json"),
                 "--origin", "https://example.test/"]) == 0

    assert (out / "dataset.csv").is_file()
    assert (out / "dataset.schema.json").is_file()

    api_index = json.loads((out / "api" / "index.json").read_text())
    assert api_index["count"] == 1
    entry = api_index["endpoints"][0]
    # Trailing slash on --origin must not produce a doubled slash in published URLs.
    assert entry["url"] == "https://example.test/api/endpoint/alpha.json"
    assert entry["page"] == "https://example.test/endpoint/alpha/"

    detail = json.loads((out / "api" / "endpoint" / "alpha.json").read_text())
    assert detail["endpoint"]["grade"] == "A"
    assert {d["key"] for d in detail["dimensions"]} == {
        "reachability", "transparency", "interop"}
    assert all(f["citation"].startswith("https://")
               for d in detail["dimensions"] for f in d["findings"])
