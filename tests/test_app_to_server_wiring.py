"""The declared app-to-server block reaches every surface #97 names, and no surface it does not.

``tests/test_backend.py`` holds the answers to the documents. This file holds the wiring to the
places a reader meets them: the ``check`` command's terminal report and result JSON, the site's
endpoint page and per-endpoint API file, and the composite Action's job summary. It also pins the
two places the block must *not* appear: inside a graded card, and anywhere in the published
``scorecards.json``, whose shape nothing in #97 decided to change.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import good_capability

from fhir_scorecard import cli
from fhir_scorecard.backend import ANSWERS, FIELD_ABSENT, NOT_RETRIEVED, QUESTIONS
from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.report import to_json

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
BASE = "https://fhir.example.test/r4"
SMART_WITHOUT_AUTH_METHODS = {
    "authorization_endpoint": "https://fhir.example.test/authorize",
    "token_endpoint": "https://fhir.example.test/token",
    "grant_types_supported": ["authorization_code"],
}


def _ok(url: str, body: dict[str, object]) -> FetchResult:
    return FetchResult(
        url=url, ok=True, status=200, elapsed_ms=40, body=json.dumps(body).encode(), error=None
    )


def _serve(monkeypatch: pytest.MonkeyPatch, smart: dict[str, object] | None) -> None:
    """A readable CapabilityStatement, and a SMART document or a 404, with no network."""

    def fake(url: str, **_: Any) -> FetchResult:
        if url.endswith("/metadata"):
            return _ok(url, good_capability())
        if smart is None:
            return FetchResult(
                url=url, ok=False, status=404, elapsed_ms=40, body=b"", error="HTTP 404"
            )
        return _ok(url, smart)

    monkeypatch.setattr("fhir_scorecard.cli.fetch_json", fake)


def _rows(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {row["key"]: row for row in block["answers"]}
    assert list(rows) == [question.key for question in QUESTIONS]
    return rows


# --- the check command ---


def test_check_prints_the_block_and_records_it_beside_the_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(monkeypatch, SMART_WITHOUT_AUTH_METHODS)
    out = tmp_path / "result.json"
    assert cli.main(["check", BASE, "--json-out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "declared app-to-server access (observed, not graded):" in printed
    assert "private_key_jwt: not declared: the field is absent" in printed
    result = json.loads(out.read_text(encoding="utf-8"))
    (card,) = result["scorecards"]
    assert "app_to_server" not in card, "a graded card must not carry an ungraded block"
    rows = _rows(result["app_to_server"][card["endpoint_id"]])
    assert rows["private_key_jwt"]["answer"] == FIELD_ABSENT
    assert all(row["answer_text"] == ANSWERS[row["answer"]] for row in rows.values())


def test_a_smart_document_the_check_was_not_served_is_not_called_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, None)
    out = tmp_path / "result.json"
    assert cli.main(["check", BASE, "--json-out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    rows = _rows(result["app_to_server"][result["scorecards"][0]["endpoint_id"]])
    assert rows["private_key_jwt"]["answer"] == NOT_RETRIEVED
    assert "requested and not served" in rows["private_key_jwt"]["detail"]


# --- the site build ---


def _build(out: Path) -> Path:
    argv = [
        "grade",
        "--offline",
        "--fixtures",
        str(FIXTURES),
        "--registry",
        str(FIXTURES / "registry.json"),
        "--out",
        str(out),
        "--history",
        str(out.parent / "history.json"),
    ]
    assert cli.main(argv) == 0
    return out


def test_the_site_carries_the_block_on_the_page_and_in_the_endpoint_file(tmp_path: Path) -> None:
    built = _build(tmp_path / "site")
    page = (built / "endpoint" / "cms-blue-button-2" / "index.html").read_text(encoding="utf-8")
    assert "<h2>Declared app-to-server access</h2>" in page
    payer = json.loads(
        (built / "api" / "endpoint" / "cms-blue-button-2.json").read_text(encoding="utf-8")
    )
    assert _rows(payer["app_to_server"])["private_key_jwt"]["answer"] == FIELD_ABSENT


def test_a_fixture_with_no_smart_document_is_requested_and_not_served(tmp_path: Path) -> None:
    """oracle-health-open has no smart.json, so the offline fetch fails: the build's own
    not-served path, not the probe path."""
    built = _build(tmp_path / "site")
    oracle = json.loads(
        (built / "api" / "endpoint" / "oracle-health-open.json").read_text(encoding="utf-8")
    )
    rows = _rows(oracle["app_to_server"])
    assert rows["private_key_jwt"]["answer"] == NOT_RETRIEVED
    assert "requested and not served" in rows["private_key_jwt"]["detail"]


def test_the_published_scorecards_file_does_not_change_shape(tmp_path: Path) -> None:
    built = _build(tmp_path / "site")
    published = json.loads((built / "scorecards.json").read_text(encoding="utf-8"))
    assert "app_to_server" not in published
    assert published["scorecards"]
    assert all("app_to_server" not in card for card in published["scorecards"])


def test_a_card_whose_facts_were_not_kept_stops_the_build() -> None:
    card = build_scorecard(
        "alpha",
        "Alpha",
        FetchResult(
            url="https://a.test/metadata", ok=True, status=200, elapsed_ms=1, body=b"", error=None
        ),
        parse_capability(b"{}"),
        parse_smart(b"{}"),
        kind="payer",
    )
    with pytest.raises(ValueError, match="no SMART or CapabilityStatement facts were kept"):
        cli._app_to_server_blocks([card], {}, {})


def test_extra_result_fields_may_never_overwrite_one_every_result_carries() -> None:
    with pytest.raises(ValueError, match="would overwrite"):
        to_json([], generated_at="g", extra={"scorecards": []})
    assert "app_to_server" not in json.loads(to_json([], generated_at="g"))


# --- the composite Action's job summary ---


def _renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "render_result", ROOT / "action" / "render_result.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(block: dict[str, Any] | None) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "generated_at": "2026-09-11 00:00 UTC",
        "vantage": "github-actions/Linux",
        "scorecards": [
            {
                "endpoint_id": "endpoint-test",
                "name": "endpoint.test",
                "grade": "B",
                "dimensions": [],
            }
        ],
    }
    if block is not None:
        artifact["app_to_server"] = {"endpoint-test": block}
    return artifact


def test_the_action_summary_renders_the_block_as_written() -> None:
    block = {
        "answers": [
            {
                "asks": "Declares private_key_jwt client authentication at the token endpoint",
                "answer": FIELD_ABSENT,
                "answer_text": ANSWERS[FIELD_ABSENT],
                "detail": "a detail | with a pipe",
            }
        ]
    }
    summary = _renderer().build_summary(_artifact(block), True)
    assert "**Declared app-to-server access** (observed, not graded)" in summary
    assert "not declared: the field is absent: a detail \\| with a pipe |" in summary


def test_the_action_summary_renders_nothing_for_a_result_without_the_block() -> None:
    summary = _renderer().build_summary(_artifact(None), True)
    assert "app-to-server" not in summary
    assert "endpoint.test: grade B" in summary
