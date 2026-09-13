"""The live-deployment check had no tests at all, so nothing said it could report a difference.

``tools/verify_live_site.py`` is inside ``make lint``, ``make format`` and ``make typecheck`` -
they are scoped by path and ``tools`` is in every one of those lists - and it was inside no test.
It is the only gate that reads the bytes a reader receives, it runs unattended on a daily cron,
and a green run of it was a claim nobody had checked. ``tests/test_shipped_code_is_gated.py``
makes exactly this argument about a file the three path-scoped gates could not see; this is the
same argument about the gate a test suite could not see.

Only the pure halves are exercised here: the comparisons, the bounds, and the freshness
arithmetic. Everything that opens a socket is not, deliberately - a test that stood up a TLS
origin would be testing ``http.client``. What made the comparisons testable was extracting
:func:`dataset_csv_differences` from the function that fetched and compared in one step; the
fetch is now three lines around it.

The CSV rows are built from ``data/registry.json`` itself, so the identity half is compared
against the real endpoint set rather than a two-row stand-in, and the row count the check
enforces is the real one.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard import dataset as dataset_module
from fhir_scorecard.published import PublishedGrade
from fhir_scorecard.registry import Endpoint, load_registry

REPO = Path(__file__).resolve().parent.parent


def _load_tool() -> Any:
    """Import ``tools/verify_live_site.py`` by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location(
        "verify_live_site", REPO / "tools" / "verify_live_site.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()

HEADER = [name for name, _description in dataset_module._COLUMNS]

#: A row's probe-derived half, for an endpoint that answered from every vantage. The identity
#: half comes from the registry; these are the columns a probe moves.
ANSWERED = {
    "grade": "A",
    "reachable": "true",
    "reachability_score": "100",
    "transparency_score": "90",
    "interop_score": "95",
    "availability": "answered 14 of 14 checks",
    "last_answered": "2026-09-13",
    "vantages_reached": "3",
    "vantages_reporting": "3",
    "observed_since": "2026-08-01",
    "failure_kinds": "",
}


def _endpoints() -> list[Endpoint]:
    enabled = [e for e in load_registry(REPO / "data" / "registry.json") if e.enabled]
    assert len(enabled) >= TOOL.MINIMUM_ENDPOINTS, "the registry is below the check's own floor"
    return enabled


def _row(endpoint: Endpoint, **overrides: str) -> dict[str, str]:
    cells = dict.fromkeys(HEADER, "")
    cells.update(
        endpoint_id=endpoint.endpoint_id,
        name=endpoint.name,
        kind=endpoint.kind,
        base_url=endpoint.base_url,
        expects_fhir=endpoint.expects,
        verified_method=endpoint.verified_method,
        verified_date=endpoint.verified_date,
        verification_basis=endpoint.verification_basis,
        reverified_date=endpoint.reverified_date,
        **ANSWERED,
    )
    cells.update(overrides)
    return cells


def _csv(rows: list[dict[str, str]], header: list[str] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header or HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _index(rows: list[dict[str, str]]) -> dict[str, PublishedGrade]:
    return {row["endpoint_id"]: PublishedGrade(row["grade"]) for row in rows}


@pytest.fixture
def endpoints() -> list[Endpoint]:
    return _endpoints()


@pytest.fixture
def rows(endpoints: list[Endpoint]) -> list[dict[str, str]]:
    return [_row(endpoint) for endpoint in endpoints]


# ----------------------------------------------------------------------------------
# The positive control, and the identity half that existed before today
# ----------------------------------------------------------------------------------


def test_a_csv_that_matches_the_registry_reports_nothing(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    """Every other case below leans on this, so a difference cannot be a difference for an
    unrelated reason."""
    assert TOOL.dataset_csv_differences(_csv(rows), endpoints, _index(rows)) == []


def test_a_registry_derived_column_that_drifted_is_reported(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    rows[0]["base_url"] = "https://somewhere.else.invalid/r4"
    differences = TOOL.dataset_csv_differences(_csv(rows), endpoints, _index(rows))
    assert len(differences) == 1
    assert "base_url" in differences[0] and rows[0]["endpoint_id"] in differences[0]


def test_a_header_that_is_not_this_checkouts_columns_is_reported(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    """And it is the only thing reported: every later comparison indexes by column name, so
    continuing past a moved header would compare the wrong cells and say so at length."""
    shifted = [*HEADER[1:], HEADER[0]]
    text = _csv([{column: row[column] for column in shifted} for row in rows], shifted)
    differences = TOOL.dataset_csv_differences(text, endpoints, _index(rows))
    assert len(differences) == 1
    assert differences[0].startswith("dataset.csv: header is")


def test_a_missing_row_and_an_unknown_row_are_both_reported(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    dropped = rows[0]["endpoint_id"]
    text = _csv([*rows[1:], _row(endpoints[0], endpoint_id="not-in-the-registry")])
    differences = TOOL.dataset_csv_differences(text, endpoints, _index(rows))
    assert any(f"no row for registry entry {dropped}" in line for line in differences)
    assert any("'not-in-the-registry'" in line for line in differences)


def test_an_empty_csv_cannot_be_read_as_a_clean_one(endpoints: list[Endpoint]) -> None:
    with pytest.raises(TOOL.LiveSiteError):
        TOOL.dataset_csv_differences("", endpoints, {})


def test_a_short_line_is_named_rather_than_indexed_into(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    """A ragged CSV used to reach ``row[index(column)]`` and raise IndexError, which the caller
    would have reported as "the check could not run" rather than as a difference."""
    text = _csv(rows).replace(
        _csv(rows).splitlines()[1], ",".join(rows[0][column] for column in HEADER[:4])
    )
    differences = TOOL.dataset_csv_differences(text, endpoints, _index(rows))
    assert any("field(s), the header has" in line for line in differences)


# ----------------------------------------------------------------------------------
# The grade half, which nothing checked on the live site until now
# ----------------------------------------------------------------------------------


def test_a_score_beside_an_endpoint_no_vantage_reached_is_reported_live(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    """The published defect of 2026-09-12, as the live site would have served it. This check ran
    nightly against that CSV for as long as the defect was up and reported it clean, because it
    read the eight identity columns beside the number and never the number."""
    rows[0].update(
        grade="not observed",
        reachable="false",
        reachability_score="0",
        transparency_score="",
        interop_score="",
        vantages_reached="0",
        vantages_reporting="3",
        failure_kinds="tls",
    )
    differences = TOOL.dataset_csv_differences(_csv(rows), endpoints, _index(rows))
    assert any("GRADE_PUBLISHED_WITHOUT_A_REACH" in line for line in differences)
    assert any("reachability_score" in line for line in differences)


def test_a_letter_withheld_over_a_complete_measurement_is_reported_live(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    rows[0]["grade"] = "not observed"
    differences = TOOL.dataset_csv_differences(_csv(rows), endpoints, _index(rows))
    assert any("GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT" in line for line in differences)


def test_a_grade_the_index_and_the_csv_disagree_about_is_reported(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    """A partial publish: one artifact fresh, the other stale. Nothing compared them before, and
    from inside a checkout there is nothing to compare either of them to."""
    index = _index(rows)
    index[rows[0]["endpoint_id"]] = PublishedGrade("F")
    differences = TOOL.dataset_csv_differences(_csv(rows), endpoints, index)
    assert any("GRADE_SURFACES_DISAGREE" in line for line in differences)


def test_an_index_that_publishes_an_endpoint_the_csv_does_not_is_reported(
    rows: list[dict[str, str]], endpoints: list[Endpoint]
) -> None:
    index = _index(rows)
    index["a-page-that-should-not-exist"] = PublishedGrade("A")
    differences = TOOL.dataset_csv_differences(_csv(rows), endpoints, index)
    assert any("GRADE_SURFACES_DISAGREE" in line for line in differences)


# ----------------------------------------------------------------------------------
# The bounds and the arithmetic, which decide whether the check can be turned off by a typo
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://fhir.chelseakr.com/",
        "https://fhir.chelseakr.com/?x=1",
        "https://fhir.chelseakr.com/#a",
        "https:///",
    ],
)
def test_an_origin_that_is_not_a_canonical_https_root_is_refused(url: str) -> None:
    with pytest.raises(TOOL.LiveSiteError):
        TOOL.Origin(url, timeout_seconds=20.0)


@pytest.mark.parametrize("timeout", [0.0, 0.9, 61.0])
def test_a_timeout_outside_the_bounds_is_refused(timeout: float) -> None:
    with pytest.raises(TOOL.LiveSiteError):
        TOOL.Origin("https://fhir.chelseakr.com/", timeout_seconds=timeout)


@pytest.mark.parametrize("relative", ["/assets/site.css", "a?b=c", "a#b"])
def test_a_relative_path_that_could_leave_the_origin_is_refused(relative: str) -> None:
    origin = TOOL.Origin("https://fhir.chelseakr.com/", timeout_seconds=20.0)
    with pytest.raises(TOOL.LiveSiteError):
        origin.target(relative, "nonce")


def test_a_nonce_is_carried_on_every_request_so_a_cache_cannot_answer_for_the_origin() -> None:
    origin = TOOL.Origin("https://fhir.chelseakr.com/", timeout_seconds=20.0)
    assert origin.target("dataset.csv", "abc123") == "/dataset.csv?live-integrity=abc123"


def test_a_stale_publish_is_reported_and_a_recent_one_is_not() -> None:
    now = dt.datetime.now(dt.UTC)
    assert TOOL.check_freshness(now - dt.timedelta(hours=1), 48.0) == []
    stale = TOOL.check_freshness(now - dt.timedelta(hours=72), 48.0)
    assert len(stale) == 1 and "publish that stopped happening" in stale[0]


def test_a_publish_timestamped_in_the_future_is_not_read_as_fresh() -> None:
    """A future date satisfies a "younger than N hours" comparison forever. It is a broken clock
    or a bad record, and it is the third state a two-state age check does not have."""
    ahead = dt.datetime.now(dt.UTC) + dt.timedelta(hours=6)
    reported = TOOL.check_freshness(ahead, 48.0)
    assert len(reported) == 1 and "in the future" in reported[0]


@pytest.mark.parametrize("stamped", [None, "", "2026-09-13", "yesterday", 20260913])
def test_a_generated_at_that_is_not_a_timestamp_stops_the_check_rather_than_passing(
    stamped: object,
) -> None:
    with pytest.raises(TOOL.LiveSiteError):
        TOOL._generated_at({"generated_at": stamped})


def test_the_committed_asset_tree_is_above_the_floor_the_check_enforces() -> None:
    """The floor exists so a check that compared nothing would fail. It is only a floor if the
    tree is actually above it, which is what this asserts."""
    assert len(TOOL.committed_assets()) >= TOOL.MINIMUM_ASSETS


def test_an_asset_tree_that_is_not_there_stops_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TOOL, "ASSETS", REPO / "src" / "fhir_scorecard" / "no-such-directory")
    with pytest.raises(TOOL.LiveSiteError):
        TOOL.committed_assets()


@pytest.mark.parametrize(
    ("attempts", "retry_seconds"), [(0, 20.0), (11, 20.0), (3, -1.0), (3, 121.0)]
)
def test_a_knob_outside_its_bounds_is_a_usage_error(attempts: int, retry_seconds: float) -> None:
    parser, args = (
        TOOL.argparse.ArgumentParser(),
        TOOL.argparse.Namespace(attempts=attempts, retry_seconds=retry_seconds),
    )
    with pytest.raises(SystemExit):
        TOOL.refuse_unbounded_options(parser, args)
