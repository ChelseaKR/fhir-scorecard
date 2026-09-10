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
from fhir_scorecard.vantage import VantageProbe, reconcile


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


def _answered_nothing_to_grade(eid: str) -> Scorecard:
    """Reachable, and ungradable. A vantage reached /metadata and came away with no document.

    HTTP 200 with an empty body is the everyday cause, and the `--from-probes` publishing run
    reproduces it whenever a peer artifact reports `reachable` without carrying the capability
    text. The card is genuinely in both states, so a sentence that treats "not observed" and
    "did not answer" as one set says something false about it.
    """
    probes = [VantageProbe(vantage="github-actions/ubuntu-latest", reachable=True, elapsed_ms=210)]
    consensus = reconcile(probes)
    return build_scorecard(
        eid,
        eid.replace("-", " ").title(),
        FetchResult(
            url=f"https://{eid}.test/metadata",
            ok=True,
            status=200,
            elapsed_ms=210,
            body=b"",
            error=None,
        ),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
    )


def test_an_endpoint_that_answered_with_nothing_is_in_both_states_and_the_page_says_so() -> None:
    card = _answered_nothing_to_grade("hollow")
    assert card.reachable and card.grade == "not observed", (
        "the premise is gone: this card is supposed to be reachable and ungraded at once"
    )

    body = home_page([_answering("alpha"), card], "https://example.test").body
    assert "<strong>2</strong> answered on this run" in body
    assert "1 answered but returned nothing this run could grade" in body
    assert "counted as answering and still carries no grade" in body
    # The old sentence counted it as ungraded and then denied it had answered, in the same breath.
    assert "1 was not observed on this run and is not counted as answering" not in body


def test_the_two_ungraded_reasons_are_reported_separately() -> None:
    body = home_page(
        [_answering("alpha"), _silent("gamma"), _answered_nothing_to_grade("hollow")],
        "https://example.test",
    ).body
    assert "<strong>2</strong> answered on this run" in body
    assert "1 was not observed on this run and is not counted as answering" in body
    assert "1 answered but returned nothing this run could grade" in body


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
    assert page.body.count('<td><a href="/endpoint/') == 1


def test_one_endpoint_two_plans_publish_through_is_counted_once() -> None:
    """Measured in the shipped curation, not hypothesised.

    ``data/cohorts/florida-marketplace.json`` lists Cigna Healthcare and Cigna Healthcare of
    Florida as two member organizations pointing at ``cigna-patientaccess`` and
    ``cigna-provider-directory``, and Florida Blue and Florida Blue HMO likewise, so the page
    counted (member, endpoint) rows and published **17 endpoints listed** over thirteen
    endpoints, with four of them counted twice in **answered on this run**.
    ``michigan-marketplace`` has one such pair.

    The row per member stays: a plan that publishes through another entity's server is still
    that plan's answer to the rule, and a reader looking for their own plan has to find it. It
    is the count labelled "endpoints" that has to be a count of endpoints.
    """
    cohort = _cohort(
        CohortMember(
            member_id="alpha-plan",
            name="Alpha Plan",
            programs=("medi-cal",),
            endpoint_ids=("shared",),
        ),
        CohortMember(
            member_id="alpha-plan-hmo",
            name="Alpha Plan HMO",
            programs=("medi-cal",),
            endpoint_ids=("shared",),
        ),
    )
    page = cohort_page(cohort, {"shared": _answering("shared")}, "https://example.test")

    assert "<strong>1</strong><span>endpoints listed</span>" in page.body
    assert "<strong>1</strong><span>answered on this run</span>" in page.body
    assert "1 of 1 listed endpoints answered on the latest run" in page.description
    # Both organizations still appear, each under its own name.
    assert page.body.count('<td><a href="/endpoint/shared/"') == 2
    assert "Alpha Plan HMO" in page.body
    # And the page says why the two numbers differ, rather than leaving a reader to count rows.
    assert "The table below has 2 rows rather than 1" in page.body
    assert "a second member organization publishing through a surface already counted" in page.body


def test_a_cohort_with_no_shared_surface_says_nothing_about_rows() -> None:
    """The other direction. A note that appeared on every cohort would be noise, and a reader
    who saw it everywhere would stop reading it where it is load-bearing."""
    cohort = _cohort(
        CohortMember(
            member_id="alpha-plan",
            name="Alpha Plan",
            programs=("medi-cal",),
            endpoint_ids=("alpha",),
        ),
        CohortMember(
            member_id="beta-plan",
            name="Beta Plan",
            programs=("medi-cal",),
            endpoint_ids=("beta",),
        ),
    )
    cards = {"alpha": _answering("alpha"), "beta": _answering("beta")}
    page = cohort_page(cohort, cards, "https://example.test")
    assert "<strong>2</strong><span>endpoints listed</span>" in page.body
    assert "The table below has" not in page.body


def test_the_shipped_cohorts_are_the_reason_this_rule_exists() -> None:
    """A regression test over the real curation files, so the finding cannot quietly expire.

    If the duplicates are edited out of the roster one day this test says so rather than
    silently becoming a statement about nothing - which is what a rule written only against a
    synthetic fixture would become.
    """
    from fhir_scorecard.cohort import load_cohort_dir
    from fhir_scorecard.registry import load_registry

    root = Path(__file__).resolve().parent.parent
    endpoints = load_registry(root / "data" / "registry.json")
    cohorts = load_cohort_dir(
        root / "data" / "cohorts", frozenset(e.endpoint_id for e in endpoints)
    )
    sharing = {
        cohort.cohort_id: (len(refs), len(set(refs)))
        for cohort in cohorts
        for refs in [[eid for m in cohort.included for eid in m.endpoint_ids]]
        if len(refs) != len(set(refs))
    }
    assert sharing == {"florida-marketplace": (17, 13), "michigan-marketplace": (6, 5)}, sharing


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
