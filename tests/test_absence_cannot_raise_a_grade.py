"""A check this run could not make must never improve the grade it could not make it on.

The grader computed a dimension's score as `earned / points that happened to be on the table`.
When a check could not run, its points left the denominator -- so the remaining checks were
divided by a smaller number, and dropping a check the endpoint was *failing* raised the
percentage. An endpoint whose SMART discovery document no vantage retrieved came out ahead of
the same endpoint with a SMART document that arrived and was unusable: identical evidence about
the endpoint, twenty-two interop points better for the absence of a document, and one letter
better once weighted.

That is this portfolio's dominant defect class arriving from an unusual direction. The familiar
shape is absence rendered as a failure -- a `-1` printed as a score, an unreachable endpoint
graded F. This is absence rendered as a *pass*, and it is harder to notice because nothing looks
wrong: the number is plausible, the letter is a letter, and the endpoint it flatters is a named
organization.

These tests are the property, not the instance. The first pair is the measured regression. The
rest hold the general rule: removing evidence may lower a grade or leave it unknown, and may
never raise one.
"""

from __future__ import annotations

import json

from fhir_scorecard.capability import (
    NO_SMART_RETRIEVED,
    CapabilityFacts,
    SmartFacts,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import (
    NOT_OBSERVED,
    DimensionScore,
    build_scorecard,
    grade_interop,
    grade_transparency,
    letter,
)

_GRADES = ("F", "D", "C", "B", "A")


def _facts(**overrides: object) -> CapabilityFacts:
    base: dict[str, object] = {
        "parsed": True,
        "observed": True,
        "resource_type_ok": True,
        "fhir_version": "4.0.1",
        "software_name": None,
        "software_version": None,
        "resource_count": 4,
        "resources_with_interactions": 2,
        "conformance_profiles": (
            (
                "rest.resource.supportedProfile",
                "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient",
            ),
        ),
        "declares_oauth_security": False,
    }
    base.update(overrides)
    return CapabilityFacts(**base)  # type: ignore[arg-type]


def _smart_unusable() -> SmartFacts:
    """A SMART document that arrived and does not declare its endpoints. An observation."""
    return parse_smart(b"{}")


def _reached() -> FetchResult:
    return FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=120,
        body=json.dumps({"resourceType": "CapabilityStatement"}).encode(),
        error=None,
    )


def _card(smart: SmartFacts):
    return build_scorecard("payer", "Payer Health Plan", _reached(), _facts(), smart, kind="payer")


# --- the measured regression -------------------------------------------------


def test_a_smart_document_nobody_retrieved_does_not_raise_the_interop_score() -> None:
    """The exact numbers that were published: 40 with the document, 62 without it."""
    observed = grade_interop(_facts(), _smart_unusable(), kind="payer")
    assert observed.score == 40, "the baseline this regression was measured against moved"

    withheld = grade_interop(_facts(), NO_SMART_RETRIEVED, kind="payer")
    assert withheld.score is None, (
        "a dimension missing 35 of its 100 points has no percentage on the published scale; "
        f"it reported {withheld.score}"
    )
    assert withheld.withheld_points == 35


def test_a_smart_document_nobody_retrieved_does_not_raise_the_letter() -> None:
    with_document = _card(_smart_unusable())
    without_document = _card(NO_SMART_RETRIEVED)
    assert with_document.grade == "F"
    assert without_document.grade == NOT_OBSERVED, (
        "this run cannot tell F from D for this endpoint, and published D"
    )


# --- the general property ----------------------------------------------------


def _weighted_floor(card) -> float:
    """The lowest weighted score consistent with what this run measured.

    A withheld check contributes nothing here: the floor assumes every unmade check would have
    failed. Comparing floors is what makes "the grade did not go up" a statement about the
    evidence rather than about the rounding.
    """
    from fhir_scorecard.grading import _WEIGHTS, _dimension_bounds

    total = 0.0
    for dimension in card.dimensions:
        bounds = _dimension_bounds(dimension)
        if bounds is None:
            return 0.0
        total += bounds[0] * _WEIGHTS.get(dimension.key, 0.0)
    return total


