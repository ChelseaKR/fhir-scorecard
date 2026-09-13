"""The published-grade contract must be able to fail, one rule at a time.

Same discipline as ``tests/test_site_audit.py``: every rule in
``fhir_scorecard.published.GRADE_CODES`` gets a case that builds a real site with the documented
offline command, breaks exactly one property, and asserts that rule fires - and the same site,
unbroken, is asserted clean, so a red result cannot be red for an unrelated reason. The union of
the cases is asserted to be every documented code, so a rule cannot ship without one.

Two of the cases are the defects of 2026-09-12 in the shape they were published in: a
``reachability_score`` of 0 beside an endpoint no vantage reached, and a letter withheld over a
complete measurement. Both were on the live site for as long as anyone cared to look, and
neither the site contract, the accessibility rules nor the weight budgets could see either,
because none of them opens the data.

The rendered-page rules are exercised against **rendered pages**, not against the JSON that
feeds them. A sibling lane measured the cost of the other choice: of eighteen negative controls,
four did not fire, and one of those was a "visible on the card" requirement where every row
assertion read the JSON and none read the HTML.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fhir_scorecard import dataset as dataset_module
from fhir_scorecard import grading
from fhir_scorecard.cli import main
from fhir_scorecard.published import (
    DIMENSION_KEYS,
    GRADE_CODES,
    LETTERS,
    NOT_OBSERVED_LITERAL,
    REQUIRED_COLUMNS,
    SCORE_COLUMNS,
    PublishedGrade,
    audit_published_grades,
    audit_rows,
    grade_badges,
    read_endpoint_page,
    rows_as_published,
    surface_differences,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: An endpoint the fixture build reaches and grades, and one no vantage reached. Named rather
#: than discovered, because a case aimed at the unreachable path that silently ran against a
#: reachable endpoint would pass for the wrong reason.
REACHED = "cms-blue-button-2"
UNREACHED = "aspirus-patient-access"


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


def _rows(site: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO((site / "dataset.csv").read_text(encoding="utf-8"))))


def _write_rows(site: Path, rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (site / "dataset.csv").write_text(buffer.getvalue(), encoding="utf-8")


def _changed(site: Path, endpoint_id: str, **cells: str) -> list[dict[str, str]]:
    """The published rows with one endpoint's cells replaced. Asserts the row was found."""
    rows = _rows(site)
    hit = [row for row in rows if row["endpoint_id"] == endpoint_id]
    assert len(hit) == 1, f"no row for {endpoint_id}; the case would examine nothing"
    for column in cells:
        assert column in hit[0], column
    hit[0].update(cells)
    return rows


def _codes(site: Path) -> list[str]:
    return [finding.code for finding in audit_published_grades(site)]


# ----------------------------------------------------------------------------------
# The population these rules run over, asserted before anything about it
# ----------------------------------------------------------------------------------


def test_the_build_publishes_both_populations_before_any_rule_is_asserted(site: Path) -> None:
    """A floor, so no case below can pass by running over nothing.

    Every rule about an unreached endpoint is unreachable from a fixture set where everything
    answers, which is the arrangement under which a published ``reachability_score: 0`` survived
    on 14 live endpoints.
    """
    rows = _rows(site)
    assert len(rows) >= 5
    assert sum(1 for row in rows if row["reachable"] == "true") >= 3
    assert sum(1 for row in rows if row["reachable"] == "false") >= 2
    assert {row["endpoint_id"] for row in rows} >= {REACHED, UNREACHED}
    by_id = {row["endpoint_id"]: row for row in rows}
    assert by_id[REACHED]["reachable"] == "true"
    assert by_id[UNREACHED]["reachable"] == "false"


def test_the_documented_offline_build_satisfies_the_grade_contract(site: Path) -> None:
    """The positive control every other case leans on."""
    assert audit_published_grades(site) == []


# ----------------------------------------------------------------------------------
# The vocabulary this module states rather than imports
# ----------------------------------------------------------------------------------


def test_the_stated_letters_are_every_letter_the_grader_can_band_to() -> None:
    """The module spells its vocabulary out so it cannot agree with the grader by construction.

    That is only safe if something checks the two agree, and this is it: a band the grader can
    produce and this module does not name would make every published row of that letter read as
    a finding, and a letter this module names that the grader cannot produce would be dead.
    """
    assert {grading._band(value) for value in range(0, 101)} == set(LETTERS)
    assert NOT_OBSERVED_LITERAL == grading.NOT_OBSERVED


