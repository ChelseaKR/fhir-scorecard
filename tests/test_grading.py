from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
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
    grade_reachability,
    grade_transparency,
    letter,
)


def _fetch(
    ok: bool, *, elapsed_ms: int = 100, status: int | None = 200, error: str | None = None
) -> FetchResult:
    return FetchResult(
        url="https://example.test/metadata",
        ok=ok,
        status=status,
        elapsed_ms=elapsed_ms,
        body=b"",
        error=error,
    )


def test_good_endpoint_grades_a(good_capability_bytes: bytes, good_smart_bytes: bytes) -> None:
    card = build_scorecard(
        "x",
        "X",
        _fetch(True),
        parse_capability(good_capability_bytes),
        parse_smart(good_smart_bytes),
    )
    assert card.grade == "A"
    assert card.reachable


def test_unreachable_is_published_with_a_reason_and_not_graded() -> None:
    """Unreachable is a fact about this run's reach, and it is published as one: the reason is
    stated, the endpoint stays in the dataset, and nothing is graded from documents nobody got."""
    card = build_scorecard(
        "x",
        "X",
        _fetch(False, status=None, error="URLError"),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
    )
    assert card.grade == NOT_OBSERVED
    assert not card.reachable
    r1 = card.dimensions[0].findings[0]
    assert not r1.ok and "unreachable" in r1.message


def test_slow_endpoint_loses_latency_points() -> None:
    fast = grade_reachability(_fetch(True, elapsed_ms=500))
    slow = grade_reachability(_fetch(True, elapsed_ms=4000))
    glacial = grade_reachability(_fetch(True, elapsed_ms=9000))
    assert fast.score > slow.score > glacial.score


def test_unparseable_capability_zeroes_transparency() -> None:
    dim = grade_transparency(parse_capability(b"<html></html>"))
    assert dim.score == 0
    assert dim.findings[0].code == "T0"


def test_boilerplate_capability_scores_low(good_capability_bytes: bytes) -> None:
    import json

    rich = grade_transparency(parse_capability(good_capability_bytes))
    bare = grade_transparency(
        parse_capability(json.dumps({"resourceType": "CapabilityStatement"}).encode())
    )
    assert rich.score == 100
    assert bare.score == 0


def test_narrow_but_complete_earns_full_breadth_points() -> None:
    """A deliberately narrow API (CMS Blue Button shape: 3 resources, all documented) is
    transparent, not deficient. Calibration 2026-08-05."""
    import json

    doc = {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "software": {"name": "NarrowServer", "version": "1.0"},
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {"type": t, "interaction": [{"code": "read"}]}
                    for t in ["Patient", "Coverage", "ExplanationOfBenefit"]
                ],
            }
        ],
    }
    dim = grade_transparency(parse_capability(json.dumps(doc).encode()))
    t3 = next(f for f in dim.findings if f.code == "T3")
    assert t3.ok and "narrow but fully documented" in t3.message
    assert dim.score == 100

    # Narrow AND undocumented still loses the points.
    for r in doc["rest"][0]["resource"]:  # type: ignore[index]
        r.pop("interaction")
    dim2 = grade_transparency(parse_capability(json.dumps(doc).encode()))
    assert not next(f for f in dim2.findings if f.code == "T3").ok


def test_interop_requires_recognized_profiles(
    good_capability_bytes: bytes, good_smart_bytes: bytes
) -> None:
    full = grade_interop(parse_capability(good_capability_bytes), parse_smart(good_smart_bytes))
    none = grade_interop(parse_capability(b""), parse_smart(b""))
    assert full.score == 100
    assert none.score == 0


@st.composite
def facts_strategy(draw: st.DrawFn) -> CapabilityFacts:
    count = draw(st.integers(min_value=0, max_value=50))
    return CapabilityFacts(
        parsed=draw(st.booleans()),
        resource_type_ok=draw(st.booleans()),
        fhir_version=draw(st.one_of(st.none(), st.text(max_size=8))),
        software_name=draw(st.one_of(st.none(), st.just("s"))),
        software_version=draw(st.one_of(st.none(), st.just("1"))),
        resource_count=count,
        resources_with_interactions=draw(st.integers(min_value=0, max_value=count)),
        supported_profiles=tuple(draw(st.lists(st.text(max_size=40), max_size=5))),
        declares_oauth_security=draw(st.booleans()),
    )


