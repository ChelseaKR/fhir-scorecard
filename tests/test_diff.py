"""The ``diff`` verb: what changed, in words, with the clause that defines it.

The four properties this file exists to hold are the ones an operator's build depends on.

*One removed interaction reads as one removed interaction.* Not as ``resource_count: 26 -> 26``,
which is what the drift fingerprint can say about it, and not as a wall of unrelated lines.

*Identical inputs produce an empty diff*, and the same inputs produce the same words every time.

*A document that could not be read diffs as unreadable against anything*, with no field-level
claims at all. The tempting failure is to compare an unreadable document's empty facts against a
good document's and report that every resource was removed. That would publish an unreadable
response as a withdrawal the server never made, which is this portfolio's most common defect
wearing a diff's clothes.

*The verb and the recorded timeline cannot disagree*, because there is one implementation of
"what moved between two fingerprints" and both call it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard import grading
from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cli import main
from fhir_scorecard.diff import (
    FHIR_CAPS,
    Change,
    DiffReport,
    classify,
    diff_bytes,
    diff_capability,
    diff_paths,
    diff_scorecards,
    diff_smart,
    render_json,
    render_text,
)
from fhir_scorecard.drift import fingerprint, fingerprint_changes, observe
from tests.conftest import good_capability, good_smart


def _without_interaction(doc: dict[str, Any], resource_type: str, code: str) -> dict[str, Any]:
    """The same document with one interaction removed, and nothing else touched.

    Asserts the removal actually happened. A helper that silently no-opped would leave every
    test below comparing a document to itself and passing.
    """
    edited = json.loads(json.dumps(doc))
    removed = 0
    for rest in edited["rest"]:
        for resource in rest["resource"]:
            if resource["type"] != resource_type:
                continue
            before = len(resource["interaction"])
            resource["interaction"] = [i for i in resource["interaction"] if i["code"] != code]
            removed += before - len(resource["interaction"])
    assert removed == 1, (
        f"expected to remove exactly one {code} from {resource_type}, removed {removed}"
    )
    return edited


def _facts(doc: dict[str, Any]) -> Any:
    return parse_capability(json.dumps(doc).encode())


# ---------------------------------------------------------------------------
# Done when: one removed search-type interaction diffs to that one line.
# ---------------------------------------------------------------------------


def test_one_removed_search_type_interaction_diffs_to_that_one_line() -> None:
    before = good_capability()
    after = _without_interaction(before, "Coverage", "search-type")

    report = diff_capability(_facts(before), _facts(after))

    assert report.comparable
    assert len(report.changes) == 1, [c.line() for c in report.changes]
    (change,) = report.changes
    assert change.subject == "Coverage search-type"
    assert change.detail == "interaction no longer declared"
    assert change.citation == FHIR_CAPS
    assert change.regression is True


def test_the_citation_is_the_one_the_grade_cites_for_the_same_material() -> None:
    """A verb that cited a different clause for the same fact than the grade does would be a
    second opinion rather than a diff. These are private in ``grading``, deliberately read here."""
    assert FHIR_CAPS == grading._FHIR_CAPS
    from fhir_scorecard.diff import FHIR_HTTP, SMART_DISCOVERY

    assert FHIR_HTTP == grading._FHIR_HTTP
    assert SMART_DISCOVERY == grading._SMART_DISCOVERY


def test_a_gained_interaction_is_reported_and_is_not_a_regression() -> None:
    reduced = _without_interaction(good_capability(), "Coverage", "search-type")
    report = diff_capability(_facts(reduced), _facts(good_capability()))
    (change,) = report.changes
    assert change.detail == "interaction newly declared"
    assert change.regression is False
    assert report.regressions == ()


def test_a_removed_resource_is_named_rather_than_counted() -> None:
    before = good_capability()
    after = json.loads(json.dumps(before))
    after["rest"][0]["resource"] = [
        r for r in after["rest"][0]["resource"] if r["type"] != "Observation"
    ]
    assert len(after["rest"][0]["resource"]) == len(before["rest"][0]["resource"]) - 1

    report = diff_capability(_facts(before), _facts(after))
    subjects = {c.subject for c in report.changes}
    assert "Observation" in subjects
    removal = next(c for c in report.changes if c.subject == "Observation")
    assert removal.detail == "no longer declared as a resource"
    assert removal.regression is True


# ---------------------------------------------------------------------------
# Done when: identical inputs produce an empty diff.
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_an_empty_diff() -> None:
    report = diff_capability(_facts(good_capability()), _facts(good_capability()))
    assert report.comparable
    assert report.changes == ()
    assert render_text(report) == "no change\n"


def test_a_key_absent_on_both_sides_is_never_mentioned() -> None:
    """Neither document declares a title. Reporting "title: None -> None", or reporting it at
    all, would be a claim about a field neither server published."""
    bare = {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"}
    report = diff_capability(_facts(bare), _facts(bare))
    assert report.changes == ()
    assert "software" not in render_text(report)


def test_the_same_inputs_produce_the_same_words() -> None:
    before = good_capability()
    after = _without_interaction(before, "Patient", "read")
    first = render_text(diff_capability(_facts(before), _facts(after)))
    second = render_text(diff_capability(_facts(before), _facts(after)))
    assert first == second
    assert render_json(diff_capability(_facts(before), _facts(after))) == render_json(
        diff_capability(_facts(before), _facts(after))
    )


# ---------------------------------------------------------------------------
# Done when: a T0 document against a valid one diffs as unreadable.
# ---------------------------------------------------------------------------


T0_DOCUMENT = {
    "resourceType": "OperationOutcome",
    "issue": [{"severity": "error", "code": "processing"}],
}


def test_a_t0_document_diffs_as_unreadable_with_no_field_level_claims() -> None:
    good = _facts(good_capability())
    t0 = _facts(T0_DOCUMENT)
    assert t0.parsed and not t0.resource_type_ok, "the T0 fixture must be a read-but-unusable doc"

    report = diff_capability(good, t0)

    assert report.comparable is False
    assert report.changes == (), "an unreadable side must yield no field-level claims"
    assert any("could not be read" in note for note in report.notes)
    rendered = render_text(report)
    assert "no comparison was made" in rendered
    # The specific danger: reporting the good side's resources as withdrawn.
    for resource_type in ("Patient", "Coverage", "Observation"):
        assert resource_type not in rendered


def test_an_unreadable_side_is_not_an_empty_diff() -> None:
    """The other way to get this wrong: calling it "no change", which says the two agree."""
    report = diff_capability(_facts(good_capability()), _facts(T0_DOCUMENT))
    assert render_text(report) != "no change\n"


def test_a_document_that_was_never_retrieved_says_so_rather_than_reading_as_unparseable() -> None:
    report = diff_capability(_facts(good_capability()), NO_CAPABILITY_RETRIEVED)
    assert report.comparable is False
    assert any("no document was retrieved" in note for note in report.notes)


def test_an_incomparable_report_cannot_be_built_with_changes() -> None:
    """The invariant is enforced in the type, not only observed in the functions above."""
    with pytest.raises(ValueError, match="must carry no changes"):
        DiffReport(
            kind="capability",
            changes=(Change("Patient", "gone", FHIR_CAPS),),
            comparable=False,
        )


def test_two_unreadable_sides_still_make_no_claims() -> None:
    report = diff_bytes(b"not json", b"also not json")
    assert report.comparable is False
    assert report.changes == ()


def test_different_kinds_of_artifact_refuse_to_be_compared() -> None:
    report = diff_bytes(
        json.dumps(good_capability()).encode(),
        json.dumps({"scorecards": []}).encode(),
    )
    assert report.comparable is False
    assert "different kinds of artifact" in report.notes[0]


# ---------------------------------------------------------------------------
# Done when: the timeline and the verb say the same thing.
# ---------------------------------------------------------------------------


def test_the_verb_and_the_recorded_timeline_say_the_same_thing() -> None:
    """``drift`` records what moved between two fingerprints; the verb reads the same function.

    This walks a fixture history the way the daily run builds one, then asserts that the lines
    the record kept are exactly the lines the shared core derives from the same pair. If the two
    implementations ever split, this is what fails.
    """
    before = good_capability()
    after = _without_interaction(before, "Coverage", "search-type")
    # Removing every interaction from one resource moves `resources_with_interactions`, so the
    # fingerprint actually differs and the recorded event is not empty.
    after = _without_interaction(after, "Coverage", "read")

    history: dict[str, Any] = {}
    observe(history, "e1", _facts(before), "2026-09-01")
    result = observe(history, "e1", _facts(after), "2026-09-02")

    derived = fingerprint_changes(fingerprint(_facts(before)), fingerprint(_facts(after)))
    assert list(result.changes) == derived
    assert derived, "this pair must actually move the fingerprint, or the check is vacuous"
    recorded = history["e1"]["events"][-1]["changes"]
    assert recorded == derived


def test_the_shared_core_is_the_one_drift_calls() -> None:
    """Not a copy that happens to agree today."""
    from fhir_scorecard import diff as diff_module
    from fhir_scorecard import drift as drift_module

    assert diff_module.fingerprint_changes is drift_module.fingerprint_changes


# ---------------------------------------------------------------------------
# SMART, scorecards, probes.
# ---------------------------------------------------------------------------


def test_a_smart_document_that_drops_its_token_endpoint_is_a_regression() -> None:
    before = good_smart()
    after = {k: v for k, v in before.items() if k != "token_endpoint"}
    report = diff_smart(
        parse_smart(json.dumps(before).encode()), parse_smart(json.dumps(after).encode())
    )
    (change,) = report.changes
    assert change.subject == "token endpoint"
    assert change.regression is True


def _run(vantage: str, *, score: int | None = 60, ok: bool = True) -> dict[str, Any]:
    return {
        "vantage": vantage,
        "scorecards": [
            {
                "endpoint_id": "e1",
                "grade": "B",
                "dimensions": [
                    {
                        "key": "reachability",
                        "score": score,
                        "findings": [
                            {
                                "code": "R1",
                                "ok": ok,
                                "message": "answers over HTTPS",
                                "citation": "https://hl7.org/fhir/R4/http.html",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_a_finding_that_flips_to_failing_is_a_regression() -> None:
    report = diff_scorecards(_run("v1"), _run("v1", ok=False))
    (change,) = report.changes
    assert "now fails" in change.detail
    assert change.regression is True


def test_a_dimension_score_that_falls_is_a_regression_and_carries_its_weight() -> None:
    report = diff_scorecards(_run("v1", score=60), _run("v1", score=40))
    (change,) = report.changes
    assert "score fell 60 -> 40" in change.detail
    assert "weight" in change.detail
    assert change.regression is True


def test_a_score_that_stops_being_published_is_reported_and_is_not_a_regression() -> None:
    """``60 -> None`` is not a fall to zero; the dimension left the published scale because part
    of it was never measured. Counting it as a regression would score an absence, which is the
    defect ``grading.letter`` was rewritten to stop making."""
    report = diff_scorecards(_run("v1", score=60), _run("v1", score=None))
    (change,) = report.changes
    assert "not comparable" in change.detail
    assert "published no score" in change.detail
    assert change.regression is False
    assert report.regressions == ()


def test_two_runs_from_different_vantages_are_not_an_endpoint_change() -> None:
    report = diff_scorecards(_run("davis-ca/residential"), _run("frankfurt/cloud", ok=False))
    assert report.comparable is False
    assert report.changes == ()
    assert "different vantages" in report.notes[0]
    assert "vantage.reconcile" in report.notes[0]


def test_probe_artifacts_from_different_vantages_are_not_an_endpoint_change() -> None:
    first = {"probes": {"e1": {"vantage": "a/one", "reachable": True}}}
    second = {"probes": {"e1": {"vantage": "b/two", "reachable": False}}}
    report = diff_bytes(json.dumps(first).encode(), json.dumps(second).encode())
    assert report.comparable is False
    assert "different vantages" in report.notes[0]


def test_probe_artifacts_from_one_vantage_report_reachability_moving() -> None:
    first = {"probes": {"e1": {"vantage": "a/one", "reachable": True}}}
    second = {"probes": {"e1": {"vantage": "a/one", "reachable": False}}}
    report = diff_bytes(json.dumps(first).encode(), json.dumps(second).encode())
    assert report.comparable is True
    (change,) = report.changes
    assert change.regression is True


def test_classify_reads_content_rather_than_a_filename() -> None:
    assert classify(json.dumps(good_capability()).encode()) == "capability"
    assert classify(json.dumps(good_smart()).encode()) == "smart"
    assert classify(json.dumps({"scorecards": []}).encode()) == "scorecards"
    assert classify(json.dumps({"probes": {}}).encode()) == "probes"
    assert classify(b"<html>") == "unreadable"


# ---------------------------------------------------------------------------
# The verb itself.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, payload: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_verb_exits_zero_even_when_it_finds_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _write(tmp_path, "a.json", good_capability())
    after = _write(
        tmp_path, "b.json", _without_interaction(good_capability(), "Coverage", "search-type")
    )
    assert main(["diff", str(before), str(after)]) == 0
    out = capsys.readouterr().out
    assert "Coverage search-type" in out
    assert FHIR_CAPS in out


def test_fail_on_regression_exits_one_only_when_something_was_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    full = _write(tmp_path, "full.json", good_capability())
    reduced = _write(
        tmp_path, "reduced.json", _without_interaction(good_capability(), "Coverage", "search-type")
    )
    assert main(["diff", str(full), str(reduced), "--fail-on-regression"]) == 1
    capsys.readouterr()
    # The same pair the other way round is an addition, and additions never fail a build.
    assert main(["diff", str(reduced), str(full), "--fail-on-regression"]) == 0


def test_fail_on_regression_does_not_trip_on_a_document_it_could_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "I could not read this" is not "you removed something". A build that went red here would
    be reporting an absence as a finding."""
    good = _write(tmp_path, "good.json", good_capability())
    unreadable = _write(tmp_path, "t0.json", T0_DOCUMENT)
    assert main(["diff", str(good), str(unreadable), "--fail-on-regression"]) == 0
    assert "no comparison was made" in capsys.readouterr().out


