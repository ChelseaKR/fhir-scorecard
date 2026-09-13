"""Why an endpoint was not reached, counted as populations rather than merged into one (#117).

`fetch.FAILURE_KINDS` has carried the condition a retrieval ended in since PR #120, and the
endpoint page, `dataset.csv` and `api/endpoint/<id>.json` have published it per endpoint since.
What no surface could do was *count* it. `/coverage/` and every cohort page published one
"did not answer" figure covering an endpoint that answered HTTP 401 and an endpoint whose
certificate does not verify, which are different facts about different things, and one of them
is a fact about an organization while the other is a fact about a record.

Measured on the live site on 2026-09-13, before this shipped: 15 of the 81 published endpoints
were not reached; 15 of those 15 published the reason on their own page and in their own JSON
and CSV row; 0 of 15 had that reason counted anywhere. This module holds the second number.

**What is deliberately not decided here.** Whether requiring credentials is a finding about the
organization that requires them is open in this repository -- `data/CANDIDATES.md` reads a 401
as a choice worth stating plainly, `docs/SAMPLING-FRAME.md` §4 reads it as a defect in the
public record -- and none of the wording under test takes a side. The assertions below check
that the conditions are kept apart and that no published sentence claims an intent; they do not
check which reading is right, because that is the maintainer's to settle and the split does not
depend on it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fhir_scorecard.archive import Observation, Record, history_json, record_page
from fhir_scorecard.cohort import Cohort, CohortMember, load_cohort_dir
from fhir_scorecard.conditions import (
    ANSWERED_AND_DECLINED,
    ANSWERED_NOT_THE_DOCUMENT,
    CONDITION_DISAGREED,
    CONDITION_HEADINGS,
    CONDITION_NOT_OBSERVED,
    CONDITION_OF_KIND,
    CONDITION_UNCLASSIFIED,
    CONDITIONS,
    NO_ANSWER,
    SIDES,
    condition_of,
)
from fhir_scorecard.coverage import (
    DOCUMENTED_UNREACHABLE,
    FrameOrg,
    classify,
    condition_counts,
    counts,
    read_frame,
    read_reviewed_rows_by_cohort,
    subtotal,
    unreachable_surfaces,
)
from fhir_scorecard.coverage import page as coverage_page
from fhir_scorecard.drift import observation_kinds, observe
from fhir_scorecard.fetch import FAILURE_KINDS, FetchResult
from fhir_scorecard.grading import Scorecard, failure_kinds_of
from fhir_scorecard.over_time import page as over_time_page
from fhir_scorecard.over_time import sections
from fhir_scorecard.registry import load_registry
from fhir_scorecard.site import DEFAULT_ORIGIN, cohort_page

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FRAME_CSV = DATA / "frames" / "qhp-landscape-py2026-individual-medical.csv"
COHORT_DIR = DATA / "cohorts"

#: Kinds this project would read as the endpoint answering and declining the request.
DECLINING = ("authentication_required", "forbidden")

#: Kinds this project would read as the public record producing no document.
NO_DOCUMENT = ("not_found", "server_error", "dns", "tls", "timeout", "connection_refused")


def _text(markup: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", markup).split())


def _card(endpoint_id: str, kinds: tuple[str, ...], kind: str = "payer") -> Scorecard:
    return Scorecard(
        endpoint_id=endpoint_id,
        name=endpoint_id.replace("-", " ").title(),
        grade="not observed",
        reachable=not kinds,
        dimensions=(),
        kind=kind,
        failure_kinds=kinds,
    )


# --- the vocabulary ------------------------------------------------------------------------


def test_every_failure_kind_is_grouped_exactly_once() -> None:
    """The two vocabularies are held in step, in both directions.

    A kind added to `fetch.FAILURE_KINDS` and not grouped here cannot be counted, and the page
    would publish a condition table quietly missing a population. A group key that is not a
    kind is a label nothing can ever land in, which reads as a measured zero.
    """
    assert set(CONDITION_OF_KIND) == set(FAILURE_KINDS)
    assert set(CONDITION_OF_KIND.values()) <= set(CONDITIONS)


def test_every_condition_has_a_heading_a_sentence_and_a_side() -> None:
    assert set(CONDITION_HEADINGS) == set(CONDITIONS) == set(SIDES)
    for condition, meaning in CONDITIONS.items():
        assert meaning and meaning[0].islower(), condition
        assert CONDITION_HEADINGS[condition].strip()


def test_the_two_sides_the_issue_names_are_kept_apart() -> None:
    """A 401 and a 404 are on different sides, and nothing in the map crosses them."""
    assert {SIDES[CONDITION_OF_KIND[kind]] for kind in DECLINING} == {"declined"}
    assert {SIDES[CONDITION_OF_KIND[kind]] for kind in NO_DOCUMENT} == {"no_document"}
    assert SIDES[CONDITION_UNCLASSIFIED] == "neither"
    assert SIDES[CONDITION_DISAGREED] == "neither"
    assert SIDES[CONDITION_NOT_OBSERVED] == "neither"


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        ((), CONDITION_NOT_OBSERVED),
        (("authentication_required",), ANSWERED_AND_DECLINED),
        (("forbidden",), ANSWERED_AND_DECLINED),
        (("authentication_required", "forbidden"), ANSWERED_AND_DECLINED),
        (("not_found",), ANSWERED_NOT_THE_DOCUMENT),
        (("server_error", "not_found"), ANSWERED_NOT_THE_DOCUMENT),
        (("tls",), NO_ANSWER),
        (("dns", "timeout", "redirect_refused"), NO_ANSWER),
        (("unclassified",), CONDITION_UNCLASSIFIED),
        (("forbidden", "tls"), CONDITION_DISAGREED),
        (("unclassified", "forbidden"), CONDITION_DISAGREED),
    ],
)
def test_a_surfaces_condition_is_read_from_every_kind_its_vantages_reported(
    kinds: tuple[str, ...], expected: str
) -> None:
    """Including the case the issue names: three vantages, three kinds, one disagreement."""
    assert condition_of(kinds) == expected


def test_disagreeing_vantages_are_never_resolved_to_one_of_them() -> None:
    assert condition_of(("forbidden", "not_found", "tls")) == CONDITION_DISAGREED
    assert CONDITION_DISAGREED not in CONDITION_OF_KIND.values()


def test_a_kind_this_module_does_not_know_raises_rather_than_landing_in_unclassified() -> None:
    """`unclassified` means a run could not classify a failure. It is not a default.

    Filing an ungrouped kind there would publish "this project has no label for what happened"
    about an endpoint whose condition was in fact known exactly, and would hide the vocabularies
    drifting apart behind a population that looks measured.
    """
    with pytest.raises(ValueError, match="CONDITION_OF_KIND"):
        condition_of(("teapot",))


# --- the arithmetic refusal, which is the point --------------------------------------------


def _orgs(*conditions: str) -> list[FrameOrg]:
    return [
        FrameOrg(
            state="TX",
            roster_name=f"Org {index}",
            population=DOCUMENTED_UNREACHABLE,
            detail="",
            surfaces=((f"endpoint-{index}", condition),),
        )
        for index, condition in enumerate(conditions)
    ]


def test_a_subtotal_within_one_side_is_computed() -> None:
    orgs = _orgs(ANSWERED_AND_DECLINED, ANSWERED_AND_DECLINED, NO_ANSWER)
    assert subtotal(orgs, [ANSWERED_AND_DECLINED]) == 2
    assert subtotal(orgs, [NO_ANSWER, ANSWERED_NOT_THE_DOCUMENT]) == 1


def test_a_subtotal_spanning_the_two_sides_is_refused() -> None:
    """The refusal #117 asks for: a gated population may never be added to a broken one.

    Refused rather than discouraged, for the reason `publishing_rate` is refused one function
    over. A number covering both sides is exactly the merged figure this issue exists to
    remove, rebuilt one level up where it is harder to notice.
    """
    orgs = _orgs(ANSWERED_AND_DECLINED, NO_ANSWER)
    with pytest.raises(ValueError, match=r"2 sides"):
        subtotal(orgs, [ANSWERED_AND_DECLINED, NO_ANSWER])


def test_the_refusal_names_both_sides_it_refused_over() -> None:
    orgs = _orgs(ANSWERED_AND_DECLINED, NO_ANSWER)
    with pytest.raises(ValueError) as raised:
        subtotal(orgs, [ANSWERED_AND_DECLINED, ANSWERED_NOT_THE_DOCUMENT])
    message = str(raised.value)
    assert "declined" in message and "no_document" in message


def test_neither_is_a_side_of_its_own_and_cannot_be_added_to_either() -> None:
    """An unclassified condition, a disagreement and an unrecorded one are about this project."""
    orgs = _orgs(ANSWERED_AND_DECLINED, CONDITION_UNCLASSIFIED)
    with pytest.raises(ValueError, match="sides"):
        subtotal(orgs, [ANSWERED_AND_DECLINED, CONDITION_UNCLASSIFIED])
    with pytest.raises(ValueError, match="sides"):
        subtotal(orgs, [NO_ANSWER, CONDITION_DISAGREED])


def test_a_subtotal_over_something_that_is_not_a_condition_is_refused() -> None:
    with pytest.raises(ValueError, match="not conditions"):
        subtotal(_orgs(NO_ANSWER), ["documented_unreachable"])


def test_conditions_are_counted_over_surfaces_not_organizations() -> None:
    """One organization listing two surfaces that failed differently counts in both.

    Counting organizations would force a choice between the two conditions, and choosing is the
    defect. The page says the counts are of surfaces for the same reason.
    """
    org = FrameOrg(
        state="TX",
        roster_name="Two Surfaces",
        population=DOCUMENTED_UNREACHABLE,
        detail="",
        surfaces=(("a", ANSWERED_AND_DECLINED), ("b", NO_ANSWER)),
    )
    tally = condition_counts([org])
    assert tally[ANSWERED_AND_DECLINED] == 1
    assert tally[NO_ANSWER] == 1
    assert len(unreachable_surfaces([org])) == 2
    assert counts([org])[DOCUMENTED_UNREACHABLE] == 1


def test_every_condition_is_present_in_the_tally_even_at_zero() -> None:
    assert set(condition_counts(_orgs(NO_ANSWER))) == set(CONDITIONS)


def test_only_the_documented_unreachable_population_contributes_surfaces() -> None:
    """A verified organization may also list a surface that did not answer.

    It is verified on its best-evidenced surface, and this is a breakdown *of* the
    documented-unreachable population, not a second census of every failed probe. Including it
    here would make the condition counts stop describing the population they sit under.
    """
    verified = FrameOrg("TX", "Verified", "verified", "", (("a", NO_ANSWER),))
    assert unreachable_surfaces([verified]) == []


# --- the committed frame -------------------------------------------------------------------


def _committed(failure_kinds: dict[str, tuple[str, ...]] | None = None) -> list[FrameOrg]:
    endpoints = [e for e in load_registry(DATA / "registry.json") if e.enabled]
    cohorts = load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in endpoints))
    return classify(
        read_frame(FRAME_CSV),
        cohorts,
        endpoints,
        read_reviewed_rows_by_cohort(COHORT_DIR),
        failure_kinds,
    )


def test_the_four_populations_still_sum_to_the_frame_with_conditions_attached() -> None:
    """The condition split is a second cut, never a fifth population."""
    orgs = _committed({"humana": ("timeout",)})
    assert sum(counts(orgs).values()) == len(read_frame(FRAME_CSV)) == len(orgs)


def test_a_build_with_no_probe_results_records_no_condition_rather_than_guessing() -> None:
    """An offline build has no conditions, and must not publish one."""
    surfaces = unreachable_surfaces(_committed())
    assert surfaces, "the committed frame has no documented-unreachable surfaces to check"
    assert {condition for _, _, condition in surfaces} == {CONDITION_NOT_OBSERVED}


def test_the_page_publishes_the_split_and_says_the_counts_are_of_surfaces() -> None:
    orgs = _committed({"humana": ("timeout",)})
    body = _text(coverage_page(orgs, DEFAULT_ORIGIN).body)
    assert 'What "did not answer" was' in body
    assert "never added to each other" in body
    assert "counts below are of surfaces, not of organizations" in body
    for heading in CONDITION_HEADINGS.values():
        assert heading in body, heading


def test_the_page_never_claims_the_organization_chose_anything() -> None:
    """The wording question #117 leaves open is left open, in the published sentence.

    `docs/SAMPLING-FRAME.md` is careful never to claim an intent -- "a member with no
    discoverable endpoint is not out of compliance with anything as far as this project is
    concerned" -- and a heading reading a 401 as a decision would be exactly that claim.
    """
    body = _text(coverage_page(_committed({"humana": ("timeout",)}), DEFAULT_ORIGIN).body).lower()
    assert "does not read either condition as a choice or as a defect" in body
    for intent in (" chose ", " chooses ", "deliberately", "refuses to publish", "is hiding"):
        assert intent not in body, f"the coverage page claims an intent: {intent!r}"


# --- cohort pages --------------------------------------------------------------------------


def _cohort_with(cards: dict[str, Scorecard]) -> Cohort:
    return Cohort(
        cohort_id="test-cohort",
        name="Test cohort",
        description="A fixed roster.",
        notes=(),
        sources=(),
        members=tuple(
            CohortMember(
                member_id=f"member-{index}",
                name=f"Member {index}",
                programs=(),
                endpoint_ids=(endpoint_id,),
            )
            for index, endpoint_id in enumerate(sorted(cards))
        ),
    )


def test_a_cohort_page_splits_its_unanswered_endpoints_by_condition() -> None:
    cards = {
        "gated": _card("gated", ("authentication_required",)),
        "broken": _card("broken", ("dns",)),
        "fine": _card("fine", ()),
    }
    body = _text(cohort_page(_cohort_with(cards), cards, DEFAULT_ORIGIN).body)
    assert 'What "did not answer" was' in body
    assert CONDITION_HEADINGS[ANSWERED_AND_DECLINED] in body
    assert CONDITION_HEADINGS[NO_ANSWER] in body


def test_a_cohort_page_keeps_the_split_within_a_category() -> None:
    """Grades are only comparable within a kind; a pooled condition table would be the first
    place on this site two kinds were compared."""
    cards = {
        "pa": _card("pa", ("forbidden",), kind="payer"),
        "pd": _card("pd", ("tls",), kind="payer_provider_directory"),
    }
    body = cohort_page(_cohort_with(cards), cards, DEFAULT_ORIGIN).body
    assert body.count("<caption>") >= 2
    text = _text(body)
    assert "1 listed endpoint did not answer" in text


def test_a_cohort_where_everything_answered_omits_the_section_rather_than_printing_zeroes() -> None:
    cards = {"fine": _card("fine", ())}
    body = _text(cohort_page(_cohort_with(cards), cards, DEFAULT_ORIGIN).body)
    assert 'What "did not answer" was' not in body


# --- the record ----------------------------------------------------------------------------


def test_the_observation_record_keeps_the_condition_of_a_day_that_did_not_answer() -> None:
    history: dict[str, object] = {}
    observe(
        history,
        "an-endpoint",
        _no_capability(),
        "2026-09-13",
        reachable=False,
        failure_kinds=("forbidden",),
    )
    entry = history["an-endpoint"]
    assert isinstance(entry, dict)
    assert entry["observations"] == [{"date": "2026-09-13", "up": False, "kinds": ["forbidden"]}]


def test_a_day_the_endpoint_answered_records_no_condition() -> None:
    history: dict[str, object] = {}
    observe(history, "an-endpoint", _no_capability(), "2026-09-13", reachable=True)
    entry = history["an-endpoint"]
    assert isinstance(entry, dict)
    assert entry["observations"] == [{"date": "2026-09-13", "up": True}]


@pytest.mark.parametrize(
    "raw",
    [
        {"date": "d", "up": False},
        {"date": "d", "up": False, "kinds": []},
        {"date": "d", "up": False, "kinds": "forbidden"},
        {"date": "d", "up": False, "kinds": ["not a kind"]},
        {"date": "d", "up": False, "kinds": ["forbidden", 7]},
        {"date": "d", "up": False, "kinds": None},
    ],
)
def test_a_kinds_value_that_cannot_be_read_is_no_condition_never_unclassified(
    raw: dict[str, object],
) -> None:
    """The opposite coercion from `fetch.normalise_failure_kind`, and deliberately so.

    That function reads a kind arriving from a foreign probe, where something did fail. Here
    nothing may be assumed to have failed at all: `unclassified` is a condition a run recorded,
    and writing it over an unreadable value would be a record of an observation nobody made.
    """
    assert observation_kinds(raw) == ()
    assert condition_of(observation_kinds(raw)) == CONDITION_NOT_OBSERVED


def test_the_published_record_carries_the_observation_and_the_reading_separately() -> None:
    """A consumer must be able to disagree with the grouping without losing the measurement."""
    record = Record(
        endpoint_id="an-endpoint",
        name="An Endpoint",
        kind="payer",
        observations=(
            Observation("2026-09-12", False, ("forbidden",)),
            Observation("2026-09-13", True),
        ),
        first_seen="2026-09-12",
        last_seen="2026-09-13",
    )
    published = json.loads(history_json(record, "2026-09-13 14:00 UTC"))["observations"]
    assert published[0] == {
        "date": "2026-09-12",
        "answered": False,
        "failure_kinds": ["forbidden"],
        "condition": ANSWERED_AND_DECLINED,
    }
    assert published[1]["failure_kinds"] == []
    assert published[1]["condition"] == CONDITION_NOT_OBSERVED

    page = _text(record_page(record, DEFAULT_ORIGIN).body)
    assert CONDITION_HEADINGS[ANSWERED_AND_DECLINED] in page
    assert "not applicable: the endpoint answered" in page


def test_an_observation_recorded_before_the_field_existed_says_so() -> None:
    record = Record(
        endpoint_id="an-endpoint",
        name="An Endpoint",
        kind="payer",
        observations=(Observation("2026-08-05", False),),
        first_seen="2026-08-05",
        last_seen="2026-08-05",
    )
    assert "no condition was recorded" in _text(record_page(record, DEFAULT_ORIGIN).body)


# --- over time -----------------------------------------------------------------------------


def _record(*observations: Observation) -> Record:
    return Record(
        endpoint_id="an-endpoint",
        name="An Endpoint",
        kind="payer",
        observations=observations,
        first_seen=observations[0].date,
        last_seen=observations[-1].date,
    )


def test_an_endpoint_moving_from_broken_to_gated_is_reported() -> None:
    """The event #117 names, and the reason the record has to carry the condition at all."""
    record = _record(
        Observation("2026-09-01", False, ("dns",)),
        Observation("2026-09-02", False, ("authentication_required",)),
    )
    moves = [move for section in sections([record]) for move in section.condition_moves]
    assert moves == [("2026-09-02", "An Endpoint", NO_ANSWER, ANSWERED_AND_DECLINED)]


