"""The CMS-0057-F readiness report states what two public files show, and nothing about the rule
that this repository cannot back.

Four properties are held from the outside rather than by reading the module:

* every sentence about the regulation is in a closed list, and each entry's quoted support is
  still present in the file it names (so a doc edit that removes the support fails here);
* no date or year appears that was not passed in or quoted;
* the counts equal an independent tally of the published ``api/endpoint/<id>.json`` files;
* an unretrieved or unreadable document is never counted as a field that was not declared.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from fhir_scorecard import readiness
from fhir_scorecard.backend import ANSWERS, QUESTIONS
from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    CapabilityFacts,
    SmartFacts,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cli import main
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.site import DEFAULT_ORIGIN

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
AS_OF = "2026-10-17"


def _capability(software: str | None, *, export: bool) -> CapabilityFacts:
    doc: dict[str, object] = {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "rest": [
            {
                "mode": "server",
                "resource": [{"type": "Patient", "interaction": [{"code": "read"}]}],
                **({"operation": [{"name": "export", "definition": "x"}]} if export else {}),
            }
        ],
    }
    if software is not None:
        doc["software"] = {"name": software}
    return parse_capability(json.dumps(doc).encode())


def _smart(**fields: list[str]) -> SmartFacts:
    return parse_smart(
        json.dumps({"authorization_endpoint": "a", "token_endpoint": "t", **fields}).encode()
    )


def _card(endpoint_id: str, name: str, kind: str) -> Scorecard:
    return Scorecard(endpoint_id, name, "B", True, (), kind=kind)


def population() -> tuple[list[Scorecard], dict[str, CapabilityFacts], dict[str, SmartFacts]]:
    """Five endpoints, four of them payer-side, hand-built so the expected tallies are written
    out below rather than computed by the code under test."""
    cards = [
        _card("a", "Alpha Plan", "payer"),
        _card("b", "Bravo Plan", "payer"),
        _card("c", "Charlie Directory", "payer_provider_directory"),
        _card("d", "Delta Plan", "payer"),
        _card("e", "Echo EHR", "ehr"),
    ]
    declared = {
        "a": _capability("Acme <FHIR>", export=True),
        "b": _capability("Acme <FHIR>", export=False),
        "c": NO_CAPABILITY_RETRIEVED,
        "d": parse_capability(b"<html>sign in</html>"),
        "e": _capability("EHR", export=True),
    }
    smart = {
        "a": _smart(
            token_endpoint_auth_methods_supported=["private_key_jwt"],
            grant_types_supported=["client_credentials"],
            capabilities=["client-confidential-asymmetric"],
            scopes_supported=["system/*.read"],
        ),
        "b": _smart(),
        "c": NO_SMART_RETRIEVED,
        "d": parse_smart(b"{}"),
        "e": _smart(),
    }
    return cards, declared, smart


def build_page() -> readiness.Page:
    cards, declared, smart = population()
    return readiness.page(cards, declared, smart, as_of=AS_OF, origin=DEFAULT_ORIGIN)


def text_of(body: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


# --- the regulatory statements ---


def _normalized(path: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / path).read_text(encoding="utf-8"))


def test_every_claim_is_supported_by_words_still_present_in_the_file_it_names() -> None:
    assert readiness.CLAIMS
    for claim in readiness.CLAIMS:
        assert claim.sources, claim.claim_id
        for source in claim.sources:
            assert (ROOT / source.path).is_file(), f"{claim.claim_id}: {source.path}"
            assert source.quote in _normalized(source.path), (claim.claim_id, source)


def test_no_claim_is_presented_as_verified_against_the_rule_text() -> None:
    """The repository does not cite or retain the rule, so nothing about the rule may say it was
    checked against it. A claim is either this project's own rule or marked unverified."""
    for claim in readiness.CLAIMS:
        assert claim.status in {readiness.PROJECT_RULE, readiness.UNVERIFIED}
    regulatory = [c for c in readiness.CLAIMS if "CMS-0057-F adds" in c.text or "dates" in c.text]
    assert regulatory
    assert all(c.status == readiness.UNVERIFIED for c in regulatory)


def test_the_repository_does_not_cite_the_rule_text_so_the_unverified_status_is_true() -> None:
    """If a primary source for CMS-0057-F is ever cited here, this fails and the status words
    and the claims should be revisited against it."""
    primary = re.compile(
        r"federalregister\.gov|ecfr\.gov|cms\.gov/(?:newsroom|files)[^ ]*0057|cms-0057-f[^ ]*\.pdf",
        re.IGNORECASE,
    )
    for path in ("README.md", "ROADMAP.md", "docs/SAMPLING-FRAME.md", "CONTRIBUTING.md"):
        assert not primary.search(_normalized(path)), path
    for cohort in (ROOT / "data" / "cohorts").glob("*.json"):
        assert not primary.search(cohort.read_text(encoding="utf-8")), cohort.name


