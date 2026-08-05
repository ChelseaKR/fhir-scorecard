from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import good_capability

from fhir_scorecard.capability import parse_capability
from fhir_scorecard.drift import load_history, observe, save_history


def _facts(**overrides: Any):
    doc = good_capability()
    doc.update(overrides)
    return parse_capability(json.dumps(doc).encode())


def test_first_observation_sets_first_seen() -> None:
    history: dict[str, Any] = {}
    result = observe(history, "x", _facts(), "2026-08-04")
    assert result.first_seen == "2026-08-04"
    assert result.changes == ()
    assert history["x"]["first_seen"] == "2026-08-04"


def test_unchanged_run_records_no_event() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    result = observe(history, "x", _facts(), "2026-08-05")
    assert result.changes == ()
    assert result.recorded_events == ()
    assert history["x"]["first_seen"] == "2026-08-04"
    assert history["x"]["last_seen"] == "2026-08-05"


def test_software_change_recorded_with_message() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    changed = _facts(software={"name": "SyntheticServer", "version": "10.0.0"})
    result = observe(history, "x", changed, "2026-08-06")
    assert any("software_version" in c and "10.0.0" in c for c in result.changes)
    assert result.recorded_events and result.recorded_events[0].startswith("2026-08-06")


def test_profile_changes_summarized_not_enumerated() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    doc = good_capability()
    rest = doc["rest"][0]["resource"]  # type: ignore[index]
    rest[0]["supportedProfile"] = ["http://example.test/new-profile"]
    result = observe(history, "x", parse_capability(json.dumps(doc).encode()), "2026-08-05")
    assert any("profiles added" in c or "profiles removed" in c for c in result.changes)


def test_outage_does_not_read_as_drift() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    result = observe(history, "x", parse_capability(b"<html>502</html>"), "2026-08-05")
    assert result.changes == ()
    assert result.first_seen == "2026-08-04"
    # The stored fingerprint survives the outage untouched.
    assert history["x"]["last_seen"] == "2026-08-04"


def test_events_bounded() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-01-01")
    for i in range(30):
        observe(history, "x",
                _facts(software={"name": "S", "version": str(i)}), f"2026-02-{i % 28 + 1:02d}")
    assert len(history["x"]["events"]) <= 20


def test_history_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    save_history(path, history)
    loaded = load_history(path)
    assert loaded["x"]["first_seen"] == "2026-08-04"


def test_corrupt_history_fails_open_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not json")
    assert load_history(path) == {}
    assert load_history(tmp_path / "missing.json") == {}
