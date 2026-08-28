"""The availability page, and the four things it must refuse.

Every assertion here has a counterpart that shows the page doing the thing when it should, so
none of them can be satisfied by a page that renders nothing:

* a below-floor endpoint gets no share and no position, and an above-floor one gets both;
* the below-floor population is named rather than dropped;
* kinds are ordered separately, never against each other;
* with nothing above the floor the page says so instead of ranking a two-day record.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fhir_scorecard.archive import Record, records
from fhir_scorecard.cli import main
from fhir_scorecard.drift import MIN_OBSERVATIONS_TO_REPORT
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.leaderboard import LEADERBOARD_PATH, below_floor, page, ranked
from fhir_scorecard.site import DEFAULT_ORIGIN

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _text(markup: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _record(
    endpoint_id: str,
    *,
    days: int,
    answered: int,
    kind: str = "payer",
    name: str | None = None,
) -> Record:
    card = Scorecard(
        endpoint_id=endpoint_id,
        name=name or endpoint_id.replace("-", " ").title(),
        grade="B",
        reachable=True,
        dimensions=(),
        kind=kind,
    )
    history = {
        endpoint_id: {
            "first_seen": "2026-08-01",
            "observations": [
                {"date": f"2026-08-{day + 1:02d}", "up": day < answered} for day in range(days)
            ],
        }
    }
    (built,) = records(history, [card])
    return built


ABOVE = MIN_OBSERVATIONS_TO_REPORT
BELOW = MIN_OBSERVATIONS_TO_REPORT - 1


# --- the floor, from both sides ---


def test_an_endpoint_at_the_floor_gets_a_share_and_a_position() -> None:
    built = [_record("steady", days=ABOVE, answered=ABOVE)]
    assert [record.endpoint_id for record in ranked(built)] == ["steady"]
    body = _text(page(built, DEFAULT_ORIGIN).body)
    assert "100%" in body
    assert "1 of 1 endpoints have enough recorded observations" in body


def test_an_endpoint_below_the_floor_gets_neither() -> None:
    built = [_record("new-arrival", days=BELOW, answered=BELOW)]
    assert ranked(built) == []
    assert [record.endpoint_id for record in below_floor(built)] == ["new-arrival"]
    body = _text(page(built, DEFAULT_ORIGIN).body)
    assert "%" not in body.split("Why fourteen")[0].replace("100%", "")
    assert "1 more needed" in body


def test_the_below_floor_population_is_named_rather_than_dropped() -> None:
    built = [
        _record("steady", days=ABOVE, answered=ABOVE, name="Steady Plan"),
        _record("new-arrival", days=3, answered=2, name="New Arrival Plan"),
    ]
    body = _text(page(built, DEFAULT_ORIGIN).body)
    assert "Steady Plan" in body and "New Arrival Plan" in body
    assert f"1 of 2 endpoints have fewer than {MIN_OBSERVATIONS_TO_REPORT}" in body
    assert (
        f"New Arrival Plan : 3 observations, {MIN_OBSERVATIONS_TO_REPORT - 3} more needed" in body
    )


def test_no_share_is_ever_printed_for_a_below_floor_endpoint() -> None:
    """The load-bearing negative. A below-floor endpoint answered every check it was given, so
    a page that computed a share anyway would print a confident 100% off three days."""
    built = [_record("new-arrival", days=3, answered=3, name="New Arrival Plan")]
    section = _text(page(built, DEFAULT_ORIGIN).body)
    assert section.count("100%") == 0
    assert "New Arrival Plan" in section


def test_every_endpoint_over_the_floor_is_said_so_when_true() -> None:
    built = [_record("a", days=ABOVE, answered=ABOVE), _record("b", days=40, answered=39)]
    body = _text(page(built, DEFAULT_ORIGIN).body)
    assert "None. Every one of the 2 endpoints in the registry has at least" in body


# --- ordering ---


def test_the_order_is_by_measured_share_then_observations_then_name() -> None:
    built = [
        _record("low", days=20, answered=10, name="Low"),
        _record("high-short", days=ABOVE, answered=ABOVE, name="High Short"),
        _record("high-long", days=40, answered=40, name="High Long"),
        _record("middle", days=20, answered=15, name="Middle"),
    ]
    assert [record.name for record in ranked(built)] == ["High Long", "High Short", "Middle", "Low"]


def test_a_tie_breaks_on_name_so_the_order_is_total() -> None:
    """Two identical records must order the same way on every rebuild, or the published page
    reshuffles daily for no reason a reader could explain."""
    built = [
        _record("zeta", days=ABOVE, answered=ABOVE, name="Zeta"),
        _record("alpha", days=ABOVE, answered=ABOVE, name="Alpha"),
    ]
    assert [record.name for record in ranked(built)] == ["Alpha", "Zeta"]
    assert [record.name for record in ranked(list(reversed(built)))] == ["Alpha", "Zeta"]


def test_kinds_are_ordered_separately_and_never_against_each_other() -> None:
    built = [
        _record("sandbox", days=ABOVE, answered=ABOVE, kind="ehr", name="Vendor Sandbox"),
        _record("payer-api", days=ABOVE, answered=ABOVE - 3, kind="payer", name="Payer API"),
    ]
    body = page(built, DEFAULT_ORIGIN).body
    assert body.count("<table") == 2
    assert "EHR vendor sandboxes" in body and "Payer Patient Access APIs" in body
    text = _text(body)
    # Each kind's own table restarts its positions at one, which is what "within a kind" means.
    assert text.count("1 Vendor Sandbox") == 1
    assert text.count("1 Payer API") == 1


def test_an_unknown_kind_still_gets_its_own_table_under_its_own_name() -> None:
    built = [_record("odd", days=ABOVE, answered=ABOVE, kind="satellite-uplink")]
    assert "satellite-uplink" in _text(page(built, DEFAULT_ORIGIN).body)


# --- the empty state ---


def test_with_nothing_over_the_floor_the_page_declines_rather_than_ranks() -> None:
    built = [_record("a", days=2, answered=2), _record("b", days=1, answered=0)]
    body = page(built, DEFAULT_ORIGIN).body
    assert "<table" not in body
    assert "Nothing is ordered yet" in _text(body)
    assert "it declines to rank it" in _text(body)
    assert "0 of 2 endpoints have enough recorded observations" in _text(body)


def test_an_empty_registry_produces_a_page_rather_than_an_error() -> None:
    body = _text(page([], DEFAULT_ORIGIN).body)
    assert "Nothing is ordered yet" in body
    assert "None. Every one of the 0 endpoints" in body


# --- what a position is allowed to mean ---


def test_the_page_states_the_vantage_limit_where_the_numbers_are() -> None:
    """The failure this caveat exists for already happened once: a live payer endpoint recorded
    as dead because a middlebox on the probing network intercepted TLS."""
    body = _text(page([_record("a", days=ABOVE, answered=ABOVE)], DEFAULT_ORIGIN).body)
    assert "three hosts on one provider's network" in body
    assert "not a service-level measurement" in body
    assert "not a statement about an organization" in body


def test_a_position_is_not_stored_on_a_record() -> None:
    """Position belongs to the table. A record carrying one could outlive the table and be
    rendered somewhere the ordering does not apply."""
    record = _record("a", days=ABOVE, answered=ABOVE)
    assert not hasattr(record, "position")
    assert not hasattr(record, "rank")


# --- the page the build writes ---


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


def test_the_build_writes_the_availability_page(site: Path) -> None:
    assert (site / LEADERBOARD_PATH / "index.html").is_file()


def test_the_availability_page_satisfies_every_gate(site: Path) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.weight import audit_weight

    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []


def test_a_first_run_publishes_the_empty_state_rather_than_a_one_day_ranking(site: Path) -> None:
    """A fresh history has one observation per endpoint. The page has to say nothing is ordered
    rather than publish three endpoints at 100% off a single day."""
    body = _text((site / LEADERBOARD_PATH / "index.html").read_text(encoding="utf-8"))
    assert "Nothing is ordered yet" in body
    assert "0 of 3 endpoints have enough recorded observations" in body


def test_the_page_and_the_record_agree_about_who_is_below_the_floor(site: Path) -> None:
    page_text = _text((site / LEADERBOARD_PATH / "index.html").read_text(encoding="utf-8"))
    for endpoint_id in ("cms-blue-button-2", "inferno-reference", "oracle-health-open"):
        payload = json.loads((site / "api" / "history" / f"{endpoint_id}.json").read_text())
        assert payload["answered_percent"] is None
        assert payload["name"] in page_text


# --- the floor has to be able to exclude something, and it does ---

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_HISTORY = REPO_ROOT / "data" / "history.json"


def _committed_records() -> list[Record]:
    """Records built from the history committed to this repository.

    Addressed absolutely: ``conftest._isolated_cwd`` runs every test from a throwaway
    directory precisely so a relative path cannot reach the real curation files by accident.
    """
    history = json.loads(COMMITTED_HISTORY.read_text(encoding="utf-8"))
    history.pop("_meta", None)
    return records(
        history,
        [
            Scorecard(
                endpoint_id=endpoint_id,
                name=endpoint_id,
                grade="B",
                reachable=True,
                dimensions=(),
                kind="payer",
            )
            for endpoint_id in sorted(history)
        ],
    )


def test_the_floor_excludes_something_in_the_data_this_repository_ships() -> None:
    """A floor nothing can fall below is a gate that cannot fail, so this asserts on real
    committed data rather than on prose.

    ``data/history.json`` on ``main`` is the seed the first live run started from and is no
    longer updated (README, Observability row): 19 endpoints, two observations each. Every one
    of them is below the floor, so this seed alone publishes the empty state and orders nothing.
    The other side of the floor is exercised by the synthetic records above, and measured on the
    live record - which lives on the ``capability-history`` branch and is deliberately not in
    this repository - at 30 of 45 endpoints above the floor and 15 below, on 2026-08-27.

    If the floor were removed or dropped to something every record clears, this test fails.
    """
    built = _committed_records()
    assert built, "the committed history should not be empty"
    assert below_floor(built) == built, "every seeded endpoint is below the floor"
    assert ranked(built) == []
    counts = {record.observed for record in built}
    assert counts == {2}, f"the seed is two observations per endpoint, found {counts}"


def test_the_committed_seed_alone_would_publish_the_empty_state() -> None:
    """The consequence of the test above, asserted on the page rather than on the lists, so a
    page that ranked below-floor endpoints anyway would still fail."""
    body = _text(page(_committed_records(), DEFAULT_ORIGIN).body)
    assert "Nothing is ordered yet" in body
    assert "0 of 19 endpoints have enough recorded observations" in body
    assert f"19 of 19 endpoints have fewer than {MIN_OBSERVATIONS_TO_REPORT}" in body
