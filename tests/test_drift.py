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
    """Changes are summarized; the history never stores or replays every profile URL."""
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-04")
    doc = good_capability()
    rest = doc["rest"][0]["resource"]  # type: ignore[index]
    rest[0]["supportedProfile"] = ["http://example.test/a", "http://example.test/b"]
    result = observe(history, "x", parse_capability(json.dumps(doc).encode()), "2026-08-05")
    assert any("declared profiles" in c for c in result.changes)
    assert not any("http" in c for c in result.changes)


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


def test_availability_accumulates_and_is_gated_until_enough_data() -> None:
    from fhir_scorecard.drift import MIN_OBSERVATIONS_TO_REPORT
    history: dict[str, Any] = {}
    for day in range(1, 5):
        r = observe(history, "x", _facts(), f"2026-08-{day:02d}")
    assert r.availability.observations == 4
    assert r.availability.reachable == 4
    assert not r.availability.reportable
    assert "reported once" in r.availability.summary()

    for day in range(5, MIN_OBSERVATIONS_TO_REPORT + 5):
        r = observe(history, "x", _facts(), f"2026-08-{day:02d}")
    assert r.availability.reportable
    assert "100%" in r.availability.summary()


def test_outage_counts_against_availability() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-01")
    r = observe(history, "x", parse_capability(b"<html>502</html>"), "2026-08-02")
    assert r.availability.observations == 2
    assert r.availability.reachable == 1


def test_rerunning_the_same_day_does_not_double_count() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-01")
    r = observe(history, "x", _facts(), "2026-08-01")
    assert r.availability.observations == 1


def test_observation_window_is_bounded() -> None:
    history: dict[str, Any] = {}
    for i in range(200):
        observe(history, "x", _facts(), f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}")
    assert len(history["x"]["observations"]) <= 120


def test_fingerprint_stores_digest_not_every_profile() -> None:
    from fhir_scorecard.drift import fingerprint
    fp = fingerprint(_facts())
    assert "supported_profiles" not in fp
    assert fp["profile_count"] == 6
    assert isinstance(fp["profile_digest"], str) and len(fp["profile_digest"]) == 16


def test_profile_swap_detected_even_when_count_is_equal() -> None:
    history: dict[str, Any] = {}
    observe(history, "x", _facts(), "2026-08-01")
    doc = good_capability()
    for i, r in enumerate(doc["rest"][0]["resource"]):  # type: ignore[index]
        r["supportedProfile"] = [f"http://example.test/other-{i}"]
    r2 = observe(history, "x", parse_capability(json.dumps(doc).encode()), "2026-08-02")
    assert any("profiles changed" in c for c in r2.changes)


def test_legacy_profile_list_migrates_without_false_drift() -> None:
    history: dict[str, Any] = {"x": {
        "first_seen": "2026-07-01", "last_seen": "2026-07-01",
        "fingerprint": {
            "fhir_version": "4.0.1", "software_name": "SyntheticServer",
            "software_version": "9.9.9", "resource_count": 6,
            "resources_with_interactions": 6, "declares_oauth_security": True,
            "supported_profiles": [
                f"http://hl7.org/fhir/us/core/StructureDefinition/us-core-{t}"
                for t in ["patient", "coverage", "explanationofbenefit",
                          "practitioner", "organization", "observation"]],
        },
        "events": [],
    }}
    r = observe(history, "x", _facts(), "2026-08-05")
    assert r.changes == ()
    assert "profile_count" in history["x"]["fingerprint"]