def test_the_stated_columns_are_columns_the_dataset_actually_publishes() -> None:
    published = [name for name, _description in dataset_module._COLUMNS]
    assert set(REQUIRED_COLUMNS) <= set(published)
    assert list(SCORE_COLUMNS) == [name for name in published if name.endswith("_score")]
    assert tuple(key for key, _title, _weight in grading.WEIGHTED_DIMENSIONS) == DIMENSION_KEYS


# ----------------------------------------------------------------------------------
# The row rules, over rows a real build published
# ----------------------------------------------------------------------------------


def test_a_score_beside_an_endpoint_nobody_reached_is_caught(site: Path) -> None:
    """2026-09-12, exactly: ``reachability_score: 0`` published against a named health insurer
    that no vantage reached. Fourteen endpoints carried it, and the CSV column it was published
    in was read by no gate anywhere."""
    findings = audit_rows(_changed(site, UNREACHED, reachability_score="0"))
    assert [f.code for f in findings] == ["GRADE_PUBLISHED_WITHOUT_A_REACH"]
    assert "reachability_score" in findings[0].detail


def test_a_letter_beside_an_endpoint_nobody_reached_is_caught(site: Path) -> None:
    findings = audit_rows(_changed(site, UNREACHED, grade="F"))
    assert [f.code for f in findings] == ["GRADE_PUBLISHED_WITHOUT_A_REACH"]


def test_a_withheld_letter_over_three_scored_dimensions_is_caught(site: Path) -> None:
    """A shape ``grading.letter`` cannot produce: every dimension scored bounds the weighted
    score to a point, so a band is always pinned. A row in this state was written by something
    downstream of the grader."""
    findings = audit_rows(_changed(site, REACHED, grade=NOT_OBSERVED_LITERAL))
    assert [f.code for f in findings] == ["GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT"]


def test_a_withheld_letter_over_a_partial_measurement_is_not_a_finding(site: Path) -> None:
    """The other side of that rule, and the reason it is not "no letter means trouble".

    An endpoint whose SMART document no vantage retrieved has an unscored interop dimension and
    bounds that can straddle two bands, and publishing no letter is then the honest result. A
    rule that fired here would push the project back toward inventing one.
    """
    assert audit_rows(_changed(site, REACHED, grade=NOT_OBSERVED_LITERAL, interop_score="")) == []


@pytest.mark.parametrize("value", ["", "E", "not-observed", "Not Observed", "A+", "95"])
def test_a_grade_outside_the_published_vocabulary_is_caught(site: Path, value: str) -> None:
    codes = [f.code for f in audit_rows(_changed(site, REACHED, grade=value))]
    assert "GRADE_NOT_IN_THE_PUBLISHED_VOCABULARY" in codes


@pytest.mark.parametrize("value", ["-1", "101", "100.0", "n/a", "0x10"])
def test_a_dimension_cell_that_is_not_a_percentage_is_caught(site: Path, value: str) -> None:
    codes = [f.code for f in audit_rows(_changed(site, REACHED, transparency_score=value))]
    assert "GRADE_SCORE_IS_NOT_A_PERCENTAGE" in codes


def test_a_condition_named_beside_an_endpoint_that_was_reached_is_caught(site: Path) -> None:
    findings = audit_rows(_changed(site, REACHED, failure_kinds="forbidden"))
    assert [f.code for f in findings] == ["GRADE_REACH_IS_NOT_COHERENT"]


def test_a_vantage_that_reached_an_endpoint_published_as_unreached_is_caught(site: Path) -> None:
    findings = audit_rows(_changed(site, UNREACHED, vantages_reached="1", vantages_reporting="3"))
    assert [f.code for f in findings] == ["GRADE_REACH_IS_NOT_COHERENT"]


def test_more_vantages_reaching_than_reporting_is_caught(site: Path) -> None:
    findings = audit_rows(_changed(site, REACHED, vantages_reached="4", vantages_reporting="3"))
    assert [f.code for f in findings] == ["GRADE_REACH_IS_NOT_COHERENT"]


def test_an_unreached_endpoint_that_vantages_asked_about_and_names_no_condition_is_caught(
    site: Path,
) -> None:
    """The third state, held from the side that is easy to lose.

    An endpoint every vantage asked about and none reached has observed something specific and
    must name it. An endpoint **nobody asked about** has not, and its empty condition is correct
    - which is why the rule reads the reporting count rather than firing on an empty cell.
    """
    findings = audit_rows(_changed(site, UNREACHED, failure_kinds="", vantages_reporting="3"))
    assert [f.code for f in findings] == ["GRADE_REACH_IS_NOT_COHERENT"]
    assert audit_rows(_changed(site, UNREACHED, failure_kinds="", vantages_reporting="0")) == []


