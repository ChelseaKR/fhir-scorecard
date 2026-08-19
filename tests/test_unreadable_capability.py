"""A document that arrived and is not a CapabilityStatement must not be graded as if it were one.

Companion to ``test_not_observed.py``, one step in. That file pins the case where no vantage
retrieved anything. This one pins the case that reaches further into the grader: the endpoint
answered ``/metadata`` with HTTP 200, and the body is an OperationOutcome, a sign-in page, a
search Bundle, or nothing at all.

``grade_transparency`` has always handled that input honestly (one T0 finding, full weight).
``grade_interop`` did not: it ran I1 and I3 against a ``CapabilityFacts`` whose fields were
dataclass defaults, and published

    I1  no profile canonical declared in rest.resource.supportedProfile,
        rest.resource.profile, instantiates, imports, or meta.profile
    I3  no OAuth security service declared

for an endpoint whose CapabilityStatement nobody had read. I1's message names five elements as
checked and not one of them existed to check. Both findings render on the endpoint page next to a
spec citation, under the name of a real health insurer, which is exactly the shape of claim the
project's own README forbids: "every finding describes a document this project actually
retrieved."

The replacement carries the same points, so this is a correction to what the site *says*, never
to what it scores. ``test_scores_and_letters_are_unchanged`` is the part that has to keep passing.
"""

from __future__ import annotations

import json

import pytest
from conftest import good_capability, good_smart

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
    Finding,
    build_scorecard,
    grade_interop,
    grade_transparency,
)
from fhir_scorecard.site import _FINDING_DOCS, endpoint_page, how_we_grade_page

# Real servers answer /metadata with every one of these. An OperationOutcome under HTTP 200 is
# the FHIR-native way to say "I will not serve you this", and a sign-in page is what an
# authenticating gateway returns when it decides a probe is a browser.
UNREADABLE_BODIES = {
    "operation-outcome": (
        b'{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"login",'
        b'"diagnostics":"Authorization required"}]}',
        "resourceType is 'OperationOutcome'",
    ),
    "sign-in-page": (b"<!doctype html><html><body>Sign in</body></html>", "not JSON"),
    "empty-body": (b"", "not JSON"),
    "search-bundle": (
        b'{"resourceType":"Bundle","type":"searchset","total":0,"entry":[]}',
        "resourceType is 'Bundle'",
    ),
    "json-array": (b"[]", "JSON body is not an object"),
    "json-null": (b"null", "JSON body is not an object"),
}

# The two sentences the grader used to publish about a document it had not read. Neither may
# appear anywhere in the output for any of the bodies above.
_UNSUPPORTED_CLAIMS = (
    "no profile canonical declared in",
    "no OAuth security service declared",
    "profile canonical(s) declared in",
    "US Core / CARIN / Da Vinci profiles declared in",
)


def _answered_200(body: bytes) -> FetchResult:
    """A live 200 from a reachable host. The endpoint is up; the body is not what it should be."""
    return FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=412,
        body=body,
        error=None,
    )


def _readable_facts() -> CapabilityFacts:
    return parse_capability(json.dumps(good_capability()).encode())


def _readable_smart() -> SmartFacts:
    return parse_smart(json.dumps(good_smart()).encode())


@pytest.mark.parametrize("label", sorted(UNREADABLE_BODIES))
@pytest.mark.parametrize("kind", ["payer", "payer_provider_directory", "ehr", "reference"])
def test_interop_makes_no_claim_about_a_document_it_could_not_read(label: str, kind: str) -> None:
    body, _ = UNREADABLE_BODIES[label]
    dimension = grade_interop(parse_capability(body), _readable_smart(), kind=kind)
    published = " ".join(f.message for f in dimension.findings)
    for claim in _UNSUPPORTED_CLAIMS:
        assert claim not in published, f"{label}/{kind} still publishes {claim!r}"
    assert not any(f.code in {"I1", "I4"} for f in dimension.findings)


@pytest.mark.parametrize("label", sorted(UNREADABLE_BODIES))
def test_i0_names_why_the_document_could_not_be_read(label: str) -> None:
    body, expected_reason = UNREADABLE_BODIES[label]
    dimension = grade_interop(parse_capability(body), _readable_smart(), kind="payer")
    i0 = next(f for f in dimension.findings if f.code == "I0")
    assert not i0.ok
    assert i0.points == 0
    # Observed, not "not observed": something did arrive, and answering /metadata with this is a
    # fact about the endpoint. The NR treatment would understate what was measured.
    assert i0.observed
    assert expected_reason in i0.message
    assert "CapabilityStatement unreadable" in i0.message


