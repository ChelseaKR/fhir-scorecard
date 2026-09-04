"""The coverage tracker, and the merge it must never perform.

`docs/SAMPLING-FRAME.md` states the rule this module exists to keep: *"not yet reviewed - a
fact about this project, never rendered as 'publishes nothing', which is a fact about an issuer
that only a completed review can establish. The two must never be merged."*

The tests below hold that from both directions. A rate over reviewed organizations is computed
and checked against the committed data; a rate over any set containing an unreviewed
organization is refused, and the refusal is asserted rather than assumed.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from fhir_scorecard.cohort import Cohort, load_cohort_dir
from fhir_scorecard.coverage import (
    COVERAGE_PATH,
    DOCUMENTED_UNREACHABLE,
    NO_PUBLIC_URL_FOUND,
    NOT_YET_REVIEWED,
    POPULATIONS,
    REVIEWED_POPULATIONS,
    ROSTER_SUFFIX,
    VERIFIED,
    FrameOrg,
    classify,
    counts,
    page,
    publishing_rate,
    read_frame,
    read_reviewed_rows_by_cohort,
)
from fhir_scorecard.registry import load_registry
from fhir_scorecard.site import DEFAULT_ORIGIN

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FRAME_CSV = DATA / "frames" / "qhp-landscape-py2026-individual-medical.csv"
COHORT_DIR = DATA / "cohorts"

#: The states whose issuers have actually been reviewed. Named once so the join-key test
#: cannot silently drift out of step with the cohorts on disk.
REVIEWED_STATES = {"TX", "FL", "OH", "WI", "AZ", "MI", "MO", "OK", "IA", "KS", "LA", "NC"}


def _text(markup: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _cohorts() -> tuple[Cohort, ...]:
    endpoints = [e for e in load_registry(DATA / "registry.json") if e.enabled]
    return load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in endpoints))


def _cohort(cohorts: tuple[Cohort, ...], cohort_id: str) -> Cohort:
    return next(cohort for cohort in cohorts if cohort.cohort_id == cohort_id)


def _committed() -> list[FrameOrg]:
    """Classification of the frame this repository ships, built from committed files only."""
    endpoints = [e for e in load_registry(DATA / "registry.json") if e.enabled]
    cohorts = load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in endpoints))
    return classify(
        read_frame(FRAME_CSV), cohorts, endpoints, read_reviewed_rows_by_cohort(COHORT_DIR)
    )


# --- the committed frame, recomputed rather than typed ---


def test_the_frame_is_the_denominator_the_sampling_frame_document_states() -> None:
    frame = read_frame(FRAME_CSV)
    assert len(frame) == 176
    assert len({state for state, _ in frame}) == 30


def test_every_frame_row_lands_in_exactly_one_population() -> None:
    orgs = _committed()
    assert len(orgs) == len(read_frame(FRAME_CSV))
    tally = counts(orgs)
    assert set(tally) == set(POPULATIONS)
    assert sum(tally.values()) == len(orgs)
    assert len({(org.state, org.roster_name) for org in orgs}) == len(orgs)


def test_the_reviewed_population_is_the_states_that_were_reviewed() -> None:
    """The measurement `docs/SAMPLING-FRAME.md` publishes: 4 of 30 states, 53 of 176
    organizations reviewed."""
    orgs = _committed()
    reviewed = [org for org in orgs if org.reviewed]
    assert len(reviewed) == 105
    assert {org.state for org in reviewed} == REVIEWED_STATES
    assert counts(orgs)[NOT_YET_REVIEWED] == 71


def test_the_reviewed_outcomes_match_the_cohorts_they_come_from() -> None:
    """67 of the 105 reviewed organizations publish a base URL, across twelve states.

    Each population asserted on its own. This used to assert only that ``verified`` and
    ``documented_unreachable`` *sum* to 15 - which is the population merge this project's whole
    method forbids, sitting in the test guarding the two numbers it most needed to keep apart.
    Flipping one ``verification_basis`` moved the split from 13/2 to 11/4 with the suite green
    and both README and ROADMAP silently wrong.
    """
    tally = counts(_committed())
    assert tally[VERIFIED] == 61
    assert tally[DOCUMENTED_UNREACHABLE] == 6
    assert tally[NO_PUBLIC_URL_FOUND] == 38
    assert tally[NOT_YET_REVIEWED] == 71


# --- the join key ---


def test_reviewedness_is_a_property_of_the_state_and_the_issuer_together() -> None:
    """A national carrier appears once per state it sells in. Joining on the issuer name alone
    credited 23 states with a review only Texas and Florida received, and would have published
    an Alabama issuer's status on the strength of reading a Texas issuer's portal."""
    frame = read_frame(FRAME_CSV)
    names_reviewed = {name for state, name in frame if state in REVIEWED_STATES}
    shared = [
        (state, name)
        for state, name in frame
        if name in names_reviewed and state not in REVIEWED_STATES
    ]
    assert shared, "the frame should contain issuer names that appear outside TX and FL"
    orgs = {(org.state, org.roster_name): org for org in _committed()}
    for row in shared:
        assert orgs[row].population == NOT_YET_REVIEWED, row


def test_the_reviewed_rows_come_from_the_committed_roster_files() -> None:
    """And stay attributed to the cohort whose roster carries them, which is what lets a review
    be published against the right row rather than against any row sharing a name."""
    by_cohort = read_reviewed_rows_by_cohort(COHORT_DIR)
    assert set(by_cohort) == {
        f"{s}-marketplace"
        for s in (
            "arizona",
            "florida",
            "iowa",
            "kansas",
            "louisiana",
            "michigan",
            "missouri",
            "north-carolina",
            "ohio",
            "oklahoma",
            "texas",
            "wisconsin",
        )
    }
    assert len({row for rows in by_cohort.values() for row in rows}) == 105
    for roster in sorted(COHORT_DIR.glob("*" + ROSTER_SUFFIX)):
        with roster.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                assert (row["state_code"], row["issuer_name"]) in by_cohort[
                    roster.name[: -len(ROSTER_SUFFIX)]
                ]


def test_a_name_in_two_cohorts_publishes_each_cohorts_own_review() -> None:
    """The defect the (state, name) key exists to prevent, asserted on the published sentence.

    ``Cigna Healthcare``, ``Molina Healthcare`` and ``UnitedHealthcare`` are roster names in both
    the Texas and the Florida cohort. ``load_cohort_dir`` sorts by filename, so a member map keyed
    on the name alone gave every one of them Texas's member, and Florida's rows published Texas's
    review. Molina's two exclusion reasons are findings about two different developer portals.
    """
    orgs = {(org.state, org.roster_name): org for org in _committed()}
    cohorts = _cohorts()
    shared = {m.roster_name for m in _cohort(cohorts, "texas-marketplace").members} & {
        m.roster_name for m in _cohort(cohorts, "florida-marketplace").members
    }
    assert shared - {""} == {"Cigna Healthcare", "Molina Healthcare", "UnitedHealthcare"}

    reasons = {
        cohort_id: _cohort(cohorts, cohort_id + "-marketplace")
        for cohort_id in ("texas", "florida")
    }
    molina = {
        state: next(m for m in cohort.members if m.roster_name == "Molina Healthcare")
        for state, cohort in reasons.items()
    }
    assert molina["texas"].exclusion is not None and molina["florida"].exclusion is not None
    assert molina["texas"].exclusion.reason != molina["florida"].exclusion.reason

    for state, cohort_id in (("TX", "texas-marketplace"), ("FL", "florida-marketplace")):
        for member in _cohort(cohorts, cohort_id).members:
            if member.roster_name and member.exclusion is not None:
                assert orgs[(state, member.roster_name)].detail == member.exclusion.reason


def test_a_cohort_without_a_roster_file_reviews_no_frame_rows() -> None:
    """California's frame is a state program roster, not a slice of the federal file. Its
    members must not mark any federal-frame row reviewed."""
    endpoints = [e for e in load_registry(DATA / "registry.json") if e.enabled]
    cohorts = load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in endpoints))
    california = next(c for c in cohorts if c.cohort_id == "california")
    assert california.members
    assert all(not member.roster_name for member in california.members)


# --- the merge that must be impossible ---


def test_a_rate_over_reviewed_organizations_is_computed() -> None:
    reviewed = [org for org in _committed() if org.reviewed]
    verified, denominator = publishing_rate(reviewed)
    assert (verified, denominator) == (61, 105)


def test_a_rate_over_the_whole_frame_is_refused() -> None:
    """The load-bearing refusal. A denominator mixing "we looked and found nothing" with
    "nobody looked" is not a coverage rate, so it is made unrepresentable rather than
    discouraged."""
    with pytest.raises(ValueError, match="unreviewed"):
        publishing_rate(_committed())


def test_the_refusal_fires_on_a_single_unreviewed_organization() -> None:
    reviewed = [org for org in _committed() if org.reviewed]
    with pytest.raises(ValueError, match="1 unreviewed organization"):
        publishing_rate([*reviewed, FrameOrg("AK", "Somebody", NOT_YET_REVIEWED, "")])


def test_the_refusal_names_what_it_refused_over() -> None:
    with pytest.raises(ValueError) as caught:
        publishing_rate([FrameOrg("AK", "Premera", NOT_YET_REVIEWED, "")])
    assert "AK" in str(caught.value) and "Premera" in str(caught.value)


def test_an_unreviewed_organization_is_never_in_a_reviewed_population() -> None:
    for org in _committed():
        if org.population == NOT_YET_REVIEWED:
            assert org.population not in REVIEWED_POPULATIONS
            assert not org.reviewed


# --- classification rules ---


def test_an_organization_with_one_answering_surface_is_verified() -> None:
    """An organization is counted on its best-evidenced surface. Publishing a Provider
    Directory that answers is a checkable endpoint whatever the Patient Access URL did, and
    counting the organization twice would break the frame's denominator."""
    orgs = _committed()
    verified = [org for org in orgs if org.population == VERIFIED]
    assert verified
    assert any("of" in org.detail for org in verified)