@pytest.mark.parametrize("value", ["", "TRUE", "yes", "1", "False"])
def test_a_reachable_cell_that_is_not_a_published_boolean_is_caught(site: Path, value: str) -> None:
    codes = [f.code for f in audit_rows(_changed(site, REACHED, reachable=value))]
    assert "GRADE_REACH_IS_NOT_COHERENT" in codes


# ----------------------------------------------------------------------------------
# Vacuity: a check that examined nothing must fail
# ----------------------------------------------------------------------------------


def test_no_rows_is_a_finding_and_not_a_clean_dataset() -> None:
    assert [f.code for f in audit_rows([])] == ["GRADE_NOTHING_EXAMINED"]


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_a_dataset_missing_any_column_these_rules_read_is_a_finding(
    site: Path, column: str
) -> None:
    """Not a KeyError, and not a pass. A CSV whose shape moved is a check that could not run."""
    rows = [{k: v for k, v in row.items() if k != column} for row in _rows(site)]
    findings = audit_rows(rows)
    assert [f.code for f in findings] == ["GRADE_NOTHING_EXAMINED"]
    assert column in findings[0].detail


def test_a_build_with_no_dataset_is_a_finding(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert [f.code for f in audit_published_grades(empty)] == ["GRADE_NOTHING_EXAMINED"]


def test_a_build_with_rows_and_no_per_endpoint_records_is_a_finding(site: Path) -> None:
    """Otherwise the cross-surface rules would compare one surface against nothing and pass."""
    for path in (site / "api" / "endpoint").glob("*.json"):
        path.unlink()
    assert "GRADE_NOTHING_EXAMINED" in _codes(site)


# ----------------------------------------------------------------------------------
# Cross-surface agreement
# ----------------------------------------------------------------------------------


def test_one_surface_alone_is_compared_against_nothing_and_says_so() -> None:
    assert surface_differences({"dataset.csv": {"a": PublishedGrade("A", ("1", "2", "3"))}}) == []


def test_a_letter_that_differs_between_two_surfaces_is_caught(site: Path) -> None:
    _write_rows(site, _changed(site, REACHED, grade="F"))
    findings = audit_published_grades(site)
    assert "GRADE_SURFACES_DISAGREE" in [f.code for f in findings]
    assert any(REACHED in f.detail and "'F'" in f.detail for f in findings)


def test_a_score_that_differs_between_two_surfaces_is_caught(site: Path) -> None:
    _write_rows(site, _changed(site, REACHED, transparency_score="99"))
    assert "GRADE_SURFACES_DISAGREE" in _codes(site)


def test_an_endpoint_one_surface_publishes_and_another_does_not_is_caught(site: Path) -> None:
    (site / "api" / "endpoint" / f"{REACHED}.json").unlink()
    assert "GRADE_SURFACES_DISAGREE" in _codes(site)


def test_a_surface_that_carries_no_scores_is_compared_on_the_letter_alone() -> None:
    """``api/index.json`` publishes a letter and nothing else. ``None`` scores must read as
    "this surface has none to compare", never as an empty list that disagrees with three."""
    reference = {"a": PublishedGrade("A", ("100", "80", "60"))}
    assert (
        surface_differences(
            {"dataset.csv": reference, "api/index.json": {"a": PublishedGrade("A")}}
        )
        == []
    )
    assert [
        f.code
        for f in surface_differences(
            {"dataset.csv": reference, "api/index.json": {"a": PublishedGrade("B")}}
        )
    ] == ["GRADE_SURFACES_DISAGREE"]


def test_the_scorecards_document_is_read_by_dimension_key_not_by_position(site: Path) -> None:
    """A reordered dimension list would compare equal on two of three columns if the scores were
    read positionally, and the third difference could be a legitimate one."""
    path = site / "scorecards.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for record in payload["scorecards"]:
        if record["endpoint_id"] == REACHED:
            record["dimensions"] = list(reversed(record["dimensions"]))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Reversing the list must not move what this reader sees, because it reads by key.
    assert audit_published_grades(site) == []


# ----------------------------------------------------------------------------------
# The rendered page, read as a rendered page
# ----------------------------------------------------------------------------------


def _page(site: Path, endpoint_id: str) -> Path:
    return site / "endpoint" / endpoint_id / "index.html"


def _replace_once(path: Path, old: str, new: str) -> None:
    """Replace one occurrence, refusing if there is not exactly one.

    A mutation that silently matched nothing, or matched somewhere else on the page, reads as a
    rule that did not fire.
    """
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


#: Where the card's own dimension meters live. An endpoint page repeats each meter further down,
#: inside the findings section, so a whole-file replace hits the *first* match and a whole-file
#: uniqueness assertion refuses a mutation that is unique where it matters. Both mutations and
#: assertions are scoped to this region.
_OVERVIEW = '<section class="score-overview"'


def _replace_once_on_the_card(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(_OVERVIEW)
    end = text.index("</section>", start) + len("</section>")
    region = text[start:end]
    assert region.count(old) == 1, f"{old!r} occurs {region.count(old)} times on the card"
    path.write_text(text[:start] + region.replace(old, new, 1) + text[end:], encoding="utf-8")


def test_the_reader_reads_the_grade_a_real_page_renders(site: Path) -> None:
    """The positive control for the HTML reader itself. A reader that returned ``None`` for every
    page would make every page rule below fire for a reason that is not the page's."""
    badge, meters = read_endpoint_page(_page(site, REACHED).read_text(encoding="utf-8"))
    assert badge == "B"
    assert [title for title, _value, _unscored in meters] == [
        title for _key, title, _weight in grading.WEIGHTED_DIMENSIONS
    ]
    assert [value for _title, value, _unscored in meters] == ["100", "100", "60"]
    assert not any(unscored for _title, _value, unscored in meters)

    badge, meters = read_endpoint_page(_page(site, UNREACHED).read_text(encoding="utf-8"))
    assert badge == NOT_OBSERVED_LITERAL
    assert all(unscored for _title, _value, unscored in meters)


def test_a_page_showing_a_letter_the_data_does_not_publish_is_caught(site: Path) -> None:
    _replace_once(_page(site, REACHED), ">B</span>", ">A</span>")
    findings = audit_published_grades(site)
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "'A'" in findings[0].detail and "'B'" in findings[0].detail


def test_a_page_showing_a_dimension_score_the_data_does_not_publish_is_caught(
    site: Path,
) -> None:
    _replace_once_on_the_card(_page(site, REACHED), "<strong>60</strong>", "<strong>90</strong>")
    assert [f.code for f in audit_published_grades(site)] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]


def test_a_page_scoring_a_dimension_the_data_did_not_measure_is_caught(site: Path) -> None:
    """The rendered half of the 2026-09-12 defect: a number on the card for a check no vantage
    was able to make."""
    page = _page(site, UNREACHED)
    _replace_once_on_the_card(
        page,
        '<div class="dimension-meter dimension-meter-unscored"><div><span>Reachability</span>'
        "<strong>no answer</strong></div>",
        '<div class="dimension-meter"><div><span>Reachability</span><strong>0</strong></div>',
    )
    findings = audit_published_grades(page.parents[2])
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "does not score it at all" in findings[0].detail


def test_a_page_with_no_hero_grade_at_all_is_caught(site: Path) -> None:
    _replace_once(_page(site, REACHED), 'class="hero-grade"', 'class="hero-grade-removed"')
    assert [f.code for f in audit_published_grades(site)] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]