def test_the_verb_reports_a_missing_file_as_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _write(tmp_path, "good.json", good_capability())
    assert main(["diff", str(good), str(tmp_path / "absent.json")]) == 2
    assert "is not a file" in capsys.readouterr().err


def test_json_output_is_machine_readable_and_carries_the_citations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _write(tmp_path, "a.json", good_capability())
    after = _write(
        tmp_path, "b.json", _without_interaction(good_capability(), "Coverage", "search-type")
    )
    assert main(["diff", str(before), str(after), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "capability"
    assert payload["comparable"] is True
    assert payload["regression_count"] == 1
    (change,) = payload["changes"]
    assert change["citation"] == FHIR_CAPS
    assert change["regression"] is True


def test_diff_paths_reads_from_disk(tmp_path: Path) -> None:
    before = _write(tmp_path, "a.json", good_capability())
    after = _write(tmp_path, "b.json", good_capability())
    assert diff_paths(before, after).changes == ()


# ---------------------------------------------------------------------------
# The remaining shapes each side can take.
# ---------------------------------------------------------------------------


def test_a_field_that_appears_is_not_reported_as_a_move_from_a_value() -> None:
    """``None -> "4.0.1"`` is a field the first server did not publish, not a move from nothing."""
    bare = {"resourceType": "CapabilityStatement"}
    report = diff_capability(_facts(bare), _facts({**bare, "fhirVersion": "4.0.1"}))
    (change,) = report.changes
    assert change.detail == "now declared as '4.0.1', not declared before"
    assert change.regression is False


def test_a_field_that_is_withdrawn_says_what_it_was() -> None:
    bare = {"resourceType": "CapabilityStatement"}
    report = diff_capability(_facts({**bare, "fhirVersion": "4.0.1"}), _facts(bare))
    (change,) = report.changes
    assert change.detail == "no longer declared (was '4.0.1')"


def test_a_newly_declared_resource_and_profile_are_additions() -> None:
    before = good_capability()
    after = json.loads(json.dumps(before))
    after["rest"][0]["resource"].append(
        {
            "type": "Encounter",
            "interaction": [{"code": "read"}],
            "supportedProfile": ["http://example.test/StructureDefinition/enc"],
        }
    )
    report = diff_capability(_facts(before), _facts(after))
    details = {c.subject: c for c in report.changes}
    assert details["Encounter"].detail == "newly declared as a resource"
    assert details["Encounter"].regression is False
    assert any("newly declared:" in c.detail for c in report.changes if c.subject == "profile")
    assert report.regressions == ()


def test_a_withdrawn_profile_is_a_regression() -> None:
    before = good_capability()
    after = json.loads(json.dumps(before))
    for resource in after["rest"][0]["resource"]:
        resource.pop("supportedProfile", None)
    report = diff_capability(_facts(before), _facts(after))
    withdrawn = [c for c in report.changes if c.subject == "profile"]
    assert withdrawn and all(c.regression for c in withdrawn)


def test_oauth_security_withdrawn_is_a_regression() -> None:
    before = good_capability()
    after = json.loads(json.dumps(before))
    after["rest"][0].pop("security")
    report = diff_capability(_facts(before), _facts(after))
    (change,) = [c for c in report.changes if c.subject == "OAuth security"]
    assert change.detail == "no longer declared"
    assert change.regression is True


def test_an_unparseable_body_is_unreadable_rather_than_empty_facts() -> None:
    report = diff_capability(_facts(good_capability()), parse_capability(b"{not json"))
    assert report.comparable is False
    assert any("could not be read" in note for note in report.notes)


def test_a_smart_document_that_could_not_be_read_makes_no_claims() -> None:
    report = diff_smart(parse_smart(json.dumps(good_smart()).encode()), parse_smart(b"<html>"))
    assert report.comparable is False
    assert report.changes == ()


def test_a_smart_pair_routes_through_the_verb(tmp_path: Path) -> None:
    before = _write(tmp_path, "a.json", good_smart())
    after = _write(
        tmp_path, "b.json", {k: v for k, v in good_smart().items() if k != "token_endpoint"}
    )
    report = diff_paths(before, after)
    assert report.kind == "smart"
    assert report.regressions


def test_a_grade_that_moves_between_runs_is_reported() -> None:
    first, second = _run("v1"), _run("v1")
    second["scorecards"][0]["grade"] = "D"
    report = diff_scorecards(first, second)
    grade_changes = [c for c in report.changes if c.subject.endswith("grade")]
    assert grade_changes and "'B' -> 'D'" in grade_changes[0].detail


def test_a_finding_that_disappears_is_reported_and_is_not_a_regression() -> None:
    first, second = _run("v1"), _run("v1")
    second["scorecards"][0]["dimensions"][0]["findings"] = []
    report = diff_scorecards(first, second)
    (change,) = [c for c in report.changes if "disappeared" in c.detail]
    assert change.regression is False


def test_malformed_entries_inside_a_run_are_skipped_rather_than_guessed() -> None:
    """A card, dimension, or finding that is not an object is not a change; it is unreadable
    structure, and inventing a comparison from it would be the same defect one level down."""
    first = _run("v1")
    second = json.loads(json.dumps(first))
    second["scorecards"].append("not an object")
    second["scorecards"][0]["dimensions"].append("not an object")
    second["scorecards"][0]["dimensions"][0]["findings"].append("not an object")
    assert diff_scorecards(first, second).changes == ()


def test_a_run_with_no_vantage_recorded_still_compares() -> None:
    first, second = _run("v1"), _run("v1")
    first.pop("vantage")
    second.pop("vantage")
    second["scorecards"][0]["dimensions"][0]["score"] = 40
    assert diff_scorecards(first, second).regressions


def test_probe_entries_that_are_not_objects_are_skipped() -> None:
    first = {"probes": {"e1": {"vantage": "a/one", "reachable": True}, "e2": "junk"}}
    second = {"probes": {"e1": {"vantage": "a/one", "reachable": True}, "e2": "junk"}}
    assert diff_bytes(json.dumps(first).encode(), json.dumps(second).encode()).changes == ()


def test_a_json_array_is_not_an_artifact_this_tool_publishes() -> None:
    assert classify(b"[1, 2, 3]") == "unreadable"


def test_render_text_lists_notes_on_a_comparable_report() -> None:
    report = DiffReport(kind="capability", changes=(), notes=("a note",))
    assert "a note" in render_text(report)
    assert "no change" in render_text(report)


def test_render_text_counts_the_regressions_it_found() -> None:
    before = good_capability()
    after = _without_interaction(before, "Coverage", "search-type")
    rendered = render_text(diff_capability(_facts(before), _facts(after)))
    assert "1 change(s)" in rendered
    assert "1 of them removed something the earlier side had" in rendered
