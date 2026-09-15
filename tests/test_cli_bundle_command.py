"""Tests for the `fhir-scorecard bundle` CLI command and the plan.json loader wired into the
site build (_load_bundle_plan, cli.py)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from fhir_scorecard.cli import _load_bundle_plan, main
from fhir_scorecard.grading import DimensionScore, Finding, Scorecard


def _write_registry(path: Path, *, enabled: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": "acme-payer",
                        "name": "Acme Payer",
                        "kind": "payer",
                        "base_url": "https://acme.test/fhir",
                        "enabled": enabled,
                        "verification": {"method": "live_capability", "date": "2026-08-01"},
                    }
                ]
            }
        )
    )


def _write_scorecards(path: Path) -> None:
    card = Scorecard(
        endpoint_id="acme-payer",
        name="Acme Payer",
        grade="A",
        reachable=True,
        dimensions=(
            DimensionScore(
                key="reachability",
                title="Reachability",
                score=100,
                findings=(
                    Finding(
                        code="R1",
                        ok=True,
                        points=60,
                        max_points=60,
                        message="ok",
                        citation="https://hl7.org/fhir/R4/http.html",
                    ),
                ),
            ),
        ),
        kind="payer",
    )
    path.write_text(
        json.dumps(
            {
                "generator": "fhir-scorecard",
                "generated_at": "2026-09-15T00:00:00Z",
                "scorecards": [dataclasses.asdict(card)],
            }
        )
    )


def _write_request(path: Path, **overrides: object) -> None:
    raw = {
        "bundle_id": "b" * 32,
        "program_name": "Acme Compliance Partners",
        "accent": "#123456",
        "logo": "",
        "endpoint_ids": "acme-payer",
        "deliver_to": "buyer@example.org",
        "cadence": "one_time",
    }
    raw.update(overrides)
    path.write_text(json.dumps(raw))


def test_cmd_bundle_end_to_end_via_main(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "registry.json"
    scorecards = tmp_path / "scorecards.json"
    request = tmp_path / "request.json"
    out_zip = tmp_path / "out.zip"
    plan_out = tmp_path / "plan-out.json"
    _write_registry(registry)
    _write_scorecards(scorecards)
    _write_request(request)

    code = main(
        [
            "bundle",
            "--request",
            str(request),
            "--registry",
            str(registry),
            "--scorecards",
            str(scorecards),
            "--out",
            str(out_zip),
            "--plan-out",
            str(plan_out),
        ]
    )
    assert code == 0
    assert out_zip.exists()
    manifest = json.loads(plan_out.read_text())
    assert manifest["included"] == 1
    out = capsys.readouterr().out
    assert "1 of 1" in out


def test_cmd_bundle_reports_a_request_parse_error(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "registry.json"
    scorecards = tmp_path / "scorecards.json"
    request = tmp_path / "request.json"
    _write_registry(registry)
    _write_scorecards(scorecards)
    _write_request(request, deliver_to="not-an-email")

    code = main(
        [
            "bundle",
            "--request",
            str(request),
            "--registry",
            str(registry),
            "--scorecards",
            str(scorecards),
            "--out",
            str(tmp_path / "out.zip"),
        ]
    )
    assert code == 2
    assert "deliver_to must be" in capsys.readouterr().err


def test_cmd_bundle_reports_an_unreadable_request_file(tmp_path: Path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("not json")
    code = main(
        [
            "bundle",
            "--request",
            str(request),
            "--out",
            str(tmp_path / "out.zip"),
        ]
    )
    assert code == 2
    assert "request error" in capsys.readouterr().err


def test_cmd_bundle_refuses_a_request_that_is_not_a_json_object(tmp_path: Path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("[1, 2, 3]")
    code = main(["bundle", "--request", str(request), "--out", str(tmp_path / "out.zip")])
    assert code == 2
    assert "must hold a JSON object" in capsys.readouterr().err


def test_cmd_bundle_reports_an_unreadable_registry(tmp_path: Path, capsys) -> None:
    request = tmp_path / "request.json"
    _write_request(request)
    code = main(
        [
            "bundle",
            "--request",
            str(request),
            "--registry",
            str(tmp_path / "does-not-exist.json"),
            "--scorecards",
            str(tmp_path / "also-missing.json"),
            "--out",
            str(tmp_path / "out.zip"),
        ]
    )
    assert code == 2
    assert "bundle error" in capsys.readouterr().err


def test_cmd_bundle_respects_max_endpoints(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "registry.json"
    scorecards = tmp_path / "scorecards.json"
    request = tmp_path / "request.json"
    _write_registry(registry)
    _write_scorecards(scorecards)
    _write_request(request, endpoint_ids="one,two,three")

    code = main(
        [
            "bundle",
            "--request",
            str(request),
            "--registry",
            str(registry),
            "--scorecards",
            str(scorecards),
            "--out",
            str(tmp_path / "out.zip"),
            "--max-endpoints",
            "2",
        ]
    )
    assert code == 2
    assert "your plan covers at most 2" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _load_bundle_plan
# ---------------------------------------------------------------------------


def test_load_bundle_plan_reads_the_real_committed_file() -> None:
    """The file this function reads by default is a real repository file, checked in at
    data/bundle/plan.json; this asserts it parses and shapes correctly from the repo root."""
    import os

    repo_root = Path(__file__).resolve().parents[1]
    previous = Path.cwd()
    os.chdir(repo_root)
    try:
        plan = _load_bundle_plan()
    finally:
        os.chdir(previous)
    assert plan["paymentsAvailable"] is False
    assert "bundle_15" in plan["products"]


def test_load_bundle_plan_falls_back_when_the_file_is_absent() -> None:
    # _isolated_cwd (conftest.py) already puts every test in a throwaway directory with no
    # data/bundle/plan.json, so this is the default case for every OTHER test in the suite too.
    plan = _load_bundle_plan()
    assert plan["paymentsAvailable"] is False
    assert plan["products"] == {}


def test_load_bundle_plan_falls_back_on_unreadable_json(tmp_path: Path) -> None:
    (tmp_path / "data" / "bundle").mkdir(parents=True)
    (tmp_path / "data" / "bundle" / "plan.json").write_text("not json")
    import os

    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        plan = _load_bundle_plan()
    finally:
        os.chdir(previous)
    assert plan["paymentsAvailable"] is False


def test_load_bundle_plan_falls_back_when_json_is_not_an_object(tmp_path: Path) -> None:
    (tmp_path / "data" / "bundle").mkdir(parents=True)
    (tmp_path / "data" / "bundle" / "plan.json").write_text("[1, 2, 3]")
    import os

    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        plan = _load_bundle_plan()
    finally:
        os.chdir(previous)
    assert plan["paymentsAvailable"] is False
