"""The conformance-over-time report: computed from the record, or not stated at all.

Two properties carry this file. Every figure on the page must be recomputable from the records
it was given, and the report must decline to say anything the record does not hold - which for
grades means declining permanently, because no run has ever written one down.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fhir_scorecard.archive import records
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.over_time import OVER_TIME_PATH, page, sections
from fhir_scorecard.site import DEFAULT_ORIGIN

ROOT = Path(__file__).resolve().parent.parent


def _text(markup: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _card(endpoint_id: str, name: str) -> Scorecard:
    return Scorecard(
        endpoint_id=endpoint_id, name=name, grade="B", reachable=True, dimensions=(), kind="payer"
    )


def _entry(dates_up: dict[str, bool], first_seen: str, events=(), alternations=()):
    return {
        "first_seen": first_seen,
        "observations": [{"date": date, "up": up} for date, up in dates_up.items()],
        "events": list(events),
        "alternations": list(alternations),
    }


TWO_MONTHS = {
    "steady": _entry(
        {"2026-07-30": True, "2026-08-01": True, "2026-08-02": True},
        "2026-07-30",
    ),
    "flappy": _entry(
        {"2026-08-01": True, "2026-08-02": False},
        "2026-08-01",
        events=[{"date": "2026-08-02", "changes": ["software_version: '1' -> '2'"]}],
        alternations=[
            {
                "first_return": "2026-08-01",
                "last_return": "2026-08-02",
                "returns": 3,
                "state_first_seen": "2026-07-30",
                "changes": ["software_version: '2' -> '1'"],
            }
        ],
    ),
}


def _two_month_records():
    return records(TWO_MONTHS, [_card("steady", "Steady Plan"), _card("flappy", "Flappy Plan")])


# --- the months come out of the record ---


def test_one_section_per_month_the_record_holds_oldest_first() -> None:
    built = sections(_two_month_records())
    assert [section.month for section in built] == ["2026-07", "2026-08"]


def test_a_month_counts_only_the_endpoints_observed_in_it() -> None:
    july, august = sections(_two_month_records())
    assert july.observed == ("Steady Plan",)
    assert august.observed == ("Flappy Plan", "Steady Plan")


def test_an_endpoint_not_observed_in_a_month_is_in_neither_column() -> None:
    """Absent from both is the third state, and the page says what it means. Folding it into
    "missed a check" would report this project's own gap as the endpoint's."""
    july, _ = sections(_two_month_records())
    assert "Flappy Plan" not in july.answered_every_day
    assert "Flappy Plan" not in july.missed_at_least_once
    assert "was not observed at all this month" in _text(
        page(_two_month_records(), DEFAULT_ORIGIN).body
    )


def test_entering_the_record_is_dated_by_first_seen() -> None:
    july, august = sections(_two_month_records())
    assert july.entered == ("Steady Plan",)
    assert august.entered == ("Flappy Plan",)


def test_answered_every_check_and_missed_one_are_disjoint_and_computed() -> None:
    _, august = sections(_two_month_records())
    assert august.answered_every_day == ("Steady Plan",)
    assert august.missed_at_least_once == ("Flappy Plan",)
    assert not set(august.answered_every_day) & set(august.missed_at_least_once)


# --- changes and returns ---


def test_a_month_with_no_declaration_change_says_so_rather_than_an_empty_table() -> None:
    july = sections(_two_month_records())[0]
    assert july.changes == ()
    body = _text(page(_two_month_records(), DEFAULT_ORIGIN).body)
    assert "No endpoint changed what it declares this month. That is a result, not a gap" in body


def test_a_month_with_changes_lists_them_with_their_dates() -> None:
    august = sections(_two_month_records())[1]
    assert august.changes == (("2026-08-02", "Flappy Plan", ("software_version: '1' -> '2'",)),)
    body = _text(page(_two_month_records(), DEFAULT_ORIGIN).body)
    assert "1 recorded change to what an endpoint declares" in body


def test_returns_are_counted_apart_from_changes() -> None:
    august = sections(_two_month_records())[1]
    assert august.returns == (("2026-08-01 to 2026-08-02", "Flappy Plan", 3),)
    body = _text(page(_two_month_records(), DEFAULT_ORIGIN).body)
    assert "Declarations returned to" in body
    assert "returned 3 times" in body
    assert "1 recorded change" in body, "the change count must not absorb the returns"


def test_a_month_with_no_returns_renders_no_returns_heading() -> None:
    body = _text(page(_two_month_records(), DEFAULT_ORIGIN).body)
    assert body.count("Declarations returned to") == 1, "July has none, August has one"


# --- every figure is recomputable ---


def test_the_summary_counts_match_the_sections_they_summarise() -> None:
    built = _two_month_records()
    body = _text(page(built, DEFAULT_ORIGIN).body)
    total_changes = sum(len(section.changes) for section in sections(built))
    total_returns = sum(len(section.returns) for section in sections(built))
    summary = body[body.index(f"{len(built)} endpoints,") :][:160]
    assert "2 months of record" in summary
    assert f"{total_changes} recorded declaration change" in summary
    assert f"{total_returns} recorded return" in summary


def test_the_report_is_regenerable_byte_for_byte_from_the_same_inputs() -> None:
    """The report stores nothing, so two builds from one record must be identical. A figure
    that moved between runs would be reading something other than the record."""
    first = page(_two_month_records(), DEFAULT_ORIGIN).body
    second = page(_two_month_records(), DEFAULT_ORIGIN).body
    assert first == second


def test_an_empty_record_reports_no_window_rather_than_zeros() -> None:
    body = _text(page(records({}, [_card("a", "A")]), DEFAULT_ORIGIN).body)
    assert "no month yet" in body
    assert "holds no observations, so there is no window to report over" in body
    assert "0 endpoints, 0 months" not in body


# --- the limitation, stated because it is permanent ---


def test_the_page_says_it_cannot_report_grade_changes() -> None:
    body = _text(page(_two_month_records(), DEFAULT_ORIGIN).body)
    assert "This report does not say whether grades moved" in body
    assert "has never retained a grade" in body


def test_the_committed_history_really_does_not_retain_a_grade() -> None:
    """The limitation above is a fact about the data, not an excuse. If a grade ever starts
    being recorded, this test fails and the page's claim has to be revisited."""
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    history.pop("_meta", None)
    keys = {key for entry in history.values() for key in entry}
    assert keys, "the committed history should not be empty"
    assert not any("grade" in key or "score" in key for key in keys), sorted(keys)


def test_no_section_carries_a_grade_field() -> None:
    for section in sections(_two_month_records()):
        assert not any("grade" in field for field in vars(section))


# --- the page the build writes ---


@pytest.fixture
def site(tmp_path: Path) -> Path:
    from fhir_scorecard.cli import main

    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(ROOT / "tests" / "fixtures"),
                "--registry",
                str(ROOT / "tests" / "fixtures" / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    return out


def test_the_build_writes_the_report(site: Path) -> None:
    assert (site / OVER_TIME_PATH / "index.html").is_file()


def test_the_report_satisfies_every_gate(site: Path) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.weight import audit_weight

    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
