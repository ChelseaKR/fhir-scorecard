"""The app-to-server block has to answer from the documents, and say which of five things it means.

Each test pins one way the block could publish something the documents did not say:

* an absent OPTIONAL field read as a refusal;
* a present, empty list read as an absent field, or the other way round;
* ``{}`` described as "could not be read" when it was read and is empty;
* a document the vantage asked for and was not served described as unreadable;
* anything here moving a grade.

The expected answers for the captured fixtures are written out by hand from the documents, so
the module cannot agree with itself by construction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from fhir_scorecard.backend import (
    ANSWERS,
    DECLARED,
    FIELD_ABSENT,
    NOT_LISTED,
    NOT_RETRIEVED,
    QUESTIONS,
    UNREADABLE,
    answers,
    block_html,
    block_json,
    check_lines,
)
from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    SMART_NOT_SERVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.grading import grade_interop

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _smart(doc: object) -> object:
    return parse_smart(json.dumps(doc).encode())


def _answer(smart: object, facts: object, key: str) -> str:
    by_key = {a.question.key: a.answer for a in answers(smart, facts)}  # type: ignore[arg-type]
    return by_key[key]


READABLE_CAPABILITY = parse_capability(
    (FIXTURES / "inferno-reference" / "metadata.json").read_bytes()
)


# --- the five answers are five different things ---


@pytest.mark.parametrize(
    ("smart", "expected"),
    [
        (_smart({"token_endpoint_auth_methods_supported": ["private_key_jwt"]}), DECLARED),
        (_smart({"token_endpoint_auth_methods_supported": ["client_secret_basic"]}), NOT_LISTED),
        (_smart({"token_endpoint_auth_methods_supported": []}), NOT_LISTED),
        (_smart({"authorization_endpoint": "https://a.test/auth"}), FIELD_ABSENT),
        (_smart({"token_endpoint_auth_methods_supported": "private_key_jwt"}), UNREADABLE),
        (parse_smart(b"not json"), UNREADABLE),
        (parse_smart(b"{}"), FIELD_ABSENT),
        (SMART_NOT_SERVED, NOT_RETRIEVED),
        (NO_SMART_RETRIEVED, NOT_RETRIEVED),
    ],
)
def test_one_field_can_be_in_any_of_five_states(smart: object, expected: str) -> None:
    assert _answer(smart, READABLE_CAPABILITY, "private_key_jwt") == expected


def test_an_absent_optional_field_is_never_published_as_a_refusal() -> None:
    """``token_endpoint_auth_methods_supported`` is OPTIONAL in the retained SMART page, so a
    document without it has said nothing about ``private_key_jwt``."""
    (row,) = [
        line
        for line in check_lines(
            _smart({"grant_types_supported": ["authorization_code"]}), READABLE_CAPABILITY
        )
        if "private_key_jwt:" in line
    ]
    assert "not declared: the field is absent" in row
    assert "not listed" not in row


def test_absent_and_empty_are_published_differently() -> None:
    """A present, empty ``capabilities`` list and a document with no ``capabilities`` field at
    all are two different statements, and the block publishes them as two different answers."""
    present_and_empty = _smart({"capabilities": []})
    field_missing = _smart({"scopes_supported": ["openid"]})
    assert _answer(present_and_empty, READABLE_CAPABILITY, "client_confidential_asymmetric") == (
        NOT_LISTED
    )
    assert _answer(field_missing, READABLE_CAPABILITY, "client_confidential_asymmetric") == (
        FIELD_ABSENT
    )
    payload = block_json(present_and_empty, READABLE_CAPABILITY)
    rows = {row["key"]: row for row in payload["answers"]}  # type: ignore[union-attr]
    assert rows["client_confidential_asymmetric"]["detail"] == "capabilities is present and empty"


def test_an_empty_object_was_read_and_declares_nothing() -> None:
    """Six live endpoints served exactly ``{}`` on 2026-09-10."""
    for question in QUESTIONS[:4]:
        assert _answer(parse_smart(b"{}"), READABLE_CAPABILITY, question.key) == FIELD_ABSENT


def test_a_document_not_served_is_not_described_as_unreadable() -> None:
    """The answer is the claim, not the wording. Deleting the not-served branch leaves the
    detail saying "requested and not served" - it is the parse error - while the answer becomes
    ``unreadable``, so a test that read only the detail passed over the broken property."""
    got = {a.question.key: a for a in answers(SMART_NOT_SERVED, READABLE_CAPABILITY)}
    assert got["private_key_jwt"].answer == NOT_RETRIEVED
    assert got["private_key_jwt"].answer != UNREADABLE
    assert "requested and not served" in got["private_key_jwt"].detail
    assert ANSWERS[got["private_key_jwt"].answer] == "not retrieved on this run"


def test_every_answer_has_words_and_every_rendered_answer_is_documented() -> None:
    rendered = {a.answer for a in answers(parse_smart(b"{}"), READABLE_CAPABILITY)}
    assert rendered <= set(ANSWERS)
    assert len(set(ANSWERS.values())) == len(ANSWERS)


# --- the captured fixtures, answered by hand ---


def test_the_captured_payer_document_is_answered_as_written() -> None:
    """cms-blue-button-2's SMART document has no token_endpoint_auth_methods_supported, grants
    authorization_code only, lists eight capabilities without client-confidential-asymmetric, and
    nine scopes none of which is a system/ scope."""
    smart = parse_smart((FIXTURES / "cms-blue-button-2" / "smart.json").read_bytes())
    facts = parse_capability((FIXTURES / "cms-blue-button-2" / "metadata.json").read_bytes())
    got = {a.question.key: a.answer for a in answers(smart, facts)}
    assert got["private_key_jwt"] == FIELD_ABSENT
    assert got["client_credentials"] == NOT_LISTED
    assert got["client_confidential_asymmetric"] == NOT_LISTED
    assert got["system_scopes"] == NOT_LISTED


def test_the_captured_reference_server_declares_backend_services() -> None:
    smart = parse_smart((FIXTURES / "inferno-reference" / "smart.json").read_bytes())
    got = {a.question.key: a.answer for a in answers(smart, READABLE_CAPABILITY)}
    assert got["private_key_jwt"] == DECLARED
    assert got["client_credentials"] == DECLARED
    assert got["client_confidential_asymmetric"] == DECLARED
    assert got["export_operation"] == DECLARED


def test_a_capability_statement_nobody_retrieved_answers_the_bulk_questions_as_not_retrieved() -> (
    None
):
    got = {a.question.key: a.answer for a in answers(NO_SMART_RETRIEVED, NO_CAPABILITY_RETRIEVED)}
    assert got["export_operation"] == NOT_RETRIEVED
    assert got["bulk_data_guide"] == NOT_RETRIEVED


def test_an_unreadable_capability_statement_is_unreadable_for_the_bulk_questions() -> None:
    facts = parse_capability(json.dumps({"resourceType": "OperationOutcome"}).encode())
    got = {a.question.key: a.answer for a in answers(NO_SMART_RETRIEVED, facts)}
    assert got["export_operation"] == UNREADABLE


# --- nothing here grades ---


@pytest.mark.parametrize(
    "kind", ["payer", "payer_provider_directory", "provider", "ehr", "reference"]
)
@pytest.mark.parametrize(
    "fixture", ["cms-blue-button-2", "inferno-reference", "oracle-health-open"]
)
def test_the_not_served_constant_grades_exactly_as_the_empty_body_it_replaces(
    fixture: str, kind: str
) -> None:
    """``SMART_NOT_SERVED`` replaces ``parse_smart(b"")`` where a vantage asked and was not served.
    The interop dimension - score, findings, messages - has to be identical, or a grade moved."""
    facts = parse_capability((FIXTURES / fixture / "metadata.json").read_bytes())
    assert asdict(grade_interop(facts, SMART_NOT_SERVED, kind=kind)) == asdict(
        grade_interop(facts, parse_smart(b""), kind=kind)
    )


def _capability_with(**extra: object) -> object:
    doc = {
        "resourceType": "CapabilityStatement",
        "rest": [{"mode": "server", "resource": [{"type": "Patient"}]}],
        **extra,
    }
    return parse_capability(json.dumps(doc).encode())


def test_the_bulk_data_guide_is_read_from_instantiates() -> None:
    bulk = _capability_with(
        instantiates=["http://hl7.org/fhir/uv/bulkdata/CapabilityStatement/bulk-data"]
    )
    other = _capability_with(
        instantiates=["http://hl7.org/fhir/us/core/CapabilityStatement/us-core-server"]
    )
    assert _answer(NO_SMART_RETRIEVED, bulk, "bulk_data_guide") == DECLARED
    assert _answer(NO_SMART_RETRIEVED, other, "bulk_data_guide") == NOT_LISTED
    assert _answer(NO_SMART_RETRIEVED, _capability_with(), "bulk_data_guide") == NOT_LISTED


def test_the_block_is_escaped_and_cites_only_retained_pages() -> None:
    """The one third-party string a detail carries is an instantiated canonical, so that is where
    this test puts the markup. A value that never reaches the page would prove nothing, which is
    what an earlier version of this test did by putting it in a SMART value no detail prints."""
    facts = _capability_with(instantiates=["http://x.test/<b>bulkdata</b>"])
    html_out = block_html(parse_smart(b"{}"), facts)
    assert "<b>bulkdata</b>" not in html_out
    assert "&lt;b&gt;bulkdata&lt;/b&gt;" in html_out
    cited = {a.question.citation for a in answers(parse_smart(b"{}"), facts)}
    assert cited == {
        "https://hl7.org/fhir/smart-app-launch/conformance.html",
        "https://hl7.org/fhir/R4/capabilitystatement.html",
    }


@pytest.mark.parametrize("body", [b"[]", b"null", b'"a string"', b"3"])
def test_json_that_is_not_an_object_is_unreadable_and_is_not_the_empty_object(body: bytes) -> None:
    """``{}`` was the only input that reached this refusal before #97 gave it a state of its own,
    so this is what keeps "not an object" tested: a JSON value that is not an object at all is
    unreadable, which is a different answer from an object that is merely empty."""
    smart = parse_smart(body)
    assert smart.parsed is False
    assert smart.empty_object is False
    assert smart.parse_error == "JSON body is not an object"
    assert _answer(smart, READABLE_CAPABILITY, "private_key_jwt") == UNREADABLE