def test_every_sentence_naming_the_rule_is_a_claim_or_a_neutral_title() -> None:
    page = build_page()
    body = text_of(page.body)
    allowed = [c.text for c in readiness.CLAIMS] + [
        "CMS-0057-F readiness report",
        "preparing for CMS-0057-F.",
        "The additional APIs",
    ]
    sentences = [s.strip() for s in re.split(r"(?<=[.])\s+", body) if "CMS-0057-F" in s]
    assert sentences
    for sentence in sentences:
        assert any(a in sentence for a in allowed), sentence


def test_the_claims_table_prints_every_claim_and_its_sources() -> None:
    body = build_page().body
    for claim in readiness.CLAIMS:
        assert html.escape(claim.text) in body
        for source in claim.sources:
            assert f"{readiness.REPO_BLOB}/{source.path}" in body


def test_the_page_says_it_is_not_a_determination_and_not_a_grade() -> None:
    body = text_of(build_page().body)
    assert "not a compliance determination" in body
    assert "not a grade" in body
    assert "Nothing here changes any grade, score or weight" in body
    for forbidden in (
        "non-compliant",
        "not compliant",
        "out of compliance",
        "in compliance",
        "is compliant",
        "fails",
        "deficien",
        "at risk",
    ):
        assert forbidden not in body.lower(), forbidden


def test_no_date_appears_that_was_not_passed_in_or_quoted() -> None:
    body = text_of(build_page().body)
    scrubbed = body.replace(AS_OF, "").replace("January 2027", "")
    assert not re.findall(r"\b(?:19|20)\d{2}\b", scrubbed)
    assert not re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d", scrubbed
    )


# --- the counts ---


def test_tallies_equal_the_hand_written_expectation() -> None:
    cards, declared, smart = population()
    built = readiness.rows(cards, declared, smart)
    assert [r.endpoint_id for r in built] == ["a", "b", "d", "c"]  # payer by name, then directory
    counted = readiness.tally(built)
    # a: all declared except export (declared). b: smart fields absent, export not listed.
    # c: nothing retrieved. d: html capability (unreadable) and an empty SMART object (absent).
    expect = {
        "private_key_jwt": {"declared": 1, "field_absent": 2, "not_retrieved": 1},
        "client_credentials": {"declared": 1, "field_absent": 2, "not_retrieved": 1},
        "client_confidential_asymmetric": {"declared": 1, "field_absent": 2, "not_retrieved": 1},
        "system_scopes": {"declared": 1, "field_absent": 2, "not_retrieved": 1},
        "export_operation": {
            "declared": 1,
            "not_listed": 1,
            "unreadable": 1,
            "not_retrieved": 1,
        },
        "bulk_data_guide": {"not_listed": 2, "unreadable": 1, "not_retrieved": 1},
    }
    assert {k: dict(v) for k, v in counted.items()} == expect
    for question in QUESTIONS:
        assert sum(counted[question.key].values()) == len(built)


def test_the_ehr_is_not_counted() -> None:
    cards, declared, smart = population()
    assert "e" not in {r.endpoint_id for r in readiness.rows(cards, declared, smart)}


def test_an_empty_smart_object_is_read_and_a_missing_document_is_not() -> None:
    cards, declared, smart = population()
    by_id = {r.endpoint_id: r for r in readiness.rows(cards, declared, smart)}
    assert by_id["d"].smart == readiness.DECLARED  # "read"
    assert by_id["d"].capability == readiness.UNREADABLE
    assert by_id["c"].smart == readiness.NOT_RETRIEVED
    assert by_id["c"].capability == readiness.NOT_RETRIEVED


def test_a_document_that_was_not_retrieved_is_never_counted_as_not_declared() -> None:
    cards, declared, smart = population()
    built = readiness.rows(cards, declared, smart)
    unretrieved = next(r for r in built if r.endpoint_id == "c")
    assert {a.answer for a in unretrieved.answers} == {readiness.NOT_RETRIEVED}
    page_text = text_of(build_page().body)
    assert "CapabilityStatement: 2 read, 1 retrieved and not readable, 1 not retrieved" in page_text


def test_facts_that_were_not_kept_stop_the_build() -> None:
    cards, declared, smart = population()
    del declared["a"]
    with pytest.raises(ValueError, match="no SMART or CapabilityStatement facts"):
        readiness.rows(cards, declared, smart)