@given(
    facts=facts_strategy(),
    smart_auth=st.booleans(),
    smart_token=st.booleans(),
    smart_parsed=st.booleans(),
    ok=st.booleans(),
    elapsed=st.integers(min_value=0, max_value=60_000),
)
def test_scores_always_bounded_and_grade_valid(
    facts: CapabilityFacts,
    smart_auth: bool,
    smart_token: bool,
    smart_parsed: bool,
    ok: bool,
    elapsed: int,
) -> None:
    smart = SmartFacts(
        parsed=smart_parsed, has_authorization_endpoint=smart_auth, has_token_endpoint=smart_token
    )
    card = build_scorecard("p", "P", _fetch(ok, elapsed_ms=elapsed), facts, smart)
    for dim in card.dimensions:
        assert dim.score is None or 0 <= dim.score <= 100
        for f in dim.findings:
            assert 0 <= f.points <= f.max_points
    assert card.grade in {"A", "B", "C", "D", "F", NOT_OBSERVED}
    if not ok:
        assert card.grade == NOT_OBSERVED


def test_letter_thresholds() -> None:
    from fhir_scorecard.grading import DimensionScore

    def dims(score: int) -> tuple:
        return tuple(
            DimensionScore(key=k, title=k, score=score, findings=())
            for k in ("reachability", "transparency", "interop")
        )

    assert letter(dims(95), reachable=True) == "A"
    assert letter(dims(85), reachable=True) == "B"
    assert letter(dims(75), reachable=True) == "C"
    assert letter(dims(65), reachable=True) == "D"
    assert letter(dims(10), reachable=True) == "F"
    # Unreachable is not a letter at all: F is what a graded endpoint earns, and a reader
    # compares an F against a C.
    assert letter(dims(100), reachable=False) == NOT_OBSERVED
    unscored = (
        DimensionScore(key="reachability", title="r", score=100, findings=()),
        DimensionScore(key="transparency", title="t", score=None, findings=()),
        DimensionScore(key="interop", title="i", score=None, findings=()),
    )
    assert letter(unscored, reachable=True) == NOT_OBSERVED


def test_kind_defaults_and_propagates(
    good_capability_bytes: bytes, good_smart_bytes: bytes
) -> None:
    default = build_scorecard(
        "x",
        "X",
        _fetch(True),
        parse_capability(good_capability_bytes),
        parse_smart(good_smart_bytes),
    )
    assert default.kind == "reference"
    payer = build_scorecard(
        "y",
        "Y",
        _fetch(True),
        parse_capability(good_capability_bytes),
        parse_smart(good_smart_bytes),
        kind="payer",
    )
    assert payer.kind == "payer"


def test_provider_directory_not_penalized_for_being_public(good_capability_bytes: bytes) -> None:
    """A Provider Directory API must be reachable without auth, so grading it on a SMART/OAuth
    surface it is required NOT to have would penalize compliant behavior. Calibration 2026-08-05."""
    import json

    doc = json.loads(good_capability_bytes)
    doc["rest"][0].pop("security")  # public by design: no OAuth declared
    facts = parse_capability(json.dumps(doc).encode())
    no_smart = parse_smart(b"")

    as_payer = grade_interop(facts, no_smart, kind="payer")
    as_directory = grade_interop(facts, no_smart, kind="payer_provider_directory")

    assert as_payer.score < as_directory.score
    assert as_directory.score == 100  # profiles present; auth findings not applicable
    for code in ("I2", "I3"):
        f = next(x for x in as_directory.findings if x.code == code)
        assert f.max_points == 0 and "not applicable" in f.message


