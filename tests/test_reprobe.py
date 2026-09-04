from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import good_capability

from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.reprobe import Candidate, ReprobeResult, format_report, load_candidates, reprobe


def _write(tmp_path: Path, rejected: list[dict]) -> Path:
    path = tmp_path / "rejected.json"
    path.write_text(json.dumps({"rejected": rejected}))
    return path


def test_shipped_rejected_file_loads() -> None:
    """Count is not asserted: entries leave this file when a re-probe promotes them into the
    registry, which is the point of re-probing."""
    cands = load_candidates(Path(__file__).parent.parent / "data" / "rejected.json")
    assert cands
    assert all(c.base_url.startswith("https://") for c in cands)
    registry = json.loads((Path(__file__).parent.parent / "data" / "registry.json").read_text())
    registered = {e["base_url"].rstrip("/") for e in registry["endpoints"]}
    # A candidate cannot be both rejected and registered.
    assert not registered & {c.base_url for c in cands}


def test_missing_fields_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="id and base_url"):
        load_candidates(_write(tmp_path, [{"id": "x"}]))
    with pytest.raises(ValueError, match="duplicate"):
        load_candidates(
            _write(
                tmp_path,
                [
                    {"id": "x", "base_url": "https://a.test"},
                    {"id": "x", "base_url": "https://b.test"},
                ],
            )
        )
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps([1, 2]))
    with pytest.raises(ValueError, match="rejected"):
        load_candidates(bad)


def _cand() -> Candidate:
    return Candidate(
        candidate_id="x", name="X", base_url="https://x.test/r4", last_outcome="HTTP 404"
    )


def test_still_failing_reported_as_such(monkeypatch) -> None:
    monkeypatch.setattr(
        "fhir_scorecard.reprobe.fetch_json",
        lambda url, **kw: FetchResult(
            url=url, ok=False, status=404, elapsed_ms=1, body=b"", error="HTTP 404"
        ),
    )
    result = reprobe(_cand())
    assert not result.now_answers and "404" in result.detail


def test_answering_but_not_fhir_is_not_a_revival(monkeypatch) -> None:
    monkeypatch.setattr(
        "fhir_scorecard.reprobe.fetch_json",
        lambda url, **kw: FetchResult(
            url=url, ok=True, status=200, elapsed_ms=1, body=b"<html>hi</html>", error=None
        ),
    )
    result = reprobe(_cand())
    assert not result.now_answers and "not a CapabilityStatement" in result.detail


def test_revival_detected(monkeypatch) -> None:
    body = json.dumps(good_capability()).encode()
    monkeypatch.setattr(
        "fhir_scorecard.reprobe.fetch_json",
        lambda url, **kw: FetchResult(
            url=url, ok=True, status=200, elapsed_ms=1, body=body, error=None
        ),
    )
    result = reprobe(_cand())
    assert result.now_answers and "4.0.1" in result.detail


def test_report_requires_human_confirmation_before_promotion() -> None:
    revived = ReprobeResult(
        candidate=_cand(), now_answers=True, detail="CapabilityStatement present"
    )
    text = format_report([revived])
    assert "NOW ANSWERS" in text
    assert "answering is not the same as verified" in text
    # No revivals: no promotion language at all.
    dead = ReprobeResult(candidate=_cand(), now_answers=False, detail="HTTP 404")
    assert "not the same as verified" not in format_report([dead])


def test_a_servers_own_string_cannot_restructure_the_report() -> None:
    """`format_report` is pasted into a GitHub issue inside a fenced block.

    `software.name` is free text on a third-party server, so a newline plus a fence escapes the
    block and injects arbitrary Markdown into this repository's issues. Control characters and
    backticks are removed and the value is capped.
    """
    from fhir_scorecard.reprobe import _MAX_QUOTED, _safe

    assert "\n" not in _safe("evil\nstill rejected] planted")
    assert "`" not in _safe("```\nmalicious")
    assert len(_safe("a" * 500)) <= _MAX_QUOTED + 3
    assert _safe(None) == ""
    assert _safe("Epic 11.4") == "Epic 11.4", "an ordinary value survives unchanged"


def test_the_revival_decision_reads_a_field_not_the_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workflow used to `grep -q "NOW ANSWERS" report.txt`, which put a payer's
    `software.name` in charge of whether this repository opens an issue."""
    import json

    from fhir_scorecard import reprobe as reprobe_module
    from fhir_scorecard.cli import main
    from fhir_scorecard.fetch import FetchResult

    # The suite never touches the network (see tests/conftest.py).
    monkeypatch.setattr(
        reprobe_module,
        "fetch_json",
        lambda url, timeout=20: FetchResult(
            url=url, ok=False, status=None, elapsed_ms=0, body=b"", error="connection timed out"
        ),
    )

    candidates = tmp_path / "rejected.json"
    candidates.write_text(
        json.dumps(
            {
                "rejected": [
                    {"id": "example", "name": "Example", "base_url": "https://198.51.100.7/r4"}
                ]
            }
        )
    )
    out = tmp_path / "recheck.json"
    assert main(["recheck", "--candidates", str(candidates), "--json-out", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["checked"] == 1
    assert result["revived"] == 0
    assert result["candidates"] == [{"id": "example", "now_answers": False}]
