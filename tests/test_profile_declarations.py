"""I1 must not conclude "none declared" from one element, and must say what it read.

Measured on 2026-08-14, one /metadata request each with the project's own fetcher and
User-Agent:

* **Aetna** (`vteapif1.aetna.com/fhirdemo/v1/patientaccess`) declares no profile canonical
  anywhere: no `supportedProfile`, no `rest.resource.profile`, no `instantiates`, no `imports`,
  no `meta.profile`. Its title, name and `implementation.description` all name CARIN, and one
  names US Core. The negative is correct at the element level and the old wording overstated it.
* **CMS Blue Button 2.0** (`api.bluebutton.cms.gov/v2/fhir`) declares
  `rest.resource.profile` on all three of its resources, an element the parser never read. The
  values are base FHIR StructureDefinitions rather than US Core or CARIN, so the finding stays
  negative; it is now a negative that was earned by looking.
"""

from __future__ import annotations

import json

from conftest import good_capability

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.grading import Finding, grade_interop

_ELEMENTS = ("rest.resource.supportedProfile", "rest.resource.profile", "instantiates",
             "imports", "meta.profile")


def _bare() -> dict[str, object]:
    """A CapabilityStatement with resources and no profile declaration anywhere."""
    doc = good_capability()
    for resource in doc["rest"][0]["resource"]:  # type: ignore[index]
        resource.pop("supportedProfile")
    return doc


def _i1(doc: dict[str, object]) -> Finding:
    dim = grade_interop(parse_capability(json.dumps(doc).encode()), parse_smart(b""),
                        kind="payer")
    return next(f for f in dim.findings if f.code == "I1")


def test_i1_no_longer_asserts_absence_from_one_element() -> None:
    finding = _i1(_bare())
    assert not finding.ok
    # The claim that was made after reading one element.
    assert "no recognized interoperability profiles declared" not in finding.message
    for element in _ELEMENTS:
        assert element in finding.message


def test_a_profile_declared_in_instantiates_is_a_declaration() -> None:
    doc = _bare()
    doc["instantiates"] = ["http://hl7.org/fhir/us/core/CapabilityStatement/us-core-server"]
    finding = _i1(doc)
    assert finding.ok
    assert "instantiates" in finding.message


def test_a_profile_declared_in_imports_is_a_declaration() -> None:
    doc = _bare()
    doc["imports"] = ["http://hl7.org/fhir/us/carin-bb/CapabilityStatement/c4bb"]
    assert _i1(doc).ok


def test_a_profile_declared_in_the_singular_resource_element_is_a_declaration() -> None:
    """CMS Blue Button 2.0 uses this element; the parser read only the plural one."""
    doc = _bare()
    doc["rest"][0]["resource"][0]["profile"] = (  # type: ignore[index]
        "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient")
    finding = _i1(doc)
    assert finding.ok
    assert "rest.resource.profile" in finding.message


def test_a_profile_declared_on_the_document_itself_is_a_declaration() -> None:
    doc = _bare()
    doc["meta"] = {"profile": [
        "http://hl7.org/fhir/us/core/CapabilityStatement/us-core-server"]}
    assert _i1(doc).ok


def test_an_stu3_shaped_reference_is_still_read() -> None:
    """A shape difference is not a conformance fact; concluding absence from one would be the
    same mistake in miniature."""
    doc = _bare()
    doc["rest"][0]["resource"][0]["profile"] = {  # type: ignore[index]
        "reference": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"}
    assert _i1(doc).ok


def test_base_fhir_profiles_are_declarations_but_not_interoperability_ones() -> None:
    """The Blue Button 2.0 shape: three resources, each with a base FHIR profile. The finding
    stays negative and says what it found rather than that nothing was found."""
    doc = _bare()
    for resource in doc["rest"][0]["resource"]:  # type: ignore[index]
        resource["profile"] = f"http://hl7.org/fhir/StructureDefinition/{resource['type']}"
    finding = _i1(doc)
    assert not finding.ok
    assert "6 profile canonical(s) declared in rest.resource.profile" in finding.message
    assert "none of them US Core, CARIN, or Da Vinci" in finding.message


def test_prose_that_names_a_guide_gets_a_note_worth_nothing() -> None:
    """The Aetna case: the document says CARIN three times and US Core once, and declares no
    profile. The note is actionable and scores nothing; the grade is unchanged."""
    doc = _bare()
    doc["title"] = "Base FHIR Capability Statement AETNA's CARIN PatientAccess Implementation"
    doc["implementation"] = {"description": "AETNA implementation of FHIR on top of USCORE - CARIN"}
    dim = grade_interop(parse_capability(json.dumps(doc).encode()), parse_smart(b""),
                        kind="payer")
    note = next(f for f in dim.findings if f.code == "I4")
    assert note.max_points == 0 and note.points == 0
    assert "implementation.description, title" in note.message
    assert "rest.resource.supportedProfile" in note.message

    without_prose = grade_interop(parse_capability(json.dumps(_bare()).encode()),
                                  parse_smart(b""), kind="payer")
    assert dim.score == without_prose.score  # prose is not a conformance claim and never scores


def test_no_prose_note_when_the_profile_is_actually_declared() -> None:
    doc = good_capability()
    doc["title"] = "CARIN PatientAccess"
    dim = grade_interop(parse_capability(json.dumps(doc).encode()), parse_smart(b""),
                        kind="payer")
    assert not any(f.code == "I4" for f in dim.findings)


def test_the_drift_fingerprint_still_counts_only_supported_profile() -> None:
    """Widening what "declared profiles" means would report a change no server made."""
    from fhir_scorecard.drift import fingerprint

    doc = _bare()
    doc["instantiates"] = ["http://hl7.org/fhir/us/core/CapabilityStatement/us-core-server"]
    assert fingerprint(parse_capability(json.dumps(doc).encode()))["profile_count"] == 0