def test_version_checked_against_declared_intent() -> None:
    """An R5 server declaring 5.0.0 is correct; marking it down for not being R4 would
    measure the wrong thing. Calibration 2026-08-05."""
    import json

    doc = {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "5.0.0",
        "software": {"name": "S", "version": "1"},
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {"type": t, "interaction": [{"code": "read"}]}
                    for t in ["Patient", "Observation", "Encounter", "Condition", "Procedure"]
                ],
            }
        ],
    }
    facts = parse_capability(json.dumps(doc).encode())
    as_r4 = grade_transparency(facts, version_prefix="4.")
    as_r5 = grade_transparency(facts, version_prefix="5.")
    assert not next(f for f in as_r4.findings if f.code == "T1").ok
    assert next(f for f in as_r5.findings if f.code == "T1").ok
    assert as_r5.score > as_r4.score
    assert "expected 5.x" in next(f for f in as_r5.findings if f.code == "T1").message


def test_consensus_rescues_a_vantage_local_failure(
    good_capability_bytes: bytes, good_smart_bytes: bytes
) -> None:
    """The real 2026-08-05 case: a live payer endpoint looked dead from one network because a
    middlebox intercepted TLS. Another vantage reaching it must settle the question."""
    from fhir_scorecard.vantage import VantageProbe, reconcile

    blocked = FetchResult(
        url="https://x.test/metadata",
        ok=False,
        status=None,
        elapsed_ms=0,
        body=b"",
        error="TLS certificate verification failed",
    )
    consensus = reconcile(
        [
            VantageProbe("home", False, 0, "TLS certificate verification failed"),
            VantageProbe("ci", True, 640),
        ]
    )
    card = build_scorecard(
        "cap",
        "Capital",
        blocked,
        parse_capability(good_capability_bytes),
        parse_smart(good_smart_bytes),
        consensus=consensus,
    )
    assert card.reachable
    assert card.grade != "F"
    assert "not reached from home" in card.vantage_note
    # And no attribution: naming the failing vantage and its cause is what this run observed;
    # deciding whether the network or the endpoint caused it is not.
    assert "property of that network" not in card.vantage_note
    r1 = next(f for f in card.dimensions[0].findings if f.code == "R1")
    assert r1.ok

    # Without the second vantage, the same run still fails closed.
    alone = build_scorecard("cap", "Capital", blocked, NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED)
    assert not alone.reachable and alone.grade == NOT_OBSERVED


def test_unanimous_failure_is_not_observed_not_f(good_capability_bytes: bytes) -> None:
    from fhir_scorecard.vantage import VantageProbe, reconcile

    down = FetchResult(
        url="https://x.test/metadata",
        ok=False,
        status=404,
        elapsed_ms=0,
        body=b"",
        error="HTTP 404",
    )
    consensus = reconcile(
        [VantageProbe("home", False, 0, "HTTP 404"), VantageProbe("ci", False, 0, "HTTP 404")]
    )
    card = build_scorecard(
        "gone", "Gone", down, NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED, consensus=consensus
    )
    assert not card.reachable and card.grade == NOT_OBSERVED
    # Every vantage failing is reported as what it is, a failure to reach from the vantages
    # tried, rather than as a settled fact about the endpoint.
    assert "not reached from any of the 2 vantages tried" in card.vantage_note
    assert "HTTP 404" in card.vantage_note


def _dims(score: int) -> tuple[DimensionScore, ...]:
    return tuple(
        DimensionScore(key=k, title=k, score=score, findings=())
        for k in ("reachability", "transparency", "interop")
    )


@pytest.mark.parametrize(
    "weighted, expected",
    [
        (100, "A"),
        (90, "A"),
        (89, "B"),
        (80, "B"),
        (79, "C"),
        (70, "C"),
        (69, "D"),
        (60, "D"),
        (59, "F"),
        (0, "F"),
    ],
)
def test_every_letter_band_edge_is_pinned(weighted: int, expected: str) -> None:
    """Each threshold tested at the edge and one below it.

    The suite used to test 95, 85, 75, 65 and 10 - every value five points inside a band, so all
    four comparisons could be changed from ``>=`` to ``>`` with the suite still green. The letter
    is the product; an endpoint scoring exactly 90 is the case that decides what A means.
    """
    assert letter(_dims(weighted), reachable=True) == expected


