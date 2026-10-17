"""The grade record: append-only, deterministic, idempotent, opt-in, and never a fall for silence.

``docs/adr/0008-grade-history.md`` is the decision these tests hold. Two properties matter more
than the rest. A run that is not given a record must publish exactly what it published before,
and a run that observed nothing must never read as a grade getting worse.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard import grade_history as gh
from fhir_scorecard.cli import main
from fhir_scorecard.grading import NOT_OBSERVED, Scorecard
from fhir_scorecard.published import LETTERS, NOT_OBSERVED_LITERAL

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def card(endpoint_id: str, grade: str) -> Scorecard:
    return Scorecard(
        endpoint_id=endpoint_id,
        name=endpoint_id.title(),
        grade=grade,
        reachable=grade != NOT_OBSERVED,
        dimensions=(),
    )


def run(ledger: dict[str, Any], day: str, **grades: str) -> gh.Published:
    return gh.record(ledger, [card(name, grade) for name, grade in grades.items()], day)


# --- vocabulary ---


def test_the_vocabulary_is_the_graders_and_the_published_contracts() -> None:
    assert set(gh.GRADES) == {*LETTERS, NOT_OBSERVED_LITERAL}
    assert NOT_OBSERVED == NOT_OBSERVED_LITERAL


def test_every_status_has_words_and_nothing_else_is_used() -> None:
    seen = {
        run({}, "2026-10-01", a="B").changes["a"].status,
        run({}, "2026-10-01", a=NOT_OBSERVED).changes["a"].status,
    }
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    seen.add(run(ledger, "2026-10-02", a="B").changes["a"].status)
    seen.add(run(ledger, "2026-10-03", a="C").changes["a"].status)
    assert seen == set(gh.STATUSES)
    assert all(gh.STATUSES.values())


# --- what counts as a change ---


def test_the_first_run_has_nothing_to_compare_against() -> None:
    change = run({}, "2026-10-01", a="B").changes["a"]
    assert change.status == gh.FIRST_RECORDED
    assert change.previous_grade is None
    assert change.previous_date is None
    assert change.direction is None


def test_the_same_letter_is_unchanged_and_names_the_run_it_was_compared_with() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    change = run(ledger, "2026-10-02", a="B").changes["a"]
    assert (change.status, change.previous_grade, change.previous_date) == (
        gh.UNCHANGED,
        "B",
        "2026-10-01",
    )


@pytest.mark.parametrize(
    ("before", "after", "direction"),
    [
        ("C", "B", gh.IMPROVED),
        ("B", "C", gh.DECLINED),
        ("F", "A", gh.IMPROVED),
        ("A", "F", "declined"),
    ],
)
def test_a_different_letter_is_a_change_with_a_direction(
    before: str, after: str, direction: str
) -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a=before)
    change = run(ledger, "2026-10-02", a=after).changes["a"]
    assert (change.status, change.previous_grade, change.direction) == (
        gh.CHANGED,
        before,
        direction,
    )


def test_a_run_that_observed_nothing_is_not_a_fall() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    change = run(ledger, "2026-10-02", a=NOT_OBSERVED).changes["a"]
    assert change.status == gh.NOT_OBSERVED_THIS_RUN
    assert change.direction is None
    assert (change.previous_grade, change.previous_date) == ("B", "2026-10-01")


def test_not_observed_rows_are_skipped_when_looking_back_and_the_gap_is_visible() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    run(ledger, "2026-10-02", a=NOT_OBSERVED)
    unchanged = run(ledger, "2026-10-03", a="B").changes["a"]
    assert (unchanged.status, unchanged.previous_date) == (gh.UNCHANGED, "2026-10-01")
    changed = run(ledger, "2026-10-04", a="C").changes["a"]
    assert (changed.status, changed.previous_date) == (gh.CHANGED, "2026-10-03")


def test_an_endpoint_that_was_only_ever_unobserved_has_no_previous_letter() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a=NOT_OBSERVED)
    change = run(ledger, "2026-10-02", a="D").changes["a"]
    assert change.status == gh.FIRST_RECORDED


# --- append-only, idempotent, deterministic ---


def test_a_rerun_of_the_same_day_is_byte_identical() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B", b="C")
    first = json.dumps(ledger, sort_keys=True)
    run(ledger, "2026-10-01", a="B", b="C")
    assert json.dumps(ledger, sort_keys=True) == first


def test_rerunning_a_day_cannot_turn_a_change_into_unchanged() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    first = run(ledger, "2026-10-02", a="C").changes["a"]
    again = run(ledger, "2026-10-02", a="C").changes["a"]
    assert first == again
    assert again.status == gh.CHANGED


def test_a_rerun_replaces_only_that_days_row() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    run(ledger, "2026-10-02", a=NOT_OBSERVED)
    run(ledger, "2026-10-02", a="B")
    assert ledger["a"]["grades"] == [
        {"date": "2026-10-01", "grade": "B"},
        {"date": "2026-10-02", "grade": "B"},
    ]


def test_earlier_rows_are_never_touched_by_a_later_run() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B")
    run(ledger, "2026-10-02", a="C")
    before = json.dumps(ledger["a"]["grades"][:2], sort_keys=True)
    run(ledger, "2026-10-03", a="A")
    assert json.dumps(ledger["a"]["grades"][:2], sort_keys=True) == before
    assert len(ledger["a"]["grades"]) == 3


def test_a_run_dated_before_the_newest_row_raises_and_changes_nothing() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-05", a="B", b="C")
    snapshot = json.dumps(ledger, sort_keys=True)
    with pytest.raises(ValueError, match="past"):
        run(ledger, "2026-10-04", a="B", b="C")
    # Also for an endpoint this run did not grade: the rule is about the record.
    with pytest.raises(ValueError, match="past"):
        run(ledger, "2026-10-04", c="A")
    assert json.dumps(ledger, sort_keys=True) == snapshot


def test_an_endpoint_a_run_did_not_grade_keeps_its_rows_and_gets_no_new_one() -> None:
    ledger: dict[str, Any] = {}
    run(ledger, "2026-10-01", a="B", b="C")
    run(ledger, "2026-10-02", a="B")
    assert len(ledger["a"]["grades"]) == 2
    assert ledger["b"]["grades"] == [{"date": "2026-10-01", "grade": "C"}]


def test_the_file_does_not_depend_on_the_order_endpoints_were_graded_in(tmp_path: Path) -> None:
    one: dict[str, Any] = {}
    two: dict[str, Any] = {}
    run(one, "2026-10-01", a="B", b="C", c=NOT_OBSERVED)
    run(two, "2026-10-01", c=NOT_OBSERVED, b="C", a="B")
    gh.save(tmp_path / "one.json", one)
    gh.save(tmp_path / "two.json", two)
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


def test_a_bad_run_date_is_refused() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        run({}, "10/01/2026", a="B")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        run({}, "2026-13-01", a="B")


# --- reading the record back ---


def test_an_absent_record_is_a_first_run(tmp_path: Path) -> None:
    assert gh.load(tmp_path / "none.json") == {}


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    ledger: dict[str, Any] = {}
    gh.ensure_mode(ledger, offline=False)
    run(ledger, "2026-10-01", a="B")
    gh.save(tmp_path / "g.json", ledger)
    assert gh.load(tmp_path / "g.json") == ledger
    assert gh.rows_for(ledger, "a") == (gh.GradeRow("2026-10-01", "B"),)
    assert gh.rows_for(ledger, "missing") == ()
    assert ledger["_meta"] == {"mode": "live", "schema_version": gh.SCHEMA_VERSION}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"a": {"grades": "B"}}),
        json.dumps({"a": {"grades": [{"date": "2026-10-01", "grade": "E"}]}}),
        json.dumps({"a": {"grades": [{"date": "2026-10-01", "grade": "B", "score": 90}]}}),
        json.dumps({"a": {"grades": [{"date": "yesterday", "grade": "B"}]}}),
        json.dumps(
            {
                "a": {
                    "grades": [
                        {"date": "2026-10-02", "grade": "B"},
                        {"date": "2026-10-02", "grade": "C"},
                    ]
                }
            }
        ),
        json.dumps(
            {
                "a": {
                    "grades": [
                        {"date": "2026-10-02", "grade": "B"},
                        {"date": "2026-10-01", "grade": "C"},
                    ]
                }
            }
        ),
        json.dumps({"_meta": {"schema_version": gh.SCHEMA_VERSION + 1}}),
    ],
)
def test_a_record_that_cannot_be_read_stops_the_run_rather_than_being_rewritten(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "g.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        gh.load(path)
    assert path.read_text(encoding="utf-8") == content


def test_fixture_grades_cannot_enter_a_live_record() -> None:
    ledger: dict[str, Any] = {}
    gh.ensure_mode(ledger, offline=False)
    with pytest.raises(ValueError, match="live run"):
        gh.ensure_mode(ledger, offline=True)


# --- the published shapes ---


def test_the_per_endpoint_record_is_a_pure_function_of_its_rows() -> None:
    rows = (gh.GradeRow("2026-10-01", "B"), gh.GradeRow("2026-10-02", NOT_OBSERVED))
    text = gh.record_json("a", "Alpha", rows)
    assert text == gh.record_json("a", "Alpha", rows)
    data = json.loads(text)
    assert data["rows"] == [
        {"date": "2026-10-01", "grade": "B"},
        {"date": "2026-10-02", "grade": "not observed"},
    ]
    assert data["schema_version"] == gh.SCHEMA_VERSION
    assert "generated_at" not in data
    assert "never edited" in data["note"]


def test_a_change_states_what_it_was_compared_against() -> None:
    payload = gh.change_for([gh.GradeRow("2026-10-01", "C")], "B").as_json()
    assert payload["basis"] == gh.BASIS
    assert payload["status_text"] == gh.STATUSES[gh.CHANGED]
    assert "never read as a fall" in str(payload["basis"])


# --- through the command ---


class _Clock:
    """Stands in for ``time`` inside the CLI so a test can run on chosen days."""

    def __init__(self, day: str) -> None:
        self.day = day

    def gmtime(self) -> time.struct_time:
        return time.gmtime()

    def strftime(self, fmt: str, moment: time.struct_time | None = None) -> str:
        if fmt == "%Y-%m-%d":
            return self.day
        return time.strftime(fmt, moment or time.gmtime())


def build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    day: str,
    out: str = "site",
    history: str = "history.json",
    grade_history: str | None = None,
    offline: bool = True,
) -> int:
    monkeypatch.setattr("fhir_scorecard.cli.time", _Clock(day))
    args = [
        "grade",
        "--fixtures",
        str(FIXTURES),
        "--registry",
        str(FIXTURES / "registry.json"),
        "--out",
        str(tmp_path / out),
        "--history",
        str(tmp_path / history),
    ]
    if offline:
        args.append("--offline")
    if grade_history is not None:
        args += ["--grade-history", str(tmp_path / grade_history)]
    return main(args)


def endpoint_json(tmp_path: Path, endpoint_id: str, out: str = "site") -> dict[str, Any]:
    path = tmp_path / out / "api" / "endpoint" / f"{endpoint_id}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_without_the_flag_nothing_about_grade_history_is_read_written_or_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert build(tmp_path, monkeypatch, day="2026-10-01") == 0
    site = tmp_path / "site"
    assert not (site / "api" / gh.API_DIR).exists()
    assert "grade_change" not in endpoint_json(tmp_path, "cms-blue-button-2")
    index = json.loads((site / "api" / "index.json").read_text(encoding="utf-8"))
    assert all("grade_change" not in e and "grade_history" not in e for e in index["endpoints"])
    assert not list(tmp_path.glob("*grade*"))


def test_recording_changes_no_grade_score_or_dataset_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Separate observation records, so the only difference between the builds is the flag.
    assert build(tmp_path, monkeypatch, day="2026-10-01", out="plain", history="h1.json") == 0
    assert (
        build(
            tmp_path,
            monkeypatch,
            day="2026-10-01",
            out="recorded",
            history="h2.json",
            grade_history="g.json",
        )
        == 0
    )
    assert (tmp_path / "plain" / "dataset.csv").read_bytes() == (
        tmp_path / "recorded" / "dataset.csv"
    ).read_bytes()
    for path in sorted((tmp_path / "plain" / "api" / "endpoint").glob("*.json")):
        plain = json.loads(path.read_text(encoding="utf-8"))
        recorded = endpoint_json(tmp_path, path.stem, "recorded")
        recorded.pop("grade_change")
        assert recorded == plain


def test_a_recorded_build_publishes_the_change_the_history_and_passes_every_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.cli import DEFAULT_ORIGIN
    from fhir_scorecard.published import audit_published_grades
    from fhir_scorecard.weight import audit_weight

    assert build(tmp_path, monkeypatch, day="2026-10-01", grade_history="g.json") == 0
    first = endpoint_json(tmp_path, "cms-blue-button-2")
    assert first["grade_change"]["status"] == gh.FIRST_RECORDED
    assert first["grade_change"]["grade"] == first["endpoint"]["grade"]

    # Plant a different letter for yesterday, as the record would hold it had the grade moved.
    ledger = json.loads((tmp_path / "g.json").read_text(encoding="utf-8"))
    today_letter = first["endpoint"]["grade"]
    earlier = "F" if today_letter != "F" else "A"
    ledger["cms-blue-button-2"]["grades"] = [{"date": "2026-09-30", "grade": earlier}]
    (tmp_path / "g.json").write_text(json.dumps(ledger), encoding="utf-8")

    assert build(tmp_path, monkeypatch, day="2026-10-02", grade_history="g.json") == 0
    changed = endpoint_json(tmp_path, "cms-blue-button-2")["grade_change"]
    assert changed["status"] == gh.CHANGED
    assert (changed["previous_grade"], changed["previous_date"]) == (earlier, "2026-09-30")
    assert changed["direction"] == (gh.IMPROVED if earlier == "F" else gh.DECLINED)

    # An endpoint no vantage reached publishes a status that is not a fall.
    unreached = endpoint_json(tmp_path, "aspirus-patient-access")["grade_change"]
    assert unreached["status"] == gh.NOT_OBSERVED_THIS_RUN
    assert unreached["direction"] is None

    index = json.loads((tmp_path / "site" / "api" / "index.json").read_text(encoding="utf-8"))
    by_id = {e["endpoint_id"]: e for e in index["endpoints"]}
    assert by_id["cms-blue-button-2"]["grade_change"]["status"] == gh.CHANGED
    url = by_id["cms-blue-button-2"]["grade_history"]
    assert url.endswith("/api/grade-history/cms-blue-button-2.json")
    record = json.loads(
        (tmp_path / "site" / "api" / "grade-history" / "cms-blue-button-2.json").read_text(
            encoding="utf-8"
        )
    )
    assert [r["date"] for r in record["rows"]] == ["2026-09-30", "2026-10-02"]

    site = tmp_path / "site"
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
    assert audit_published_grades(site) == []


def test_rerunning_the_same_day_rewrites_the_same_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert build(tmp_path, monkeypatch, day="2026-10-01", grade_history="g.json") == 0
    ledger = (tmp_path / "g.json").read_bytes()
    file = tmp_path / "site" / "api" / "grade-history" / "cms-blue-button-2.json"
    record = file.read_bytes()
    assert build(tmp_path, monkeypatch, day="2026-10-01", grade_history="g.json") == 0
    assert (tmp_path / "g.json").read_bytes() == ledger
    assert file.read_bytes() == record


def test_a_run_dated_before_the_record_stops_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build(tmp_path, monkeypatch, day="2026-10-05", grade_history="g.json") == 0
    ledger = (tmp_path / "g.json").read_bytes()
    history = (tmp_path / "history.json").read_bytes()
    assert build(tmp_path, monkeypatch, day="2026-10-04", out="late", grade_history="g.json") == 2
    assert "past" in capsys.readouterr().err
    assert (tmp_path / "g.json").read_bytes() == ledger
    assert (tmp_path / "history.json").read_bytes() == history
    assert not (tmp_path / "late").exists()


def test_a_live_record_refuses_a_fixture_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "g.json").write_text(
        json.dumps({"_meta": {"mode": "live", "schema_version": 1}}), encoding="utf-8"
    )
    before = (tmp_path / "g.json").read_bytes()
    assert build(tmp_path, monkeypatch, day="2026-10-01", grade_history="g.json") == 2
    assert "live run" in capsys.readouterr().err
    assert (tmp_path / "g.json").read_bytes() == before
    assert not (tmp_path / "site").exists()


def test_an_unreadable_record_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "g.json").write_text("{broken", encoding="utf-8")
    assert build(tmp_path, monkeypatch, day="2026-10-01", grade_history="g.json") == 2
    assert "not readable JSON" in capsys.readouterr().err
    assert (tmp_path / "g.json").read_text(encoding="utf-8") == "{broken"