def test_a_page_rendering_fewer_meters_than_the_data_carries_is_caught(site: Path) -> None:
    _replace_once_on_the_card(
        _page(site, REACHED),
        '<div class="dimension-meter"><div><span>Interop readiness</span><strong>60</strong></div>',
        '<div class="dimension-meter-dropped"><div><span>Interop readiness</span>'
        "<strong>60</strong></div>",
    )
    findings = audit_published_grades(site)
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "2 dimension meter(s)" in findings[0].detail


def test_an_endpoint_with_data_and_no_page_is_caught(site: Path) -> None:
    _page(site, REACHED).unlink()
    assert [f.code for f in audit_published_grades(site)] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]


def _report(site: Path, endpoint_id: str) -> Path:
    return site / "endpoint" / endpoint_id / "report" / "index.html"


def test_the_report_page_publishes_the_same_grade_as_the_data(site: Path) -> None:
    """The positive control for the report rule. The report renders a badge and no hero block,
    so ``read_endpoint_page`` sees nothing on it and only ``grade_badges`` does - which is
    precisely how a page can carry a published grade that no gate reads."""
    assert read_endpoint_page(_report(site, REACHED).read_text(encoding="utf-8"))[0] is None
    assert grade_badges(_report(site, REACHED).read_text(encoding="utf-8")) == ["B"]
    assert grade_badges(_report(site, UNREACHED).read_text(encoding="utf-8")) == [
        NOT_OBSERVED_LITERAL
    ]


def test_a_report_showing_a_grade_the_data_does_not_publish_is_caught(site: Path) -> None:
    _replace_once(_report(site, REACHED), ">B</span>", ">A</span>")
    findings = audit_published_grades(site)
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "report" in findings[0].where