def test_the_published_weights_are_the_ones_the_letter_uses() -> None:
    """The methodology page prints "35% / 35% / 30%" as its method.

    Nothing connected those characters to `_WEIGHTS`: reordering the mapping left the whole
    suite green while the site went on publishing the old split. The page now renders from
    `WEIGHTED_DIMENSIONS`, and this asserts the arithmetic behind it still holds.
    """
    from fhir_scorecard.grading import _WEIGHTS, WEIGHTED_DIMENSIONS
    from fhir_scorecard.site import how_we_grade_page

    assert sum(w for _, _, w in WEIGHTED_DIMENSIONS) == 1.0
    assert {k: w for k, _, w in WEIGHTED_DIMENSIONS} == _WEIGHTS
    assert {k for k, _, _ in WEIGHTED_DIMENSIONS} == {"reachability", "transparency", "interop"}

    body = how_we_grade_page("https://x.test").body
    printed = [int(pct) for pct in re.findall(r"<strong>(\d+)%</strong>", body)]
    assert printed == [round(w * 100) for _, _, w in WEIGHTED_DIMENSIONS]
    assert sum(printed) == 100

    # And the titles beside the numbers are the ones the grader actually produces.
    for _, title, _ in WEIGHTED_DIMENSIONS:
        assert f"<span>{title}</span>" in body


@pytest.mark.parametrize(
    "elapsed, points, ok",
    [(0, 40, True), (3000, 40, True), (3001, 20, False), (8000, 20, False), (8001, 0, False)],
)
def test_both_latency_band_edges_are_pinned(elapsed: int, points: int, ok: bool) -> None:
    """The old test asserted only ``fast > slow > glacial`` at 500/4000/9000, so both edges
    could move and the middle band's value could change from 20 to 30 unnoticed."""
    dim = grade_reachability(_fetch(True, elapsed_ms=elapsed))
    r2 = next(f for f in dim.findings if f.code == "R2")
    assert (r2.points, r2.ok) == (points, ok)


def _caps(count: int, documented: int) -> CapabilityFacts:
    return CapabilityFacts(
        parsed=True,
        resource_type_ok=True,
        fhir_version="4.0.1",
        software_name="s",
        software_version="1",
        resource_count=count,
        resources_with_interactions=documented,
        supported_profiles=(),
        declares_oauth_security=True,
    )


@pytest.mark.parametrize(
    "count, documented, t3_ok, narrow",
    [
        (0, 0, False, False),
        (1, 1, False, False),  # one resource is not "narrow but complete"
        (2, 2, True, True),  # lower edge of the narrow-but-complete window
        (4, 4, True, True),  # upper edge
        (4, 3, False, False),  # narrow and NOT fully documented loses the points
        (5, 0, True, False),  # five types is enough on breadth alone
        (5, 5, True, False),  # and is never described as narrow
    ],
)
def test_t3_breadth_window_is_pinned_at_both_edges(
    count: int, documented: int, t3_ok: bool, narrow: bool
) -> None:
    """`2 <= count < 5` and `count >= 5` were both free to move.

    Only hand-built 3- and 6-resource documents existed, so `>= 5` could become `> 5` and the
    window's lower bound could become `1 <=` with the suite green.
    """
    dim = grade_transparency(_caps(count, documented))
    t3 = next(f for f in dim.findings if f.code == "T3")
    assert t3.ok is t3_ok
    assert ("narrow but fully documented" in t3.message) is narrow


@pytest.mark.parametrize(
    "count, documented, ok",
    [
        (10, 10, True),  # 100%
        (10, 8, True),  # exactly the 80% threshold
        (10, 7, False),  # just under
        (5, 4, True),  # 80% on a smaller denominator
        (5, 3, False),  # 60% - the value a 0.5 rule would wrongly pass
        (0, 0, False),  # nothing declared is not "fully documented"
    ],
)
def test_t4_documentation_ratio_is_pinned_as_a_ratio(count: int, documented: int, ok: bool) -> None:
    """`>= 0.8 * resource_count` could be rewritten to `>= 0.5 *` and survive the whole suite.

    That is not an off-by-one, it is half the rule: the 5-of-3 case below passes under 0.5 and
    fails under 0.8, and nothing distinguished them.
    """
    t4 = next(f for f in grade_transparency(_caps(count, documented)).findings if f.code == "T4")
    assert t4.ok is ok


