"""What an endpoint declares about app-to-server access: SMART Backend Services and Bulk Data (#97).

The two documents this project retrieves say more than the graded dimensions read. The SMART
discovery document declares how a client may authenticate at the token endpoint, which grant types
and scopes exist, and a list of capability codes. The CapabilityStatement declares operations,
among them Bulk Data's ``export``, and which implementation guides it instantiates. Whether an app
can get a token without a phone call is exactly what those fields declare, and population health
and payer-to-payer use cases run on backend services and bulk export.

**Observed, never graded.** Nothing here enters a dimension, a finding, a weight, the SARIF or the
JUnit a ``check`` writes, or a letter. Grading any of it is a scoring-policy decision #97 leaves
to the maintainer, and this module is the extraction that decision would sit on.

**Five answers, never two.** Each question has five possible answers, and they are kept apart:

* ``declared``: the field lists it.
* ``not_listed``: the field is present and does not list it, including a present, empty list.
* ``field_absent``: the field is not in the document at all. ``token_endpoint_auth_methods_
  supported`` is OPTIONAL in the retained SMART App Launch page, so a document without it has said
  nothing about ``private_key_jwt``; reading that as "does not support" would publish a refusal
  nobody made.
* ``unreadable``: the document was retrieved and is not a JSON object, or the field is present and
  is not a list of strings.
* ``not_retrieved``: no vantage retrieved the document on this run.

**Citations only to retained pages.** The SMART fields cite the retained SMART App Launch
conformance page. The Bulk Data Access implementation guide is **not** retained in this project's
corpus, so the ``export`` operation is reported as a declaration in ``rest.operation``, the element
the retained CapabilityStatement page defines, and the guide is named without a quoted passage.
Retaining it is a provenance act with a retrieval date and a hash, and this change does not make it.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts, SmartFacts

DECLARED = "declared"
NOT_LISTED = "not_listed"
FIELD_ABSENT = "field_absent"
UNREADABLE = "unreadable"
NOT_RETRIEVED = "not_retrieved"

#: Every answer a question can have, with the words a reader sees. ``tests/test_backend.py`` holds
#: the renderer to this map, so an answer cannot ship without a documented meaning.
ANSWERS: dict[str, str] = {
    DECLARED: "declared",
    NOT_LISTED: "not listed",
    FIELD_ABSENT: "not declared: the field is absent",
    UNREADABLE: "could not be read",
    NOT_RETRIEVED: "not retrieved on this run",
}

_SMART_PAGE = "https://hl7.org/fhir/smart-app-launch/conformance.html"
_CAPABILITY_PAGE = "https://hl7.org/fhir/R4/capabilitystatement.html"

#: What every surface says about this block, once.
OBSERVATION_NOTE = (
    "What the endpoint's own documents declare about app-to-server access, observed on this run. "
    "Nothing here was requested or exercised, and none of it is graded."
)


@dataclass(frozen=True)
class Question:
    key: str
    asks: str
    source: str
    citation: str


@dataclass(frozen=True)
class Answer:
    question: Question
    answer: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {
            "key": self.question.key,
            "asks": self.question.asks,
            "source": self.question.source,
            "citation": self.question.citation,
            "answer": self.answer,
            # The words a reader sees, carried so a renderer never keeps its own copy of
            # the vocabulary and cannot drift from it.
            "answer_text": ANSWERS[self.answer],
            "detail": self.detail,
        }


QUESTIONS: tuple[Question, ...] = (
    Question(
        "private_key_jwt",
        "Declares private_key_jwt client authentication at the token endpoint",
        "SMART discovery: token_endpoint_auth_methods_supported",
        _SMART_PAGE,
    ),
    Question(
        "client_credentials",
        "Declares the client_credentials grant, which SMART Backend Services uses",
        "SMART discovery: grant_types_supported",
        _SMART_PAGE,
    ),
    Question(
        "client_confidential_asymmetric",
        "Declares the client-confidential-asymmetric capability",
        "SMART discovery: capabilities",
        _SMART_PAGE,
    ),
    Question(
        "system_scopes",
        "Declares system-level scopes",
        "SMART discovery: scopes_supported",
        _SMART_PAGE,
    ),
    Question(
        "export_operation",
        "Declares an export operation",
        "CapabilityStatement: rest.operation and rest.resource.operation",
        _CAPABILITY_PAGE,
    ),
    Question(
        "bulk_data_guide",
        "Instantiates the Bulk Data Access implementation guide",
        "CapabilityStatement: instantiates",
        _CAPABILITY_PAGE,
    ),
)


def _smart_answer(
    smart: SmartFacts,
    field: str,
    values: tuple[str, ...] | None,
    test: Callable[[tuple[str, ...]], tuple[bool, str]],
) -> tuple[str, str]:
    if not smart.observed:
        return NOT_RETRIEVED, "no vantage retrieved .well-known/smart-configuration"
    if smart.not_served:
        return (
            NOT_RETRIEVED,
            "the SMART discovery document was requested and not served on this run",
        )
    if smart.empty_object:
        return FIELD_ABSENT, "the SMART document is an empty JSON object, so it declares no field"
    if not smart.parsed:
        return UNREADABLE, smart.parse_error or "the SMART document is not a JSON object"
    if field in smart.malformed_fields:
        return UNREADABLE, f"{field} is present and is not a list of strings"
    if values is None:
        return FIELD_ABSENT, f"the document has no {field}"
    if not values:
        return NOT_LISTED, f"{field} is present and empty"
    hit, detail = test(values)
    return (DECLARED if hit else NOT_LISTED), detail


def _contains(wanted: str, field: str) -> Callable[[tuple[str, ...]], tuple[bool, str]]:
    def test(values: tuple[str, ...]) -> tuple[bool, str]:
        if wanted in values:
            return True, f"{field} lists {wanted} among {len(values)}"
        return False, f"{field} lists {len(values)} and not {wanted}"

    return test


def _system_scopes(values: tuple[str, ...]) -> tuple[bool, str]:
    system = [value for value in values if value.startswith("system/")]
    if system:
        return True, f"{len(system)} of {len(values)} scopes_supported are system/ scopes"
    return False, f"none of {len(values)} scopes_supported is a system/ scope"


def _capability_answer(facts: CapabilityFacts) -> tuple[str, str] | None:
    if not facts.observed:
        return NOT_RETRIEVED, "no vantage retrieved the CapabilityStatement"
    if not facts.parsed or not facts.resource_type_ok:
        return UNREADABLE, facts.parse_error or "the document is not a CapabilityStatement"
    return None


def _export(facts: CapabilityFacts) -> tuple[str, str]:
    refused = _capability_answer(facts)
    if refused is not None:
        return refused
    where = sorted(
        {
            "the whole server" if owner is None else owner
            for owner, name, _ in facts.operations
            if name.lstrip("$") == "export"
        }
    )
    if where:
        return DECLARED, "export is declared on " + ", ".join(where)
    return NOT_LISTED, f"{len(facts.operations)} operations are declared and none is named export"


def _bulk_guide(facts: CapabilityFacts) -> tuple[str, str]:
    refused = _capability_answer(facts)
    if refused is not None:
        return refused
    instantiated = [
        canonical
        for element, canonical in facts.conformance_profiles
        if element == "CapabilityStatement.instantiates"
    ]
    bulk = [canonical for canonical in instantiated if "bulkdata" in canonical.lower()]
    if bulk:
        return DECLARED, "instantiates " + ", ".join(bulk)
    return NOT_LISTED, f"{len(instantiated)} canonicals are instantiated and none is Bulk Data's"


def answers(smart: SmartFacts, facts: CapabilityFacts) -> tuple[Answer, ...]:
    """Every question, answered from the two documents this run retrieved and nothing else."""
    pairs = {
        "private_key_jwt": _smart_answer(
            smart,
            "token_endpoint_auth_methods_supported",
            smart.token_endpoint_auth_methods,
            _contains("private_key_jwt", "token_endpoint_auth_methods_supported"),
        ),
        "client_credentials": _smart_answer(
            smart,
            "grant_types_supported",
            smart.grant_types,
            _contains("client_credentials", "grant_types_supported"),
        ),
        "client_confidential_asymmetric": _smart_answer(
            smart,
            "capabilities",
            smart.capabilities,
            _contains("client-confidential-asymmetric", "capabilities"),
        ),
        "system_scopes": _smart_answer(smart, "scopes_supported", smart.scopes, _system_scopes),
        "export_operation": _export(facts),
        "bulk_data_guide": _bulk_guide(facts),
    }
    # In the order QUESTIONS gives. A question with no answerer here fails loudly at the
    # lookup rather than disappearing from every surface the block reaches.
    return tuple(Answer(question, *pairs[question.key]) for question in QUESTIONS)


def block_json(smart: SmartFacts, facts: CapabilityFacts) -> dict[str, object]:
    """The block as data, for ``api/endpoint/<id>.json`` and a ``check`` result."""
    return {
        "note": OBSERVATION_NOTE,
        "answers": [answer.as_json() for answer in answers(smart, facts)],
    }


def block_html(smart: SmartFacts, facts: CapabilityFacts) -> str:
    """The block for the endpoint page. A table, because every row has the same three facts."""
    rows = "".join(
        f'<tr><th scope="row">{html.escape(answer.question.asks)}</th>'
        f"<td>{html.escape(ANSWERS[answer.answer])}</td>"
        f"<td>{html.escape(answer.detail)}</td>"
        f'<td><a href="{html.escape(answer.question.citation)}" rel="nofollow">'
        f"{html.escape(answer.question.source)}</a></td></tr>"
        for answer in answers(smart, facts)
    )
    return (
        "<h2>Declared app-to-server access</h2>"
        f"<p>{html.escape(OBSERVATION_NOTE)} An absent field is reported as absent, not as a "
        "refusal: a document that does not mention a field has not said anything about it.</p>"
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        'aria-label="Declared app-to-server access">'
        '<table class="usa-table usa-table--striped"><caption>What the SMART discovery document '
        "and the CapabilityStatement declare about SMART Backend Services and Bulk Data</caption>"
        '<thead><tr><th scope="col">Question</th><th scope="col">Answer</th>'
        '<th scope="col">What the document says</th><th scope="col">Where it is declared</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def check_lines(smart: SmartFacts, facts: CapabilityFacts) -> list[str]:
    """The block as the lines ``check`` prints under the dimension scores."""
    return ["  declared app-to-server access (observed, not graded):"] + [
        f"    {answer.question.key}: {ANSWERS[answer.answer]} ({answer.detail})"
        for answer in answers(smart, facts)
    ]