def test_software_groups_use_the_declared_name_and_keep_unread_documents_apart() -> None:
    cards, declared, smart = population()
    groups = {
        name: [r.endpoint_id for r in members]
        for name, members in readiness.by_software(readiness.rows(cards, declared, smart))
    }
    assert groups == {
        "Acme <FHIR>": ["a", "b"],
        "CapabilityStatement not read": ["d", "c"],
    }


def test_markup_in_a_third_partys_document_is_escaped() -> None:
    body = build_page().body
    assert "Acme <FHIR>" not in body
    assert "Acme &lt;FHIR&gt;" in body


def test_questions_labels_and_states_cover_the_backend_vocabulary() -> None:
    assert set(readiness.SHORT_LABELS) == {q.key for q in QUESTIONS}
    assert set(readiness.STATES) == set(ANSWERS)


def test_the_page_is_a_pure_function_of_its_inputs() -> None:
    assert build_page() == build_page()
    cards, declared, smart = population()
    shuffled = readiness.page(
        list(reversed(cards)), declared, smart, as_of=AS_OF, origin=DEFAULT_ORIGIN
    )
    assert shuffled.body == build_page().body


def test_a_build_with_no_payer_side_endpoint_says_so() -> None:
    page = readiness.page(
        [_card("e", "Echo EHR", "ehr")],
        {"e": _capability("EHR", export=False)},
        {"e": _smart()},
        as_of=AS_OF,
        origin=DEFAULT_ORIGIN,
    )
    assert "Nothing to count" in page.body
    assert "0 payer-side" in page.body


# --- the built site ---


@pytest.fixture
def site(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    code = main(
        [
            "grade",
            "--offline",
            "--fixtures",
            str(FIXTURES),
            "--registry",
            str(FIXTURES / "registry.json"),
            "--out",
            str(out),
            "--history",
            str(tmp_path / "history.json"),
        ]
    )
    assert code == 0
    return out


def test_the_build_writes_the_page_lists_it_and_links_it_from_home(site: Path) -> None:
    assert (site / readiness.READINESS_PATH / "index.html").is_file()
    assert f"/{readiness.READINESS_PATH}/" in (site / "sitemap.xml").read_text(encoding="utf-8")
    assert f'href="/{readiness.READINESS_PATH}/"' in (site / "index.html").read_text(
        encoding="utf-8"
    )


def test_the_page_satisfies_every_gate(site: Path) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.published import audit_published_grades
    from fhir_scorecard.weight import audit_weight

    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
    assert audit_published_grades(site) == []


def test_the_pages_numbers_equal_a_tally_of_the_published_endpoint_files(site: Path) -> None:
    """An independent path to the same facts: the per-endpoint JSON the build wrote."""
    expected: dict[str, dict[str, int]] = {q.key: {} for q in QUESTIONS}
    payer_side = 0
    for path in sorted((site / "api" / "endpoint").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["endpoint"]["kind"] not in readiness.PAYER_KINDS:
            continue
        payer_side += 1
        for answer in data["app_to_server"]["answers"]:
            counts = expected[answer["key"]]
            counts[answer["answer"]] = counts.get(answer["answer"], 0) + 1
    assert payer_side == 3

    body = (site / readiness.READINESS_PATH / "index.html").read_text(encoding="utf-8")
    assert f"What {payer_side} payer-side FHIR endpoints" in text_of(body)
    for question in QUESTIONS:
        row = re.search(
            rf"<tr><th scope=\"row\">{re.escape(html.escape(question.asks))}</th>(.*?)</tr>",
            body,
        )
        assert row, question.key
        cells = [int(n) for n in re.findall(r"<td>(\d+)</td>", row.group(1))]
        assert cells == [expected[question.key].get(state, 0) for state in readiness.STATES]


def test_the_page_is_byte_identical_across_two_builds(tmp_path: Path, site: Path) -> None:
    again = tmp_path / "again"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(FIXTURES),
                "--registry",
                str(FIXTURES / "registry.json"),
                "--out",
                str(again),
                "--history",
                str(tmp_path / "history2.json"),
            ]
        )
        == 0
    )
    name = readiness.READINESS_PATH
    one = text_of((site / name / "index.html").read_text(encoding="utf-8"))
    two = text_of((again / name / "index.html").read_text(encoding="utf-8"))
    assert one == two


def test_no_grade_or_score_elsewhere_depends_on_the_report(site: Path) -> None:
    csv_text = (site / "dataset.csv").read_text(encoding="utf-8")
    assert "0057" not in csv_text
    assert "readiness" not in csv_text