def test_withholding_a_document_never_raises_the_floor_of_any_grade() -> None:
    """Across every SMART state and several capability documents, losing the SMART document
    may lower what this run can say and may never raise it."""
    capabilities = [
        _facts(),
        _facts(declares_oauth_security=True),
        _facts(resource_count=8, resources_with_interactions=8),
        _facts(conformance_profiles=()),
    ]
    smart_good = parse_smart(
        json.dumps(
            {"authorization_endpoint": "https://a/", "token_endpoint": "https://t/"}
        ).encode()
    )
    for facts in capabilities:
        for present in (smart_good, _smart_unusable()):
            observed = build_scorecard("payer", "Payer", _reached(), facts, present, kind="payer")
            withheld = build_scorecard(
                "payer", "Payer", _reached(), facts, NO_SMART_RETRIEVED, kind="payer"
            )
            assert _weighted_floor(withheld) <= _weighted_floor(observed) + 1e-9, (
                "losing the SMART document raised the floor of the weighted score"
            )
            if withheld.grade in _GRADES and observed.grade in _GRADES:
                assert _GRADES.index(withheld.grade) <= _GRADES.index(observed.grade), (
                    f"grade rose from {observed.grade} to {withheld.grade} when the SMART "
                    "document went missing"
                )


def test_a_complete_run_is_graded_exactly_as_before() -> None:
    """The bound only widens when something was not measured. For every endpoint whose
    documents were all retrieved the two bounds are equal, so this change is a no-op -- which
    is what makes it safe to ship against a published site."""
    from fhir_scorecard.grading import _dimension_bounds

    smart_good = parse_smart(
        json.dumps(
            {"authorization_endpoint": "https://a/", "token_endpoint": "https://t/"}
        ).encode()
    )
    for facts in (_facts(), _facts(declares_oauth_security=True)):
        for smart in (smart_good, _smart_unusable()):
            card = build_scorecard("payer", "Payer", _reached(), facts, smart, kind="payer")
            for dimension in card.dimensions:
                assert dimension.withheld_points == 0
                low, high = _dimension_bounds(dimension)  # type: ignore[misc]
                assert low == high, f"{dimension.key} bounds separated with nothing withheld"
            assert card.grade in _GRADES


def test_a_provider_directory_is_unaffected_because_nothing_is_withheld() -> None:
    """`public_by_design` gives I2 and I3 zero max points and marks them *observed*: not
    applicable is a measurement, and must keep scoring. If this started reporting withheld
    points, every provider directory would lose its letter for a check it should not have."""
    dimension = grade_interop(_facts(), NO_SMART_RETRIEVED, kind="payer_provider_directory")
    assert dimension.withheld_points == 0
    assert dimension.score is not None


def test_nothing_retrieved_at_all_is_still_not_observed_rather_than_bounded() -> None:
    """A dimension with no observed points has no bound to give, and must not be treated as
    one where a little was withheld."""
    from fhir_scorecard.capability import NO_CAPABILITY_RETRIEVED

    unreachable = FetchResult(
        url="https://payer.test/r4/metadata",
        ok=False,
        status=None,
        elapsed_ms=0,
        body=b"",
        error="connection refused",
    )
    card = build_scorecard(
        "payer", "Payer", unreachable, NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED, kind="payer"
    )
    assert card.grade == NOT_OBSERVED
    transparency = next(d for d in card.dimensions if d.key == "transparency")
    assert transparency.score is None and transparency.withheld_points == 0


# --- negative controls -------------------------------------------------------


def test_the_bound_actually_separates_when_points_are_withheld() -> None:
    """The control for `test_a_complete_run_is_graded_exactly_as_before`. If `_dimension_bounds`
    returned the same pair whatever happened, that test would pass over a grader that had not
    been fixed at all."""
    from fhir_scorecard.grading import _dimension_bounds

    withheld = grade_interop(_facts(), NO_SMART_RETRIEVED, kind="payer")
    low, high = _dimension_bounds(withheld)  # type: ignore[misc]
    assert low < high, "the bound did not widen for a dimension missing 35 of its 100 points"
    assert round(low) == 40 and round(high) == 75


