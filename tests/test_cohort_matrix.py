"""The cohort census has to count what was read, out of what was read, within one kind.

Each test pins one way a cohort aggregate can be wrong while looking right:

* an endpoint whose document was not read is folded into the denominator as "does not declare";
* two kinds are added together into a count that describes neither;
* two member organizations on one surface count one endpoint twice;
* an interaction code outside R4's list falls off the edge of a fixed table;
* a share of one is published as 100%;
* a page links a census the build did not write.

The expected numbers are computed here from the documents, not read back from the module.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    CapabilityFacts,
    parse_capability,
)
from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.cohort_matrix import R4_TYPE_INTERACTIONS, census, censuses, pages_for
from fhir_scorecard.matrix import TABLE_BYTE_BUDGET
from fhir_scorecard.site import DEFAULT_ORIGIN, cohort_page
from fhir_scorecard.weight import MAX_PAGE_BYTES

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cohort(*members: tuple[str, tuple[str, ...]]) -> Cohort:
    return Cohort(
        cohort_id="testville",
        name="Testville",
        description="A fixture cohort.",
        notes=(),
        sources=(),
        members=tuple(
            CohortMember(member_id=mid, name=mid.title(), programs=(), endpoint_ids=ids)
            for mid, ids in members
        ),
    )


def _declares(*resources: str, codes: tuple[str, ...] = ("read", "search-type")) -> CapabilityFacts:
    return parse_capability(
        json.dumps(
            {
                "resourceType": "CapabilityStatement",
                "rest": [
                    {
                        "mode": "server",
                        "resource": [
                            {"type": r, "interaction": [{"code": c} for c in codes]}
                            for r in resources
                        ],
                    }
                ],
            }
        ).encode()
    )


UNREADABLE_DOC = parse_capability(json.dumps({"resourceType": "OperationOutcome"}).encode())
EMPTY_STATEMENT = parse_capability(json.dumps({"resourceType": "CapabilityStatement"}).encode())


def _cells(body: str, resource: str) -> list[str]:
    start = body.index(f'<th scope="row">{resource}</th>')
    row = body[start : body.index("</tr>", start)]
    # Each chunk ends at a </td>; the first one also carries the row's <th>, so a cell is the
    # text after the chunk's last <td>, never after its first '>'.
    return [cell.rsplit("<td>", 1)[1] for cell in row.split("</td>")[:-1] if "<td>" in cell]


# --- populations ---


def test_an_unread_document_is_stated_and_never_counted_as_declaring_nothing() -> None:
    cohort = _cohort(("a", ("read-it",)), ("b", ("unreadable",)), ("c", ("unretrieved",)))
    declared = {
        "read-it": _declares("Patient"),
        "unreadable": UNREADABLE_DOC,
        "unretrieved": NO_CAPABILITY_RETRIEVED,
    }
    kinds = dict.fromkeys(declared, "payer")
    item = census(cohort, "payer", declared, kinds)
    assert item.readable == ("read-it",)
    assert item.unreadable == ("unreadable",)
    assert item.not_retrieved == ("unretrieved",)
    (page,) = pages_for(item)
    assert "3 listed" in page.body
    assert "<strong>1</strong> with a readable declaration" in page.body
    assert "Every count below is out of the 1 with a readable declaration" in page.body
    assert _cells(page.body, "Patient")[0] == "1 of 1"
    assert 'href="/endpoint/unreadable/"' in page.body
    assert 'href="/endpoint/unretrieved/"' in page.body


def test_a_statement_that_declares_nothing_is_in_the_denominator() -> None:
    """It was read, and what it says is that it declares no Patient. That is a count."""
    cohort = _cohort(("a", ("full",)), ("b", ("empty",)))
    declared = {"full": _declares("Patient"), "empty": EMPTY_STATEMENT}
    item = census(cohort, "payer", declared, dict.fromkeys(declared, "payer"))
    assert item.readable == ("full", "empty")
    (page,) = pages_for(item)
    assert _cells(page.body, "Patient")[0] == "1 of 2"
    assert "Every count below is out of" not in page.body


def test_the_populations_always_add_up_to_the_listed_endpoints() -> None:
    cohort = _cohort(("a", ("x", "y")), ("b", ("z",)))
    declared = {"x": _declares("Patient"), "y": UNREADABLE_DOC, "z": NO_CAPABILITY_RETRIEVED}
    item = census(cohort, "payer", declared, dict.fromkeys(declared, "payer"))
    assert len(item.readable) + len(item.unreadable) + len(item.not_retrieved) == len(item.listed)
    assert len(item.listed) == 3


# --- within a kind ---


def test_kinds_are_counted_apart_and_never_added() -> None:
    cohort = _cohort(("plan", ("access", "directory")))
    declared = {"access": _declares("Patient", "Coverage"), "directory": _declares("Practitioner")}
    kinds = {"access": "payer", "directory": "payer_provider_directory"}
    items = {item.kind: item for item in censuses(cohort, declared, kinds)}
    assert set(items) == {"payer", "payer_provider_directory"}
    assert dict(items["payer"].resources) == {"Coverage": 1, "Patient": 1}
    assert dict(items["payer_provider_directory"].resources) == {"Practitioner": 1}
    pages = {item.kind: pages_for(item)[0] for item in items.values()}
    assert "Practitioner" not in pages["payer"].body
    assert "Patient" not in pages["payer_provider_directory"].body
    assert pages["payer"].path != pages["payer_provider_directory"].path


def test_two_plans_on_one_surface_count_it_once() -> None:
    cohort = _cohort(("plan", ("shared",)), ("plan-hmo", ("shared",)))
    declared = {"shared": _declares("Patient")}
    item = census(cohort, "payer", declared, {"shared": "payer"})
    assert item.listed == ("shared",)
    assert _cells(pages_for(item)[0].body, "Patient")[0] == "1 of 1"


# --- what gets counted ---


def test_counts_match_the_documents_counted_independently() -> None:
    raws = {
        name: (FIXTURES / name / "metadata.json").read_bytes()
        for name in ("cms-blue-button-2", "inferno-reference", "oracle-health-open")
    }
    declared = {name: parse_capability(raw) for name, raw in raws.items()}
    cohort = _cohort(*((name, (name,)) for name in raws))
    item = census(cohort, "payer", declared, dict.fromkeys(raws, "payer"))
    expected: dict[str, int] = {}
    for raw in raws.values():
        rest = next(r for r in json.loads(raw)["rest"] if r.get("mode") == "server")
        for resource_type in {r["type"] for r in rest.get("resource", []) if r.get("type")}:
            expected[resource_type] = expected.get(resource_type, 0) + 1
    assert dict(item.resources) == expected
    assert expected


def test_an_interaction_code_outside_r4_is_listed_not_dropped() -> None:
    cohort = _cohort(("a", ("odd",)))
    declared = {"odd": _declares("Patient", codes=("read", "made-up-code"))}
    (page,) = pages_for(census(cohort, "payer", declared, {"odd": "payer"}))
    cells = _cells(page.body, "Patient")
    assert cells[1 + R4_TYPE_INTERACTIONS.index("read")] == "1"
    assert cells[-1] == "made-up-code (1)"


def test_a_share_of_one_is_a_count_and_never_a_percentage() -> None:
    cohort = _cohort(("a", ("only",)))
    (page,) = pages_for(census(cohort, "payer", {"only": _declares("Patient")}, {"only": "payer"}))
    assert "1 of 1" in page.body
    assert "%" not in page.body


def test_a_resource_name_a_payer_wrote_is_escaped() -> None:
    cohort = _cohort(("a", ("x",)))
    (page,) = pages_for(census(cohort, "payer", {"x": _declares("<b>Patient</b>")}, {"x": "payer"}))
    assert "<b>Patient</b>" not in page.body
    assert "&lt;b&gt;Patient&lt;/b&gt;" in page.body


def test_a_kind_with_nothing_readable_says_so_and_draws_no_table() -> None:
    cohort = _cohort(("a", ("gone",)))
    (page,) = pages_for(
        census(cohort, "payer", {"gone": NO_CAPABILITY_RETRIEVED}, {"gone": "payer"})
    )
    assert "<table" not in page.body
    assert "nothing to count" in page.body


# --- paging ---


def test_a_census_too_large_for_one_page_is_split_and_nothing_is_dropped() -> None:
    names = [f"Resource{index:05d}" for index in range(2500)]
    cohort = _cohort(("a", ("wide",)))
    item = census(cohort, "payer", {"wide": _declares(*names)}, {"wide": "payer"})
    pages = pages_for(item)
    assert len(pages) > 1
    listed = [
        name for page in pages for name in names if f'<th scope="row">{name}</th>' in page.body
    ]
    assert listed == names
    for page in pages:
        assert len(page.body.encode()) < MAX_PAGE_BYTES
        table = page.body[page.body.index("<tbody>") : page.body.index("</tbody>")]
        assert len(table.encode()) <= TABLE_BYTE_BUDGET + len("<tbody>")
    assert len({page.title for page in pages}) == len(pages)
    assert TABLE_BYTE_BUDGET < MAX_PAGE_BYTES


# --- the wiring ---


def test_a_cohort_page_links_its_censuses_only_when_told_they_exist() -> None:
    cohort = _cohort(("a", ("x",)))
    assert "/capabilities/" not in cohort_page(cohort, {}, DEFAULT_ORIGIN).body
    linked = cohort_page(cohort, {}, DEFAULT_ORIGIN, declared_kinds=("payer",)).body
    assert 'href="/testville/capabilities/payer/"' in linked


def test_a_built_cohort_carries_its_census_pages_and_passes_every_gate(tmp_path: Path) -> None:
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.cli import main
    from fhir_scorecard.weight import audit_weight

    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    cohorts = tmp_path / "cohorts"
    cohorts.mkdir()
    (cohorts / "fixture-cohort.json").write_text(
        json.dumps(
            {
                "cohort": {
                    "id": "fixture-cohort",
                    "name": "Fixture cohort",
                    "description": "A cohort over the captured fixture endpoints.",
                },
                "members": [
                    {
                        "id": "cms",
                        "name": "CMS",
                        "programs": ["tx-marketplace"],
                        "endpoints": ["cms-blue-button-2", "inferno-reference"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    site = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(fixtures),
                "--registry",
                str(fixtures / "registry.json"),
                "--cohorts",
                str(cohorts),
                "--out",
                str(site),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    registry = json.loads((fixtures / "registry.json").read_text(encoding="utf-8"))
    kinds = {entry["id"]: entry["kind"] for entry in registry["endpoints"]}
    for kind in {kinds["cms-blue-button-2"], kinds["inferno-reference"]}:
        page = site / "fixture-cohort" / "capabilities" / kind / "index.html"
        assert page.is_file(), page
        assert f'href="/fixture-cohort/capabilities/{kind}/"' in (
            site / "fixture-cohort" / "index.html"
        ).read_text(encoding="utf-8")
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
