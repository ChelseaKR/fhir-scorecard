"""The headline numbers must count what their words say they count.

"25 of 31 publisher-documented endpoints answer (81%)" was a line count of a curation file,
published in the present tense on a page regenerated daily: if every endpoint stopped answering
tomorrow, the number would not move, because the only thing that can move it is someone editing
JSON. The README says this is the most citable number in the project, so it is the one that most
needs to be a measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cli import main
from fhir_scorecard.cohort import Cohort, CohortMember, CohortSource
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.site import cohort_page, home_page, kind_page


def _answering(eid: str, kind: str = "payer") -> Scorecard:
    return build_scorecard(
        eid,
        eid.replace("-", " ").title(),
        FetchResult(
            url=f"https://{eid}.test/metadata",
            ok=True,
            status=200,
            elapsed_ms=120,
            body=b"",
            error=None,
        ),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind=kind,
    )


def _silent(eid: str, kind: str = "payer") -> Scorecard:
    return build_scorecard(
        eid,
        eid.replace("-", " ").title(),
        FetchResult(
            url=f"https://{eid}.test/metadata",
            ok=False,
            status=None,
            elapsed_ms=0,
            body=b"",
            error="connection timed out",
        ),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind=kind,
    )


def test_home_headline_counts_endpoints_that_answered_not_registry_rows() -> None:
    cards = [_answering("alpha"), _answering("beta"), _silent("gamma")]
    body = home_page(cards, "https://example.test").body
    assert "<strong>3</strong> endpoints listed" in body
    assert "<strong>2</strong> answered on this run" in body
    # And the page says which is which, because a reader takes the headline away.
    assert "3 is how many endpoints the registry lists and this run graded" in body
    assert "2 is how many answered /metadata during the run that generated this page" in body
    assert "1 was not observed on this run and is not counted as answering" in body


def test_category_page_counts_answers_not_rows() -> None:
    body = kind_page("payer", [_answering("alpha"), _silent("beta")], "https://example.test").body
    assert "<strong>1</strong><span>answered on this run</span>" in body


def _cohort(*members: CohortMember) -> Cohort:
    return Cohort(
        cohort_id="testville",
        name="Testville payer cohort",
        description="Every plan on the public Testville roster.",
        notes=(),
        sources=(CohortSource(label="Roster", url="https://roster.test", date="2026-08-06"),),
        members=members,
    )


def test_cohort_page_separates_the_curated_count_from_the_measured_one() -> None:
    """Two members were verified as publishing a base URL, which is a dated curation record.
    One of them answered today. Both numbers appear, labelled as what they are."""
    cohort = _cohort(
        CohortMember(
            member_id="alpha-plan",
            name="Alpha Plan",
            programs=("medi-cal",),
            endpoint_ids=("alpha",),
        ),
        CohortMember(
            member_id="beta-plan", name="Beta Plan", programs=("medi-cal",), endpoint_ids=("beta",)
        ),
    )
    cards = {"alpha": _answering("alpha"), "beta": _silent("beta")}
    page = cohort_page(cohort, cards, "https://example.test")

    assert "<strong>2</strong><span>endpoints listed</span>" in page.body
    assert "<strong>1</strong><span>answered on this run</span>" in page.body
    assert "1 answered when this page was generated" in page.body
    assert "a curation record with a date on" in page.body
    assert "1 of 2 listed endpoints answered on the latest run" in page.description


def test_cohort_endpoint_count_never_exceeds_the_endpoints_it_can_show() -> None:
    """The count came from ids in the curation file while the table came from graded cards, so
    a listed id with no card inflated the number above the rows beneath it."""
    cohort = _cohort(
        CohortMember(
            member_id="alpha-plan",
            name="Alpha Plan",
            programs=("medi-cal",),
            endpoint_ids=("alpha", "ghost"),
        )
    )
    page = cohort_page(cohort, {"alpha": _answering("alpha")}, "https://example.test")
    assert "<strong>1</strong><span>endpoints listed</span>" in page.body
    assert page.body.count('<td><a href="/fhir-scorecard/endpoint/') == 1


def test_published_api_reports_both_numbers(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": "alpha",
                        "name": "Alpha",
                        "kind": "payer",
                        "base_url": "https://alpha.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-06"},
                    },
                    {
                        "id": "dark",
                        "name": "Dark",
                        "kind": "payer",
                        "base_url": "https://dark.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-06"},
                    },
                ]
            }
        )
    )
    fixtures = tmp_path / "fixtures" / "alpha"
    fixtures.mkdir(parents=True)
    (fixtures / "metadata.json").write_text(json.dumps(good_capability()))
    (fixtures / "smart.json").write_text(json.dumps(good_smart()))

    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(registry),
                "--offline",
                "--fixtures",
                str(tmp_path / "fixtures"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
            ]
        )
        == 0
    )
    index = json.loads((out / "api" / "index.json").read_text())
    assert index["endpoints_listed"] == 2
    assert index["answered_on_this_run"] == 1