def test_an_organization_whose_only_surfaces_are_documented_is_not_verified() -> None:
    documented = [org for org in _committed() if org.population == DOCUMENTED_UNREACHABLE]
    assert documented, "the committed frame should contain documented-unreachable issuers"
    for org in documented:
        assert "not retrievable" in org.detail


def test_an_excluded_member_carries_the_reason_the_review_recorded() -> None:
    for org in _committed():
        if org.population == NO_PUBLIC_URL_FOUND:
            assert org.detail, f"{org.roster_name} should carry its recorded reason"


# --- the page ---


def test_the_page_states_the_reviewed_fraction_beside_any_rate() -> None:
    body = _text(page(_committed(), DEFAULT_ORIGIN).body)
    assert "Of the 105 organizations reviewed so far, 61 publish a base URL" in body
    assert "105 of 176 organizations, in 12 of 30 states" in body
    assert "The other 71 have not been looked at" in body


def test_the_page_never_prints_a_population_total_that_includes_the_unreviewed() -> None:
    """176 may appear as the size of the frame. It must never appear as the denominator of a
    publishing outcome."""
    body = _text(page(_committed(), DEFAULT_ORIGIN).body)
    for forbidden in (
        "of 176 publish",
        "61 of 176",
        "38 of 176",
        "publish a base URL this project retrieved a conformance document from. That figure is over 176",
    ):
        assert forbidden not in body