@pytest.mark.parametrize(
    "parsed, auth, token, ok, points",
    [
        (True, True, True, True, 35),
        (True, True, False, False, 0),
        (True, False, True, False, 0),
        (True, False, False, False, 0),
        (False, True, True, False, 0),
    ],
)
def test_smart_completeness_needs_every_field_not_any_of_them(
    parsed: bool, auth: bool, token: bool, ok: bool, points: int
) -> None:
    """`parsed and auth and token` relaxed to `parsed and (auth or token)` and survived.

    A half-published SMART document then scored 35 of 35 and rendered "present and complete"
    about a named payer.
    """
    smart = SmartFacts(parsed=parsed, has_authorization_endpoint=auth, has_token_endpoint=token)
    i2 = next(f for f in grade_interop(_caps(6, 6), smart).findings if f.code == "I2")
    assert (i2.ok, i2.points) == (ok, points)
    assert ("present and complete" in i2.message) is ok


@pytest.mark.parametrize(
    "name, version, ok, points",
    [
        ("Server", "1.0", True, 20),
        ("Server", None, False, 0),
        (None, "1.0", False, 0),
        (None, None, False, 0),
    ],
)
def test_software_identification_needs_both_name_and_version(
    name: str | None, version: str | None, ok: bool, points: int
) -> None:
    """The conjunction relaxed to `or` and survived: a name with no version earned the full 20."""
    facts = CapabilityFacts(
        parsed=True,
        resource_type_ok=True,
        fhir_version="4.0.1",
        software_name=name,
        software_version=version,
        resource_count=6,
        resources_with_interactions=6,
        supported_profiles=(),
        declares_oauth_security=True,
    )
    t2 = next(f for f in grade_transparency(facts).findings if f.code == "T2")
    assert (t2.ok, t2.points) == (ok, points)


# --- properties, rather than bounds ---
#
# The one existing property test drives every branch of this module with random facts and
# asserts only that scores stay within 0..100 and the grade is a member of the alphabet. That is
# maximal execution with near-zero discrimination, and it is why every boundary above could move
# with the suite green and `grading.py` at 100% line coverage. These say what the scoring means.


@given(weighted=st.integers(min_value=0, max_value=100))
def test_a_letter_never_improves_as_the_score_falls(weighted: int) -> None:
    """Monotonic, and each band edge belongs to the better letter."""
    order = ["F", "D", "C", "B", "A"]
    here = letter(_dims(weighted), reachable=True)
    below = letter(_dims(max(0, weighted - 1)), reachable=True)
    assert order.index(here) >= order.index(below)


@given(
    count=st.integers(min_value=0, max_value=40),
    documented=st.integers(min_value=0, max_value=40),
    extra=st.integers(min_value=1, max_value=10),
)
def test_documenting_more_resources_never_lowers_transparency(
    count: int, documented: int, extra: int
) -> None:
    """The direction of the score is the claim the dimension makes. A rule that inverted for
    some input would still satisfy a bounds check."""
    documented = min(documented, count)
    base = grade_transparency(_caps(count, documented)).score or 0
    more = grade_transparency(_caps(count, min(count, documented + extra))).score or 0
    assert more >= base


@given(elapsed=st.integers(min_value=0, max_value=60_000), faster=st.integers(1, 5_000))
def test_answering_faster_never_lowers_reachability(elapsed: int, faster: int) -> None:
    base = grade_reachability(_fetch(True, elapsed_ms=elapsed)).score or 0
    quicker = grade_reachability(_fetch(True, elapsed_ms=max(0, elapsed - faster))).score or 0
    assert quicker >= base


@given(
    count=st.integers(min_value=0, max_value=40),
    documented=st.integers(min_value=0, max_value=40),
)
def test_a_dimension_score_is_its_findings_arithmetic(count: int, documented: int) -> None:
    """The published score is exactly points over max points, so no finding is silently
    weighted twice or dropped from the total it appears in."""
    dim = grade_transparency(_caps(count, min(count, documented)))
    earned = sum(f.points for f in dim.findings)
    possible = sum(f.max_points for f in dim.findings)
    assert dim.score == (round(100 * earned / possible) if possible else 0)