@pytest.mark.parametrize("label", sorted(UNREADABLE_BODIES))
def test_transparency_and_interop_agree_that_nothing_was_readable(label: str) -> None:
    """T0 and I0 are the same judgement about the same document, in two dimensions."""
    body, _ = UNREADABLE_BODIES[label]
    facts = parse_capability(body)
    t_codes = [f.code for f in grade_transparency(facts).findings]
    i_codes = [f.code for f in grade_interop(facts, _readable_smart(), kind="payer").findings]
    assert t_codes == ["T0"]
    assert i_codes[0] == "I0"


@pytest.mark.parametrize("label", sorted(UNREADABLE_BODIES))
def test_smart_discovery_is_still_graded_on_its_own_evidence(label: str) -> None:
    """Two documents were requested, and only one of them was unreadable.

    Folding SMART into I0 would throw away a retrieval that succeeded, which is the mirror image
    of the bug: it would decline to credit an endpoint for something this run did observe.
    """
    body, _ = UNREADABLE_BODIES[label]
    facts = parse_capability(body)

    with_smart = grade_interop(facts, _readable_smart(), kind="payer")
    i2 = next(f for f in with_smart.findings if f.code == "I2")
    assert i2.ok and i2.points == 35

    without_smart = grade_interop(facts, parse_smart(b"{}"), kind="payer")
    i2_missing = next(f for f in without_smart.findings if f.code == "I2")
    assert not i2_missing.ok and i2_missing.points == 0

    unasked = grade_interop(facts, NO_SMART_RETRIEVED, kind="payer")
    i2_unasked = next(f for f in unasked.findings if f.code == "I2")
    assert not i2_unasked.observed and i2_unasked.max_points == 0


@pytest.mark.parametrize("label", sorted(UNREADABLE_BODIES))
@pytest.mark.parametrize(
    ("kind", "smart_kind", "expected_score", "expected_max"),
    [
        # (dimension score, I0's max_points) as the grader produced them before this change,
        # computed from I1's 40 plus I3's 25 where I3 applies. These numbers are the contract:
        # an unreadable document may not move a score in either direction.
        ("payer", "good", 35, 65),
        ("payer", "empty", 0, 65),
        ("payer", "unasked", 0, 65),
        ("ehr", "good", 35, 65),
        ("reference", "good", 35, 65),
        # A Provider Directory API is not scored on SMART or OAuth at all, so I0 carries I1 alone.
        ("payer_provider_directory", "good", 0, 40),
        ("payer_provider_directory", "unasked", 0, 40),
    ],
)
def test_scores_and_letters_are_unchanged(
    label: str, kind: str, smart_kind: str, expected_score: int, expected_max: int
) -> None:
    body, _ = UNREADABLE_BODIES[label]
    smart = {
        "good": _readable_smart(),
        "empty": parse_smart(b"{}"),
        "unasked": NO_SMART_RETRIEVED,
    }[smart_kind]
    facts = parse_capability(body)

    dimension = grade_interop(facts, smart, kind=kind)
    assert dimension.score == expected_score
    assert next(f for f in dimension.findings if f.code == "I0").max_points == expected_max

    card = build_scorecard(
        "payer", "Payer Health Plan", _answered_200(body), facts, smart, kind=kind
    )
    # Reachable and answering: this is an F, not "not observed". The endpoint did respond, and
    # what it responded with is the finding.
    assert card.reachable
    assert card.grade == "F"


def test_i0_carries_exactly_what_the_checks_it_replaces_would_have_carried() -> None:
    """The denominator is the whole guarantee, so it is asserted against the readable path."""
    readable = grade_interop(_readable_facts(), _readable_smart(), kind="payer")
    replaced = sum(f.max_points for f in readable.findings if f.code in {"I1", "I3"})
    unreadable = grade_interop(parse_capability(b"[]"), _readable_smart(), kind="payer")
    assert next(f for f in unreadable.findings if f.code == "I0").max_points == replaced
    assert sum(f.max_points for f in readable.findings) == sum(
        f.max_points for f in unreadable.findings
    )


def test_a_readable_capabilitystatement_is_untouched() -> None:
    dimension = grade_interop(_readable_facts(), _readable_smart(), kind="payer")
    codes = [f.code for f in dimension.findings]
    assert "I0" not in codes
    assert codes == ["I1", "I2", "I3"]
    assert dimension.score == 100