def test_a_withheld_check_that_cannot_change_the_band_still_yields_a_letter() -> None:
    """The other control. A rule that withheld the letter whenever *anything* was unmeasured
    would satisfy every assertion above and would blank grades this run genuinely knows.

    Here the capability document arrived and is not a CapabilityStatement, so interop can earn
    at most the withheld 35 -- not enough to lift the weighted score out of F from either side.
    The letter is published, and it is the same letter as when the SMART document was in hand.
    """
    body = b'{"resourceType":"OperationOutcome"}'
    metadata = FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=412,
        body=body,
        error=None,
    )
    facts = parse_capability(body)
    withheld = build_scorecard("payer", "Payer", metadata, facts, NO_SMART_RETRIEVED, kind="payer")
    observed = build_scorecard("payer", "Payer", metadata, facts, _smart_unusable(), kind="payer")
    assert observed.grade == "F"
    assert withheld.grade == "F", (
        "the letter was withheld for an endpoint whose grade the missing document could not "
        "change; that trades one false publication for another"
    )
    interop = next(d for d in withheld.dimensions if d.key == "interop")
    assert interop.score is None and interop.withheld_points == 35


def test_letter_refuses_when_the_bound_spans_two_bands() -> None:
    """The assertion `letter` exists to make, stated directly rather than through a fixture."""
    reach = DimensionScore(key="reachability", title="Reachability", score=100, findings=())
    transparency = grade_transparency(_facts())
    interop = grade_interop(_facts(), NO_SMART_RETRIEVED, kind="payer")
    assert letter((reach, transparency, interop), reachable=True) == NOT_OBSERVED
    # ...and does not refuse merely because it was asked.
    settled = grade_interop(_facts(), _smart_unusable(), kind="payer")
    assert letter((reach, transparency, settled), reachable=True) in _GRADES


# --- the invariant that makes the rest unnecessary ---------------------------


def test_every_dimensions_denominator_is_the_same_for_every_endpoint_of_a_kind() -> None:
    """The general form of this bug, stated once so no future check can reintroduce it.

    A dimension score is a percentage, and the letter thresholds are calibrated against a
    full scale. That only holds while `sum(max_points) + withheld_points` is the same number
    for every endpoint of a given kind, whatever its documents said or failed to say. A check
    that is conditionally appended, or one that quietly carries `max_points=0` where it should
    carry its weight, moves the denominator and puts the endpoints on different scales -- which
    is what happened here, and what nothing was watching for.

    Dimensions that observed nothing at all are exempt: they publish no percentage, so there is
    no scale for them to be off.
    """
    from fhir_scorecard.capability import NO_CAPABILITY_RETRIEVED, parse_capability

    smart_states = {
        "complete": parse_smart(
            json.dumps(
                {"authorization_endpoint": "https://a/", "token_endpoint": "https://t/"}
            ).encode()
        ),
        "empty": parse_smart(b"{}"),
        "unretrieved": NO_SMART_RETRIEVED,
    }
    capability_states = {
        "good": _facts(),
        "oauth": _facts(declares_oauth_security=True),
        "no-profiles": _facts(conformance_profiles=()),
        "unreadable": parse_capability(b'{"resourceType":"OperationOutcome"}'),
        "empty-body": parse_capability(b""),
        "unretrieved": NO_CAPABILITY_RETRIEVED,
    }

    seen: dict[tuple[str, str], set[int]] = {}
    for kind in ("payer", "ehr", "reference", "payer_provider_directory"):
        for cap_name, facts in capability_states.items():
            for smart_name, smart in smart_states.items():
                card = build_scorecard("e", "Endpoint", _reached(), facts, smart, kind=kind)
                for dimension in card.dimensions:
                    observed = sum(f.max_points for f in dimension.findings)
                    scale = observed + dimension.withheld_points
                    if scale == 0:
                        # Nothing was observed; no percentage is published.
                        assert dimension.score is None, (kind, cap_name, smart_name)
                        continue
                    seen.setdefault((kind, dimension.key), set()).add(scale)

    assert seen, "the sweep produced no dimension to check"
    drifting = {key: sorted(scales) for key, scales in seen.items() if len(scales) > 1}
    assert not drifting, (
        "these dimensions score different endpoints of the same kind out of different "
        f"totals, so their percentages are not comparable and the letter thresholds do not "
        f"mean one thing: {drifting}"
    )
