"""The documented offline command must work, and must not write into the real history.

Before this, `--offline --fixtures tests/fixtures` named a directory that did not exist, so every
endpoint failed to load a fixture, the run exited 0, and it wrote a `{"up": false}` observation
for all thirty named organizations into `data/history.json` on a date that had none.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhir_scorecard.cli import main
from fhir_scorecard.drift import META_KEY, endpoint_count, ensure_mode

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
FIXTURE_REGISTRY = FIXTURES / "registry.json"


def test_the_fixtures_the_readme_names_exist() -> None:
    assert FIXTURES.is_dir()
    assert FIXTURE_REGISTRY.is_file()
    ids = [e["id"] for e in json.loads(FIXTURE_REGISTRY.read_text())["endpoints"]]
    assert ids, "the fixture registry must list the endpoints it has captures for"
    for endpoint_id in ids:
        assert (FIXTURES / endpoint_id / "metadata.json").is_file(), endpoint_id


def test_the_documented_offline_command_grades_real_captured_documents(tmp_path: Path) -> None:
    out = tmp_path / "offline-site"
    assert main(["grade", "--offline", "--fixtures", str(FIXTURES),
                 "--registry", str(FIXTURE_REGISTRY), "--out", str(out),
                 "--history", str(tmp_path / "history.json")]) == 0

    cards = {c["endpoint_id"]: c for c in
             json.loads((out / "scorecards.json").read_text())["scorecards"]}
    assert set(cards) == {"cms-blue-button-2", "inferno-reference", "oracle-health-open"}
    # Every one of them was retrieved, so every one of them is graded rather than "not observed".
    assert all(card["grade"] in set("ABCDF") for card in cards.values())

    # Facts from the real documents, which no hand-written fixture in conftest.py exercises.
    findings = {eid: {f["code"]: f for d in card["dimensions"] for f in d["findings"]}
                for eid, card in cards.items()}
    assert "narrow but fully documented" in findings["cms-blue-button-2"]["T3"]["message"]
    assert findings["inferno-reference"]["I1"]["ok"]
    # Oracle Health answers 404 at .well-known/smart-configuration, so no smart.json is
    # committed: the SMART finding fails as an observation, not as an absence of data.
    assert not findings["oracle-health-open"]["I2"]["ok"]
    assert "absent or incomplete" in findings["oracle-health-open"]["I2"]["message"]


def test_an_offline_run_writes_its_history_to_a_scratch_path(tmp_path: Path) -> None:
    """`--offline` without `--history` must not resolve to data/history.json. The suite already
    runs from a throwaway directory, so this checks the resolution rather than the damage."""
    real = tmp_path / "data" / "history.json"
    real.parent.mkdir()
    before = json.dumps({META_KEY: {"mode": "live"},
                         "cms-blue-button-2": {"observations": [{"date": "2026-08-13",
                                                                 "up": True}]}}, indent=2)
    real.write_text(before)

    assert main(["grade", "--offline", "--fixtures", str(FIXTURES),
                 "--registry", str(FIXTURE_REGISTRY),
                 "--out", str(tmp_path / "site")]) == 0

    assert real.read_text() == before, "an offline run must not touch the live history"
    scratch = tmp_path / ".cache" / "offline-history.json"
    assert scratch.is_file()
    assert json.loads(scratch.read_text())[META_KEY]["mode"] == "offline"


def test_an_offline_run_does_not_load_the_shipped_cohorts(tmp_path: Path) -> None:
    """The fixture registry is a subset, and a cohort referencing an endpoint the graded registry
    lacks fails the build by design. That must not make the documented command fail."""
    cohorts = tmp_path / "data" / "cohorts"
    cohorts.mkdir(parents=True)
    (cohorts / "california.json").write_text(json.dumps({
        "id": "california", "name": "California", "description": "x", "sources": [
            {"label": "roster", "url": "https://example.test", "date": "2026-08-06"}],
        "members": [{"id": "plan", "name": "Plan", "programs": ["medi-cal"],
                     "endpoints": ["not-in-the-fixture-registry"]}]}))
    assert main(["grade", "--offline", "--fixtures", str(FIXTURES),
                 "--registry", str(FIXTURE_REGISTRY),
                 "--out", str(tmp_path / "site")]) == 0


def test_a_live_run_refuses_a_history_an_offline_run_wrote(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    assert main(["grade", "--offline", "--fixtures", str(FIXTURES),
                 "--registry", str(FIXTURE_REGISTRY), "--out", str(tmp_path / "site"),
                 "--history", str(history)]) == 0
    written = history.read_text()

    # The live run never gets as far as a request: it refuses on the history file.
    assert main(["grade", "--registry", str(FIXTURE_REGISTRY),
                 "--out", str(tmp_path / "site2"), "--history", str(history)]) == 2
    assert history.read_text() == written


def test_the_guard_fires_on_a_file_written_before_it_existed() -> None:
    """A legacy file carried no stamp, so the guard could not fire on the one file it was
    written to protect. The committed history now carries one."""
    committed = json.loads((REPO / "data" / "history.json").read_text())
    assert committed.get(META_KEY, {}).get("mode") == "live"
    assert endpoint_count(committed) == len(committed) - 1

    with pytest.raises(ValueError, match="written by a live run"):
        ensure_mode(dict(committed), offline=True)


def test_ensure_mode_stamps_an_unstamped_file_and_then_holds_it() -> None:
    history: dict[str, object] = {}
    ensure_mode(history, offline=True)
    assert history[META_KEY] == {"mode": "offline"}
    ensure_mode(history, offline=True)  # same mode again is fine
    with pytest.raises(ValueError):
        ensure_mode(history, offline=False)