def test_nothing_retrieved_still_reports_not_observed_rather_than_i0() -> None:
    """The two cases stay distinct: no document at all is not the same as an unreadable one."""
    dimension = grade_interop(NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED, kind="payer")
    assert [f.code for f in dimension.findings] == ["NR"]
    assert dimension.score is None
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        FetchResult(
            url="https://payer.test/r4/metadata",
            ok=False,
            status=None,
            elapsed_ms=0,
            body=b"",
            error="connection timed out",
        ),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    assert card.grade == NOT_OBSERVED


def test_the_endpoint_page_does_not_render_an_unsupported_claim() -> None:
    """The regression has to be pinned where a reader actually meets it."""
    body, _ = UNREADABLE_BODIES["operation-outcome"]
    card = build_scorecard(
        "payer-health-plan",
        "Payer Health Plan",
        _answered_200(body),
        parse_capability(body),
        _readable_smart(),
        kind="payer",
    )
    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live CapabilityStatement fetch (recorded 2026-08-07)",
        origin="https://example.test",
    )
    for claim in _UNSUPPORTED_CLAIMS:
        assert claim not in page.body
    assert "CapabilityStatement unreadable" in page.body


# --- every finding code the grader can emit must resolve to a definition -------------------
#
# site.py renders each finding code as a link to /how-we-grade/#<code>. A code with no entry in
# _FINDING_DOCS renders as a link to an anchor that does not exist, which is how T0 shipped: the
# grader has emitted it since v0.1 and the methodology page never defined it. Adding I0 without
# this test would have been the same mistake twice.

_ALL_CODES = {"R1", "R2", "NR", "T0", "T1", "T2", "T3", "T4", "I0", "I1", "I2", "I3", "I4"}


def _every_code_the_grader_emits() -> set[str]:
    """Drive grading over inputs that between them reach every branch that emits a finding."""
    smart_variants = [_readable_smart(), parse_smart(b"{}"), NO_SMART_RETRIEVED]
    capability_variants = [
        _readable_facts(),
        # Declares nothing, so I1 fails and I4 fires off the prose in the title.
        parse_capability(
            json.dumps(
                {
                    "resourceType": "CapabilityStatement",
                    "fhirVersion": "4.0.1",
                    "title": "CARIN Patient Access Implementation",
                    "rest": [{"mode": "server", "resource": [{"type": "Patient"}]}],
                }
            ).encode()
        ),
        parse_capability(b'{"resourceType":"OperationOutcome"}'),
        NO_CAPABILITY_RETRIEVED,
    ]
    reachability = [
        _answered_200(b"{}"),
        FetchResult(
            url="https://payer.test/r4/metadata",
            ok=False,
            status=503,
            elapsed_ms=9,
            body=b"",
            error="HTTP 503",
        ),
    ]
    codes: set[str] = set()
    for facts in capability_variants:
        for smart in smart_variants:
            for kind in ("payer", "payer_provider_directory", "reference"):
                for metadata in reachability:
                    card = build_scorecard(
                        "e", "E", metadata, facts, smart, kind=kind, vantage="test"
                    )
                    codes.update(f.code for d in card.dimensions for f in d.findings)
    return codes


def test_the_matrix_reaches_every_documented_code() -> None:
    assert _every_code_the_grader_emits() == _ALL_CODES


def test_every_emitted_finding_code_has_a_published_definition() -> None:
    documented = {code for code, *_ in _FINDING_DOCS}
    undocumented = _every_code_the_grader_emits() - documented
    assert not undocumented, (
        f"{sorted(undocumented)} render as links to /how-we-grade/ anchors that do not exist"
    )


def test_the_methodology_page_carries_an_anchor_for_every_code() -> None:
    page = how_we_grade_page("https://example.test")
    for code in _ALL_CODES:
        assert f'id="{code}"' in page.body, f"how-we-grade has no anchor for {code}"


def test_finding_docs_has_no_entry_for_a_code_the_grader_cannot_emit() -> None:
    """A definition for a code nothing produces is documentation of a check that does not run."""
    documented = {code for code, *_ in _FINDING_DOCS}
    assert documented == _ALL_CODES
    assert len(documented) == len(_FINDING_DOCS), "duplicate code in _FINDING_DOCS"


def test_finding_is_still_frozen() -> None:
    """I0 is constructed positionally nowhere; guard the dataclass contract it relies on."""
    finding = Finding(code="I0", ok=False, points=0, max_points=65, message="m", citation="c")
    with pytest.raises(AttributeError):
        finding.points = 40  # type: ignore[misc]
