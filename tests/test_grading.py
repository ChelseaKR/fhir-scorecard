from __future__ import annotations

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
    build_scorecard,
    grade_interop,
    grade_reachability,
    grade_transparency,
    letter,
)


def _fetch(ok: bool, *, elapsed_ms: int = 100, status: int | None = 200,
           error: str | None = None) -> FetchResult:
    return FetchResult(url="https://example.test/metadata", ok=ok, status=status,
                       elapsed_ms=elapsed_ms, body=b"", error=error)


def test_good_endpoint_grades_a(good_capability_bytes: bytes, good_smart_bytes: bytes) -> None:
    card = build_scorecard("x", "X", _fetch(True),
                           parse_capability(good_capability_bytes),
                           parse_smart(good_smart_bytes))
    assert card.grade == "A"
    assert card.reachable


def test_unreachable_is_published_with_a_reason_and_not_graded() -> None:
    """Unreachable is a fact about this run's reach, and it is published as one: the reason is
    stated, the endpoint stays in the dataset, and nothing is graded from documents nobody got."""
    card = build_scorecard("x", "X", _fetch(False, status=None, error="URLError"),
                           NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED)
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
    bare = grade_transparency(parse_capability(
        json.dumps({"resourceType": "CapabilityStatement"}).encode()))
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
        "rest": [{"mode": "server", "resource": [
            {"type": t, "interaction": [{"code": "read"}]}
            for t in ["Patient", "Coverage", "ExplanationOfBenefit"]
        ]}],
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


def test_interop_requires_recognized_profiles(good_capability_bytes: bytes,
                                              good_smart_bytes: bytes) -> None:
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


@given(facts=facts_strategy(),
       smart_auth=st.booleans(), smart_token=st.booleans(), smart_parsed=st.booleans(),
       ok=st.booleans(), elapsed=st.integers(min_value=0, max_value=60_000))
def test_scores_always_bounded_and_grade_valid(facts: CapabilityFacts, smart_auth: bool,
                                               smart_token: bool, smart_parsed: bool,
                                               ok: bool, elapsed: int) -> None:
    smart = SmartFacts(parsed=smart_parsed, has_authorization_endpoint=smart_auth,
                       has_token_endpoint=smart_token)
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
        return tuple(DimensionScore(key=k, title=k, score=score, findings=())
                     for k in ("reachability", "transparency", "interop"))
    assert letter(dims(95), reachable=True) == "A"
    assert letter(dims(85), reachable=True) == "B"
    assert letter(dims(75), reachable=True) == "C"
    assert letter(dims(65), reachable=True) == "D"
    assert letter(dims(10), reachable=True) == "F"
    # Unreachable is not a letter at all: F is what a graded endpoint earns, and a reader
    # compares an F against a C.
    assert letter(dims(100), reachable=False) == NOT_OBSERVED
    unscored = (DimensionScore(key="reachability", title="r", score=100, findings=()),
                DimensionScore(key="transparency", title="t", score=None, findings=()),
                DimensionScore(key="interop", title="i", score=None, findings=()))
    assert letter(unscored, reachable=True) == NOT_OBSERVED


def test_kind_defaults_and_propagates(good_capability_bytes: bytes,
                                      good_smart_bytes: bytes) -> None:
    default = build_scorecard("x", "X", _fetch(True), parse_capability(good_capability_bytes),
                              parse_smart(good_smart_bytes))
    assert default.kind == "reference"
    payer = build_scorecard("y", "Y", _fetch(True), parse_capability(good_capability_bytes),
                            parse_smart(good_smart_bytes), kind="payer")
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
        "resourceType": "CapabilityStatement", "fhirVersion": "5.0.0",
        "software": {"name": "S", "version": "1"},
        "rest": [{"mode": "server", "resource": [
            {"type": t, "interaction": [{"code": "read"}]}
            for t in ["Patient", "Observation", "Encounter", "Condition", "Procedure"]]}],
    }
    facts = parse_capability(json.dumps(doc).encode())
    as_r4 = grade_transparency(facts, version_prefix="4.")
    as_r5 = grade_transparency(facts, version_prefix="5.")
    assert not next(f for f in as_r4.findings if f.code == "T1").ok
    assert next(f for f in as_r5.findings if f.code == "T1").ok
    assert as_r5.score > as_r4.score
    assert "expected 5.x" in next(f for f in as_r5.findings if f.code == "T1").message


def test_consensus_rescues_a_vantage_local_failure(good_capability_bytes: bytes,
                                                   good_smart_bytes: bytes) -> None:
    """The real 2026-08-05 case: a live payer endpoint looked dead from one network because a
    middlebox intercepted TLS. Another vantage reaching it must settle the question."""
    from fhir_scorecard.vantage import VantageProbe, reconcile

    blocked = FetchResult(url="https://x.test/metadata", ok=False, status=None, elapsed_ms=0,
                          body=b"", error="TLS certificate verification failed")
    consensus = reconcile([
        VantageProbe("home", False, 0, "TLS certificate verification failed"),
        VantageProbe("ci", True, 640),
    ])
    card = build_scorecard("cap", "Capital", blocked,
                           parse_capability(good_capability_bytes),
                           parse_smart(good_smart_bytes), consensus=consensus)
    assert card.reachable
    assert card.grade != "F"
    assert "property of that network" in card.vantage_note
    r1 = next(f for f in card.dimensions[0].findings if f.code == "R1")
    assert r1.ok

    # Without the second vantage, the same run still fails closed.
    alone = build_scorecard("cap", "Capital", blocked,
                            NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED)
    assert not alone.reachable and alone.grade == NOT_OBSERVED


def test_unanimous_failure_is_not_observed_not_f(good_capability_bytes: bytes) -> None:
    from fhir_scorecard.vantage import VantageProbe, reconcile
    down = FetchResult(url="https://x.test/metadata", ok=False, status=404, elapsed_ms=0,
                       body=b"", error="HTTP 404")
    consensus = reconcile([VantageProbe("home", False, 0, "HTTP 404"),
                           VantageProbe("ci", False, 0, "HTTP 404")])
    card = build_scorecard("gone", "Gone", down, NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED,
                           consensus=consensus)
    assert not card.reachable and card.grade == NOT_OBSERVED
    # Every vantage failing is reported as what it is, a failure to reach from the vantages
    # tried, rather than as a settled fact about the endpoint.
    assert "not reached from any of the 2 vantages tried" in card.vantage_note
    assert "HTTP 404" in card.vantage_note
