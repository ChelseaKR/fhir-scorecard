"""The observation record, and the two things it must never do.

An archive is only worth publishing if a reader can trust what is *not* on it. Two refusals are
load-bearing here and each is asserted from both sides, because an assertion that only ever sees
the refusing case cannot tell a working refusal from a page that prints nothing at all:

* an endpoint with no observations says so, and renders no table, no zero, and no percent;
* a rate is printed only at or above ``drift.MIN_OBSERVATIONS_TO_REPORT``, and the boundary is
  tested at one observation below it and at exactly it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fhir_scorecard.archive import (
    Record,
    history_json,
    index_page,
    mode_of,
    record_page,
    records,
    window,
)
from fhir_scorecard.cli import main
from fhir_scorecard.drift import META_KEY, MIN_OBSERVATIONS_TO_REPORT
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.site import DEFAULT_ORIGIN

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _card(endpoint_id: str, name: str = "Acme Health") -> Scorecard:
    return Scorecard(endpoint_id=endpoint_id, name=name, grade="B", reachable=True, dimensions=())


def _history(endpoint_id: str, days: int, answered: int) -> dict[str, object]:
    return {
        endpoint_id: {
            "first_seen": "2026-08-01",
            "last_seen": "2026-08-30",
            "observations": [
                {"date": f"2026-08-{day + 1:02d}", "up": day < answered} for day in range(days)
            ],
        }
    }


def _text(markup: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


# --- what the record carries ---


def test_a_record_reads_only_what_the_history_holds() -> None:
    (record,) = records(_history("acme", days=5, answered=4), [_card("acme")])
    assert (record.observed, record.answered) == (5, 4)
    assert record.first_seen == "2026-08-01"
    assert [observation.date for observation in record.observations][:2] == [
        "2026-08-01",
        "2026-08-02",
    ]


def test_an_endpoint_the_registry_no_longer_grades_leaves_the_archive() -> None:
    """A history file remembers endpoints for as long as it is kept. Publishing one that is no
    longer graded would show a record nobody is still adding to as though it were live."""
    history = _history("acme", days=5, answered=5) | _history("retired", days=90, answered=90)
    assert [record.endpoint_id for record in records(history, [_card("acme")])] == ["acme"]


def test_an_endpoint_with_no_history_still_gets_a_record() -> None:
    (record,) = records({}, [_card("brand-new")])
    assert record.observed == 0
    assert record.first_seen is None


def test_a_malformed_observation_list_is_read_as_no_observations() -> None:
    """History is a file on a branch that a workflow rewrites daily. A shape nobody expected
    must produce an empty record, never a crash in the publishing run."""
    assert records({"acme": {"observations": "yesterday"}}, [_card("acme")])[0].observed == 0
    assert records({"acme": ["not", "a", "dict"]}, [_card("acme")])[0].observed == 0
    assert records({"acme": {"observations": [{"up": True}]}}, [_card("acme")])[0].observed == 0


# --- refusal one: nothing observed means nothing stated ---


def test_an_unobserved_endpoint_renders_no_table_and_no_number() -> None:
    (record,) = records({}, [_card("brand-new", "Brand New Plan")])
    body = record_page(record, DEFAULT_ORIGIN).body
    assert "No observations are recorded for this endpoint yet" in _text(body)
    assert "<table" not in body
    assert record.rate_percent is None
    assert "0%" not in body


def test_an_observed_endpoint_does_render_its_table() -> None:
    """The other side of the refusal above: without this, a template that rendered nothing at
    all would pass the test that matters."""
    (record,) = records(_history("acme", days=3, answered=2), [_card("acme")])
    body = record_page(record, DEFAULT_ORIGIN).body
    assert "<table" in body
    assert _text(body).count("answered") >= 2
    assert "did not answer" in _text(body)


def test_an_unobserved_endpoint_publishes_a_null_rate_not_a_zero() -> None:
    (record,) = records({}, [_card("brand-new")])
    payload = json.loads(history_json(record, "2026-08-27"))
    assert payload["answered_percent"] is None
    assert payload["observations_recorded"] == 0
    assert payload["observations"] == []


# --- refusal two: the reporting floor, tested at the boundary ---


@pytest.mark.parametrize("days", [1, MIN_OBSERVATIONS_TO_REPORT - 1])
def test_below_the_floor_no_rate_is_published_anywhere(days: int) -> None:
    (record,) = records(_history("acme", days=days, answered=days), [_card("acme")])
    assert record.rate_percent is None
    body = record_page(record, DEFAULT_ORIGIN).body
    assert "%" not in _text(body)
    assert f"needs {MIN_OBSERVATIONS_TO_REPORT - days} more" in _text(body)
    assert json.loads(history_json(record, "2026-08-27"))["answered_percent"] is None


def test_at_the_floor_the_rate_appears_and_is_the_measured_one() -> None:
    """The boundary from the other side. If the floor were quietly lowered or removed, the test
    above would fail; if it were raised, this one would."""
    days = MIN_OBSERVATIONS_TO_REPORT
    (record,) = records(_history("acme", days=days, answered=days - 2), [_card("acme")])
    assert record.rate_percent == round(100 * (days - 2) / days)
    assert f"({record.rate_percent}%)" in _text(record_page(record, DEFAULT_ORIGIN).body)


def test_the_index_names_the_below_floor_population_rather_than_dropping_it() -> None:
    built = records(
        _history("low", days=3, answered=3) | _history("high", days=40, answered=38),
        [_card("low", "Low Count Plan"), _card("high", "High Count Plan")],
    )
    text = _text(index_page(built, DEFAULT_ORIGIN).body)
    assert "Low Count Plan" in text and "High Count Plan" in text
    assert f"1 of 2 endpoints have fewer than {MIN_OBSERVATIONS_TO_REPORT} observations" in text
    assert "not enough observations" in text
    assert "95%" in text


def test_the_index_says_so_when_every_endpoint_is_over_the_floor() -> None:
    built = records(_history("high", days=40, answered=40), [_card("high")])
    text = _text(index_page(built, DEFAULT_ORIGIN).body)
    assert f"Every endpoint listed has at least {MIN_OBSERVATIONS_TO_REPORT}" in text
    assert "have fewer than" not in text


def test_the_index_reports_the_window_the_record_covers() -> None:
    built = records(_history("acme", days=4, answered=4), [_card("acme")])
    assert window(built) == ("2026-08-01", "2026-08-04")
    assert "Observations run from 2026-08-01 to 2026-08-04" in _text(
        index_page(built, DEFAULT_ORIGIN).body
    )


def test_the_index_with_nothing_observed_says_nothing_is_observed() -> None:
    built = records({}, [_card("acme")])
    assert window(built) is None
    assert "No observations are recorded yet" in _text(index_page(built, DEFAULT_ORIGIN).body)


# --- fixture observations must never read as measurements ---


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ({META_KEY: {"mode": "live"}}, "live"),
        ({META_KEY: {"mode": "offline"}}, "offline"),
        ({}, "unknown"),
        ({META_KEY: "not a dict"}, "unknown"),
    ],
)
def test_the_mode_is_read_from_the_history_itself(history: dict, expected: str) -> None:
    assert mode_of(history) == expected


def test_a_fixture_written_record_is_labelled_and_a_live_one_is_not() -> None:
    built = records(_history("acme", days=3, answered=3), [_card("acme")])
    assert "must not be read as availability" in _text(
        index_page(built, DEFAULT_ORIGIN, "offline").body
    )
    assert "must not be read as availability" not in _text(
        index_page(built, DEFAULT_ORIGIN, "live").body
    )


# --- the pages the build actually writes ---


@pytest.fixture
def site(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(FIXTURES),
                "--registry",
                str(FIXTURES / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    return out


def test_the_build_writes_a_record_page_and_a_json_file_per_endpoint(site: Path) -> None:
    for endpoint_id in ("cms-blue-button-2", "inferno-reference", "oracle-health-open"):
        assert (site / "history" / endpoint_id / "index.html").is_file()
        payload = json.loads((site / "api" / "history" / f"{endpoint_id}.json").read_text())
        assert payload["endpoint_id"] == endpoint_id
        assert payload["minimum_observations_to_report"] == MIN_OBSERVATIONS_TO_REPORT


def test_the_archive_pages_satisfy_the_contract_the_accessibility_rules_and_the_budgets(
    site: Path,
) -> None:
    """The archive is new page shapes, and phases 6 and 7 exist so new page shapes cannot ship
    unchecked. This is that check, run over a build that contains them."""
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.weight import audit_weight

    assert (site / "history" / "index.html").is_file()
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []


def test_an_endpoint_page_links_its_own_record(site: Path) -> None:
    page = (site / "endpoint" / "cms-blue-button-2" / "index.html").read_text(encoding="utf-8")
    assert 'href="/history/cms-blue-button-2/"' in page


def test_the_json_record_matches_the_page_it_accompanies(site: Path) -> None:
    payload = json.loads((site / "api" / "history" / "cms-blue-button-2.json").read_text())
    page = _text((site / "history" / "cms-blue-button-2" / "index.html").read_text())
    assert f"answered {payload['observations_answered']} of " in page
    for observation in payload["observations"]:
        assert observation["date"] in page


def test_a_record_page_renders_every_observation_and_invents_none() -> None:
    (record,) = records(_history("acme", days=9, answered=6), [_card("acme")])
    body = record_page(record, DEFAULT_ORIGIN).body
    assert body.count("<tr>") == 9 + 1  # nine observations plus the header row
    assert body.count("did not answer") == 3


def test_a_record_lists_its_observations_newest_first() -> None:
    (record,) = records(_history("acme", days=3, answered=3), [_card("acme")])
    body = record_page(record, DEFAULT_ORIGIN).body
    assert body.index("2026-08-03") < body.index("2026-08-01")


def test_record_pages_describe_themselves_distinctly(site: Path) -> None:
    """Two record pages sharing a description would be two pages a search engine reads as one.
    The accessibility gate already refuses duplicate titles; this is the description."""
    descriptions = {
        re.search(r'<meta name="description" content="([^"]*)"', path.read_text()).group(1)
        for path in (site / "history").glob("*/index.html")
    }
    assert len(descriptions) == 3


def test_a_record_of_one_endpoint_never_reports_another(site: Path) -> None:
    payload = json.loads((site / "api" / "history" / "inferno-reference.json").read_text())
    assert "cms-blue-button-2" not in json.dumps(payload)


def test_the_dataclass_rejects_nothing_it_was_given() -> None:
    """``Record`` is a plain carrier: every number on a page is computed from its observations,
    so a record built by hand behaves exactly like one read from a file."""
    record = Record("x", "X", (), None, None)
    assert record.summary() == "no observations recorded yet"
    assert record.rate_percent is None