def test_a_report_that_renders_no_grade_is_caught(site: Path) -> None:
    _replace_once(_report(site, REACHED), 'class="grade grade-b"', 'class="badge-b"')
    findings = audit_published_grades(site)
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "renders no grade at all" in findings[0].detail


def test_an_endpoint_with_data_and_no_report_page_is_caught(site: Path) -> None:
    _report(site, REACHED).unlink()
    findings = audit_published_grades(site)
    assert [f.code for f in findings] == ["GRADE_PAGE_DISAGREES_WITH_THE_DATA"]
    assert "has no report page" in findings[0].detail


def test_the_reader_only_reads_the_score_overview_meters(site: Path) -> None:
    """An endpoint page repeats each dimension meter inside its findings section. Reading those
    too would make the meter count three larger than the data carries on every clean build,
    which is exactly what the first run of this rule reported."""
    text = _page(site, REACHED).read_text(encoding="utf-8")
    assert text.count('class="dimension-meter') > len(SCORE_COLUMNS)
    _badge, meters = read_endpoint_page(text)
    assert len(meters) == len(SCORE_COLUMNS)


# ----------------------------------------------------------------------------------
# Every documented code has a case, and no case emits an undocumented one
# ----------------------------------------------------------------------------------


def _break_row(endpoint_id: str, **cells: str) -> Callable[[Path], None]:
    """A mutation that rewrites one endpoint's row. The endpoint is named at the call site,
    because a case aimed at the unreachable path that ran against a reachable endpoint would
    pass for the wrong reason - and ``_changed`` asserts the row exists."""

    def mutate(site: Path) -> None:
        _write_rows(site, _changed(site, endpoint_id, **cells))

    return mutate


def _drop_dataset(site: Path) -> None:
    (site / "dataset.csv").unlink()


def _empty_dataset(site: Path) -> None:
    (site / "dataset.csv").write_text(
        ",".join(name for name, _ in dataset_module._COLUMNS) + "\n", encoding="utf-8"
    )


def _drop_a_card(site: Path) -> None:
    (site / "api" / "endpoint" / f"{REACHED}.json").unlink()


def _break_the_page(site: Path) -> None:
    _replace_once(_page(site, REACHED), ">B</span>", ">F</span>")


#: One case per documented code. ``test_every_documented_code_has_a_case`` asserts this covers
#: :data:`GRADE_CODES` exactly, so adding a rule without a case fails rather than shipping
#: unexercised.
CASES: tuple[tuple[str, Callable[[Path], None]], ...] = (
    ("GRADE_NOT_IN_THE_PUBLISHED_VOCABULARY", _break_row(REACHED, grade="E")),
    ("GRADE_SCORE_IS_NOT_A_PERCENTAGE", _break_row(REACHED, reachability_score="n/a")),
    ("GRADE_PUBLISHED_WITHOUT_A_REACH", _break_row(UNREACHED, reachability_score="0")),
    (
        "GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT",
        _break_row(REACHED, grade=NOT_OBSERVED_LITERAL),
    ),
    ("GRADE_REACH_IS_NOT_COHERENT", _break_row(UNREACHED, vantages_reached="2")),
    ("GRADE_SURFACES_DISAGREE", _drop_a_card),
    ("GRADE_PAGE_DISAGREES_WITH_THE_DATA", _break_the_page),
    ("GRADE_NOTHING_EXAMINED", _empty_dataset),
)


def test_every_documented_code_has_a_case() -> None:
    assert {code for code, _mutation in CASES} == set(GRADE_CODES)


@pytest.mark.parametrize(("code", "mutation"), CASES, ids=[code for code, _ in CASES])
def test_each_case_fires_its_rule_and_emits_no_undocumented_code(
    site: Path, code: str, mutation: Callable[[Path], None]
) -> None:
    assert audit_published_grades(site) == [], "the case starts from a clean build"
    mutation(site)
    findings = audit_published_grades(site)
    emitted = {finding.code for finding in findings}
    assert code in emitted, f"{code} did not fire; emitted {sorted(emitted)}"
    assert emitted <= set(GRADE_CODES), sorted(emitted - set(GRADE_CODES))


def test_a_dropped_dataset_is_one_finding_and_not_a_crash(site: Path) -> None:
    _drop_dataset(site)
    assert [f.code for f in audit_published_grades(site)] == ["GRADE_NOTHING_EXAMINED"]


def test_rows_as_published_skips_a_row_with_no_identity(site: Path) -> None:
    rows = _rows(site)
    rows.append(dict.fromkeys(rows[0], ""))
    assert set(rows_as_published(rows)) == {
        row["endpoint_id"] for row in rows if row["endpoint_id"]
    }
