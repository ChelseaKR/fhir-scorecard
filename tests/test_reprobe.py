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
    cands = load_candidates(Path(__file__).parent.parent / "data" / "rejected.json")
    assert len(cands) >= 10
    assert all(c.base_url.startswith("https://") for c in cands)


def test_missing_fields_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="id and base_url"):
        load_candidates(_write(tmp_path, [{"id": "x"}]))
    with pytest.raises(ValueError, match="duplicate"):
        load_candidates(_write(tmp_path, [
            {"id": "x", "base_url": "https://a.test"},
            {"id": "x", "base_url": "https://b.test"}]))
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps([1, 2]))
    with pytest.raises(ValueError, match="rejected"):
        load_candidates(bad)


def _cand() -> Candidate:
    return Candidate(candidate_id="x", name="X", base_url="https://x.test/r4",
                     last_outcome="HTTP 404")


def test_still_failing_reported_as_such(monkeypatch) -> None:
    monkeypatch.setattr("fhir_scorecard.reprobe.fetch_json",
                        lambda url, **kw: FetchResult(url=url, ok=False, status=404,
                                                      elapsed_ms=1, body=b"", error="HTTP 404"))
    result = reprobe(_cand())
    assert not result.now_answers and "404" in result.detail


def test_answering_but_not_fhir_is_not_a_revival(monkeypatch) -> None:
    monkeypatch.setattr("fhir_scorecard.reprobe.fetch_json",
                        lambda url, **kw: FetchResult(url=url, ok=True, status=200, elapsed_ms=1,
                                                      body=b"<html>hi</html>", error=None))
    result = reprobe(_cand())
    assert not result.now_answers and "not a CapabilityStatement" in result.detail


def test_revival_detected(monkeypatch) -> None:
    body = json.dumps(good_capability()).encode()
    monkeypatch.setattr("fhir_scorecard.reprobe.fetch_json",
                        lambda url, **kw: FetchResult(url=url, ok=True, status=200, elapsed_ms=1,
                                                      body=body, error=None))
    result = reprobe(_cand())
    assert result.now_answers and "4.0.1" in result.detail


def test_report_requires_human_confirmation_before_promotion() -> None:
    revived = ReprobeResult(candidate=_cand(), now_answers=True,
                            detail="CapabilityStatement present")
    text = format_report([revived])
    assert "NOW ANSWERS" in text
    assert "answering is not the same as verified" in text
    # No revivals: no promotion language at all.
    dead = ReprobeResult(candidate=_cand(), now_answers=False, detail="HTTP 404")
    assert "not the same as verified" not in format_report([dead])