def test_the_page_names_every_population_including_the_empty_ones() -> None:
    body = _text(page(_committed(), DEFAULT_ORIGIN).body)
    for population in POPULATIONS:
        assert population.replace("_", " ") in body


def test_a_frame_with_nothing_reviewed_says_so_rather_than_reporting_zero_coverage() -> None:
    orgs = [FrameOrg("AK", "A", NOT_YET_REVIEWED, ""), FrameOrg("AL", "B", NOT_YET_REVIEWED, "")]
    body = _text(page(orgs, DEFAULT_ORIGIN).body)
    assert "No state on this frame has been reviewed yet" in body
    assert "0%" not in body


def test_a_population_with_no_members_says_so_rather_than_rendering_an_empty_list() -> None:
    orgs = [FrameOrg("TX", "Only One", VERIFIED, "1 of 1 listed surfaces answered")]
    body = _text(page(orgs, DEFAULT_ORIGIN).body)
    assert (
        body.count("No organization on the reviewed part of the frame is in this population.") == 2
    )


def test_the_state_table_accounts_for_every_frame_row() -> None:
    orgs = _committed()
    body = page(orgs, DEFAULT_ORIGIN).body
    for state in sorted({org.state for org in orgs}):
        assert f'<th scope="row">{state}</th>' in body


# --- the build ---


def test_the_build_writes_the_coverage_page_when_it_has_a_frame(tmp_path: Path) -> None:
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
                str(DATA / "registry.json"),
                "--cohorts",
                str(COHORT_DIR),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    assert (out / COVERAGE_PATH / "index.html").is_file()
    assert 'href="/coverage/"' in (out / "index.html").read_text(encoding="utf-8")


def test_a_build_with_no_frame_omits_the_page_and_does_not_link_it(tmp_path: Path) -> None:
    """Absent, not empty. A coverage page whose denominator is missing would report zero in
    every population, which reads as a measured result and is not one - and a nav link to a
    page that was not built is the dead link the site contract exists to catch."""
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
    assert not (out / COVERAGE_PATH).exists()
    assert 'href="/coverage/"' not in (out / "index.html").read_text(encoding="utf-8")


def test_the_coverage_page_satisfies_every_gate(tmp_path: Path) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.cli import main
    from fhir_scorecard.weight import audit_weight

    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(ROOT / "tests" / "fixtures"),
                "--registry",
                str(DATA / "registry.json"),
                "--cohorts",
                str(COHORT_DIR),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    assert audit_site(out, DEFAULT_ORIGIN) == []
    assert audit_accessibility(out) == []
    assert audit_weight(out) == []
