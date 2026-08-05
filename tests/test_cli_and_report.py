from __future__ import annotations

import json
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.report import render_html, to_json


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"endpoints": [
        {"id": "alpha", "name": "Alpha Reference", "kind": "reference",
         "base_url": "https://alpha.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-04"}},
        {"id": "beta-dark", "name": "Beta (no fixture)", "kind": "payer",
         "base_url": "https://beta.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-04"}},
    ]}))
    return path


def test_cli_offline_end_to_end(tmp_path: Path, capsys: object) -> None:
    fixtures = tmp_path / "fixtures" / "alpha"
    fixtures.mkdir(parents=True)
    (fixtures / "metadata.json").write_text(json.dumps(good_capability()))
    (fixtures / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"

    code = main(["grade", "--registry", str(_registry(tmp_path)), "--offline",
                 "--fixtures", str(tmp_path / "fixtures"), "--out", str(out)])
    assert code == 0

    payload = json.loads((out / "scorecards.json").read_text())
    grades = {s["endpoint_id"]: s["grade"] for s in payload["scorecards"]}
    assert grades["alpha"] == "A"
    assert grades["beta-dark"] == "F"  # missing fixture = unreachable = fail closed
    assert "disclaimer" in payload

    html_out = (out / "index.html").read_text()
    assert "Alpha Reference" in html_out and "lang=\"en\"" in html_out


def test_cli_offline_requires_fixtures(tmp_path: Path) -> None:
    assert main(["grade", "--registry", str(_registry(tmp_path)), "--offline"]) == 2


def test_cli_bad_registry_is_error(tmp_path: Path) -> None:
    bad = tmp_path / "registry.json"
    bad.write_text(json.dumps({"endpoints": [{"id": "x"}]}))
    assert main(["grade", "--registry", str(bad), "--offline",
                 "--fixtures", str(tmp_path)]) == 2


def test_report_escapes_html() -> None:
    card = build_scorecard(
        "evil", "<script>alert(1)</script>",
        FetchResult(url="https://x.test/metadata", ok=True, status=200, elapsed_ms=10,
                    body=b"", error=None),
        parse_capability(b""), parse_smart(b""))
    html_out = render_html([card], generated_at="2026-08-04")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_json_report_round_trips() -> None:
    card = build_scorecard(
        "x", "X",
        FetchResult(url="https://x.test/metadata", ok=False, status=None, elapsed_ms=0,
                    body=b"", error="URLError"),
        parse_capability(b""), parse_smart(b""))
    payload = json.loads(to_json([card], generated_at="2026-08-04"))
    assert payload["scorecards"][0]["grade"] == "F"
    assert payload["generator"] == "fhir-scorecard"