def test_an_outage_between_two_identical_conditions_is_not_a_move() -> None:
    """Gated, back up, gated again has not moved condition.

    Reading the answering day as an end of one run and a start of another would turn an outage
    into a finding about the public record.
    """
    record = _record(
        Observation("2026-09-01", False, ("forbidden",)),
        Observation("2026-09-02", True),
        Observation("2026-09-03", False, ("forbidden",)),
    )
    assert [move for section in sections([record]) for move in section.condition_moves] == []


def test_an_observation_with_no_recorded_condition_never_starts_or_ends_a_move() -> None:
    """Most of the record predates the field; a move from nothing to something is about that."""
    record = _record(
        Observation("2026-09-01", False),
        Observation("2026-09-02", False, ("forbidden",)),
    )
    assert [move for section in sections([record]) for move in section.condition_moves] == []


def test_a_month_with_no_move_says_which_of_two_empty_months_it_is() -> None:
    """A month before the condition was retained can produce no move at all.

    Reading that as "nothing moved" would publish a fact about when this field shipped as a
    fact about the endpoints, which is the same error one level over from #135's.
    """
    rendered = _text(
        over_time_page([_record(Observation("2026-09-01", False))], DEFAULT_ORIGIN).body
    )
    assert "Moved from one condition to another" in rendered
    assert "fact about the record rather than about any endpoint" in rendered


# --- one reconciliation, two readers -------------------------------------------------------


def test_the_record_and_the_card_read_the_same_reconciliation() -> None:
    """`failure_kinds_of` is public so the availability record and the card cannot disagree.

    The record is written before the card is built, so a second expression of the rule at the
    call site would be a second place for the two to drift.
    """
    metadata = FetchResult(
        url="https://example.test/metadata",
        ok=False,
        status=403,
        elapsed_ms=0,
        body=b"",
        error="HTTP 403",
        failure_kind="forbidden",
    )
    assert failure_kinds_of(metadata, None) == ("forbidden",)
    assert condition_of(failure_kinds_of(metadata, None)) == ANSWERED_AND_DECLINED


def _no_capability() -> object:
    from fhir_scorecard.capability import CapabilityFacts

    return CapabilityFacts(parsed=False, resource_type_ok=False)
