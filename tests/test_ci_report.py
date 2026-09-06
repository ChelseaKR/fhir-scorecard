"""`check --registry`, and the JUnit and SARIF artifacts it writes.

The property this file exists to hold is the one :mod:`fhir_scorecard.grading` is built around,
arriving by a new route. A ``Finding`` that was never made carries ``ok=False``, because a
boolean has no third value; ``observed`` is what says whether the check ran. Any renderer that
reads ``ok`` without reading ``observed`` republishes the defect PR #110 fixed -- a check this
run could not make, reported as a check the endpoint failed -- this time in an operator's build
log rather than in a published letter.

So the cases below are mostly about absence: an endpoint nothing was retrieved from, a dimension
where some checks ran and some could not, and a run over an empty list. Each asserts what the
artifact is *not* allowed to say, not merely that it says something.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from fhir_scorecard.ci_report import (
    SEVERITY_FAILED,
    SEVERITY_NOT_OBSERVED,
    SEVERITY_PASSED,
    EndpointResult,
    to_junit,
    to_sarif,
)
from fhir_scorecard.cli import main
from fhir_scorecard.gate import GateOutcome
from fhir_scorecard.grading import NOT_OBSERVED, DimensionScore, Finding, Scorecard
from fhir_scorecard.operator import (
    OperatorEndpoint,
    OperatorRegistryError,
    load_operator_registry,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
OPERATOR_REGISTRY = FIXTURES / "operator-registry.json"

#: The three fixture endpoints carry three different kinds, which is what makes the issue's
#: "three JUnit testsuites" a real assertion rather than a restatement of the entry count.
FIXTURE_IDS = {"cms-blue-button-2", "inferno-reference", "oracle-health-open"}


def _registry(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "operator.json"
    path.write_text(json.dumps({"endpoints": entries}), encoding="utf-8")
    return path


def _run(tmp_path: Path, registry: Path, *extra: str) -> tuple[int, Path, Path]:
    junit, sarif = tmp_path / "j.xml", tmp_path / "s.sarif"
    code = main(
        [
            "check",
            "--registry",
            str(registry),
            "--offline",
            "--fixtures",
            str(FIXTURES),
            "--junit",
            str(junit),
            "--sarif",
            str(sarif),
            *extra,
        ]
    )
    return code, junit, sarif


# ---------------------------------------------------------------------------
# The issue's four acceptance criteria.
# ---------------------------------------------------------------------------


def test_a_three_entry_registry_yields_three_testsuites_and_a_cited_sarif(tmp_path: Path) -> None:
    code, junit, sarif = _run(tmp_path, OPERATOR_REGISTRY)
    assert code == 0

    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    suites = root.findall("testsuite")
    assert len(suites) == 3, "one testsuite per kind; the three fixtures carry three kinds"
    assert {s.get("name") for s in suites} == {"payer", "reference", "ehr"}
    cases = root.findall(".//testcase")
    assert len(cases) == 9, "three endpoints, three dimensions each"
    assert {c.get("classname") for c in cases} == FIXTURE_IDS

    document = json.loads(sarif.read_text(encoding="utf-8"))
    results = document["runs"][0]["results"]
    assert results, "a SARIF with no results would satisfy every assertion below vacuously"
    for result in results:
        citation = result["properties"]["citation"]
        assert citation.startswith("https://"), f"{result['ruleId']} carries no citation"


def test_an_endpoint_nothing_was_retrieved_from_is_skipped_not_failed(tmp_path: Path) -> None:
    """The issue's sharpest criterion, and the one a naive renderer gets wrong.

    When nothing is retrieved, `reachability` still holds R1 and R2 with `observed=True` and
    `ok=False`: this vantage really did try and really did get nothing. Rendering those as a
    `<failure>` turns a blocked runner into a red build attributed to the endpoint, which
    `fhir_scorecard.gate` says in terms it must not be.
    """
    registry = _registry(
        tmp_path,
        [
            {
                "id": "unreachable-one",
                "name": "An endpoint no vantage reached",
                "kind": "payer",
                "base_url": "https://example.invalid/fhir",
            }
        ],
    )
    code, junit, sarif = _run(tmp_path, registry)
    assert code == 0, "no threshold was set, so nothing was asked of this endpoint"

    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.findall(".//failure") == [], "an unreachable endpoint produced a failure"
    assert len(root.findall(".//skipped")) == 3, "every dimension should be skipped"
    assert root.get("failures") == "0"
    for skipped in root.findall(".//skipped"):
        message = skipped.get("message") or ""
        assert "unspecified" in message, "the skip must name the vantage it measured from"
        assert "not a finding about what the endpoint publishes" in message

    document = json.loads(sarif.read_text(encoding="utf-8"))
    for result in document["runs"][0]["results"]:
        assert result["level"] == "note", result
        assert result["properties"]["severity"] == SEVERITY_NOT_OBSERVED


def test_the_exit_code_reflects_only_the_threshold_the_caller_set(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        [
            {
                "id": "unreachable-one",
                "name": "An endpoint no vantage reached",
                "kind": "payer",
                "base_url": "https://example.invalid/fhir",
            }
        ],
    )
    assert _run(tmp_path, registry)[0] == 0
    # A threshold was requested and could not be evaluated, so the gate fails -- while the
    # artifact still calls every dimension skipped rather than failed.
    code, junit, _ = _run(tmp_path, registry, "--min-grade", "C")
    assert code == 1
    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.findall(".//failure") == []


def test_two_runs_over_the_fixtures_produce_byte_identical_artifacts(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _, j1, s1 = _run(first, OPERATOR_REGISTRY)
    _, j2, s2 = _run(second, OPERATOR_REGISTRY)
    assert j1.read_bytes() == j2.read_bytes()
    assert s1.read_bytes() == s2.read_bytes()
    assert "timestamp" not in j1.read_text().lower()


def test_an_http_base_url_is_exit_2_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused at load time, which is what "before any request" means mechanically."""
    import fhir_scorecard.cli as cli_module

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("a request was made for a registry that should have been refused")

    monkeypatch.setattr(cli_module, "fetch_json", explode)
    registry = _registry(
        tmp_path,
        [
            {
                "id": "plain-http",
                "name": "Plain HTTP",
                "kind": "payer",
                "base_url": "http://example.com/fhir",
            }
        ],
    )
    assert main(["check", "--registry", str(registry)]) == 2


# ---------------------------------------------------------------------------
# The ok/observed split, asserted directly rather than through the CLI.
# ---------------------------------------------------------------------------


def _card(dimensions: tuple[DimensionScore, ...], grade: str = "C") -> Scorecard:
    return Scorecard(endpoint_id="e", name="E", grade=grade, reachable=True, dimensions=dimensions)


def _result(card: Scorecard) -> EndpointResult:
    return EndpointResult(
        entry=OperatorEndpoint(
            endpoint_id="e", name="E", kind="payer", base_url="https://example.test/fhir"
        ),
        card=card,
        outcome=GateOutcome(True, ""),
    )


def test_a_dimension_of_only_unobserved_findings_never_renders_as_a_failure() -> None:
    """`ok=False` on an unobserved finding is not a failing check. This is the conflation."""
    dimension = DimensionScore(
        key="interop",
        title="Interop readiness",
        score=None,
        findings=(
            Finding(
                code="I3",
                ok=False,  # the value an unmade check necessarily carries
                points=0,
                max_points=0,
                message="the SMART document was not retrieved",
                citation="https://hl7.org/fhir/smart-app-launch/conformance.html",
                observed=False,
                withheld_points=25,
            ),
        ),
        withheld_points=25,
    )
    xml = to_junit([_result(_card((dimension,)))])
    root = ET.fromstring(xml)
    assert root.findall(".//failure") == []
    assert len(root.findall(".//skipped")) == 1

    document = json.loads(to_sarif([_result(_card((dimension,)))]))
    result = document["runs"][0]["results"][0]
    assert result["level"] == "note"
    assert result["properties"]["severity"] == SEVERITY_NOT_OBSERVED
    assert result["properties"]["withheldPoints"] == 25


def test_a_partly_measured_dimension_blames_only_the_checks_that_ran() -> None:
    """Some checks ran and failed, others could not run. The failure must name only the first,
    and must say the picture is incomplete rather than letting the reader assume it is whole."""
    dimension = DimensionScore(
        key="interop",
        title="Interop readiness",
        score=None,
        findings=(
            Finding(
                code="I1",
                ok=False,
                points=0,
                max_points=40,
                message="no US Core profile declared",
                citation="https://hl7.org/fhir/us/core/",
            ),
            Finding(
                code="I3",
                ok=False,
                points=0,
                max_points=0,
                message="the SMART document was not retrieved",
                citation="https://hl7.org/fhir/smart-app-launch/conformance.html",
                observed=False,
                withheld_points=25,
            ),
        ),
        withheld_points=25,
    )
    root = ET.fromstring(to_junit([_result(_card((dimension,)))]))
    failure = root.find(".//failure")
    assert failure is not None
    assert "I1" in (failure.get("message") or "")
    assert "I3" not in (failure.get("message") or ""), "an unmade check was named as a failure"
    assert "could not be made" in (failure.text or "")

    document = json.loads(to_sarif([_result(_card((dimension,)))]))
    by_rule = {r["ruleId"]: r for r in document["runs"][0]["results"]}
    assert by_rule["I1"]["properties"]["severity"] == SEVERITY_FAILED
    assert by_rule["I3"]["properties"]["severity"] == SEVERITY_NOT_OBSERVED
    assert by_rule["I1"]["level"] != by_rule["I3"]["level"]


def test_an_unobserved_card_is_not_observed_throughout() -> None:
    dimension = DimensionScore(
        key="reachability",
        title="Reachability",
        score=0,
        findings=(
            Finding(
                code="R1",
                ok=False,
                points=0,
                max_points=60,
                message="/metadata unreachable",
                citation="https://hl7.org/fhir/R4/http.html",
            ),
        ),
    )
    result = _result(_card((dimension,), grade=NOT_OBSERVED))
    assert not result.observed
    root = ET.fromstring(to_junit([result]))
    assert root.findall(".//failure") == []


# ---------------------------------------------------------------------------
# SARIF structure. Not full schema validation: see the PR body.
# ---------------------------------------------------------------------------


def test_the_sarif_carries_the_2_1_0_structure_a_consumer_requires(tmp_path: Path) -> None:
    _, _, sarif = _run(tmp_path, OPERATOR_REGISTRY)
    document = json.loads(sarif.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    assert document["$schema"].endswith("sarif-2.1.0.json")
    assert len(document["runs"]) == 1
    driver = document["runs"][0]["tool"]["driver"]
    assert driver["name"] == "fhir-scorecard"
    assert driver["informationUri"].startswith("https://")
    declared = {rule["id"] for rule in driver["rules"]}
    assert declared, "a run with no declared rules would make the next assertion vacuous"
    for rule in driver["rules"]:
        assert rule["shortDescription"]["text"]
        assert rule["helpUri"].startswith("https://")
    for result in document["runs"][0]["results"]:
        assert result["ruleId"] in declared, f"{result['ruleId']} is used but never declared"
        assert result["level"] in {"none", "note", "warning", "error"}
        assert result["message"]["text"]
        location = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert location.startswith("https://")


def test_every_rule_the_run_declares_is_one_the_run_actually_emitted(tmp_path: Path) -> None:
    """Derived from the run, not from a hand-kept catalogue: a rule nothing emits is a rule
    that has outlived its finding, and this is what would notice."""
    _, _, sarif = _run(tmp_path, OPERATOR_REGISTRY)
    document = json.loads(sarif.read_text(encoding="utf-8"))
    declared = {rule["id"] for rule in document["runs"][0]["tool"]["driver"]["rules"]}
    used = {result["ruleId"] for result in document["runs"][0]["results"]}
    assert declared == used


# ---------------------------------------------------------------------------
# The operator registry itself.
# ---------------------------------------------------------------------------


def test_the_published_severity_vocabulary_is_pinned_to_its_literals() -> None:
    """These three strings are what a consumer's dashboard groups by, so they are a contract
    rather than an implementation detail.

    Every other assertion in this file compares against the imported constants, which means a
    rename would move the code and the tests together and pass. This is the one place the
    literals are written out, so that cannot happen silently.
    """
    assert SEVERITY_NOT_OBSERVED == "not observed"
    assert SEVERITY_FAILED == "failed"
    assert SEVERITY_PASSED == "passed"
    assert SEVERITY_NOT_OBSERVED != SEVERITY_FAILED, (
        "a dashboard grouping by severity could not tell an unmade check from a failed one"
    )


def test_the_committed_operator_fixture_matches_the_captured_directories() -> None:
    entries = load_operator_registry(OPERATOR_REGISTRY)
    assert {e.endpoint_id for e in entries} == FIXTURE_IDS
    for entry in entries:
        assert (FIXTURES / entry.endpoint_id / "metadata.json").is_file()


def test_a_verification_block_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """An operator who writes one is describing a record nothing here reads."""
    registry = _registry(
        tmp_path,
        [
            {
                "id": "endpoint-one",
                "name": "E",
                "kind": "payer",
                "base_url": "https://example.test/fhir",
                "verification": {"method": "self", "date": "2026-09-06"},
            }
        ],
    )
    with pytest.raises(OperatorRegistryError, match="verification"):
        load_operator_registry(registry)


def test_an_empty_registry_is_an_error_not_a_clean_run(tmp_path: Path) -> None:
    """A registry mode that exited 0 having graded nothing would be a gate that cannot fail."""
    registry = _registry(tmp_path, [])
    with pytest.raises(OperatorRegistryError, match="no endpoints"):
        load_operator_registry(registry)
    assert main(["check", "--registry", str(registry)]) == 2


def test_every_entry_disabled_is_an_error_not_a_clean_run(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        [
            {
                "id": "endpoint-one",
                "name": "E",
                "kind": "payer",
                "base_url": "https://example.test/fhir",
                "enabled": False,
            }
        ],
    )
    assert main(["check", "--registry", str(registry)]) == 2


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {"id": "Endpoint-One", "name": "E", "kind": "payer", "base_url": "https://a.test"},
            "slug",
        ),
        ({"id": "endpoint-one", "name": "E", "kind": "nope", "base_url": "https://a.test"}, "kind"),
        (
            {
                "id": "endpoint-one",
                "name": "E",
                "kind": "payer",
                "base_url": "https://a.test",
                "expects": "r9",
            },
            "expects",
        ),
        (
            {
                "id": "endpoint-one",
                "name": "E",
                "kind": "payer",
                "base_url": "https://a.test",
                "min_grade": "Z",
            },
            "min_grade",
        ),
        (
            {
                "id": "endpoint-one",
                "name": "E",
                "kind": "payer",
                "base_url": "https://a.test",
                "enabled": "yes",
            },
            "enabled",
        ),
    ],
)
def test_malformed_entries_are_refused_and_name_the_field(
    tmp_path: Path, entry: dict[str, object], message: str
) -> None:
    with pytest.raises(OperatorRegistryError, match=message):
        load_operator_registry(_registry(tmp_path, [entry]))


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    entry = {"id": "endpoint-one", "name": "E", "kind": "payer", "base_url": "https://a.test"}
    with pytest.raises(OperatorRegistryError, match="duplicate"):
        load_operator_registry(_registry(tmp_path, [entry, dict(entry)]))


def test_a_per_entry_min_grade_overrides_the_run_wide_one(tmp_path: Path) -> None:
    """An operator whose sandbox and whose production API sit in one file should not have to
    run the tool twice to hold them to different bars."""
    entries = json.loads(OPERATOR_REGISTRY.read_text())["endpoints"]
    for entry in entries:
        entry.pop("min_grade", None)
    # oracle-health-open grades C on the fixtures; demand an A of it alone.
    for entry in entries:
        if entry["id"] == "oracle-health-open":
            entry["min_grade"] = "A"
    registry = _registry(tmp_path, entries)
    assert _run(tmp_path, registry)[0] == 1, "the per-entry threshold was not applied"

    for entry in entries:
        entry.pop("min_grade", None)
    assert _run(tmp_path, _registry(tmp_path, entries))[0] == 0


def test_the_single_endpoint_check_still_works_and_still_needs_one_of_the_two() -> None:
    assert main(["check"]) == 2
    assert main(["check", "http://example.com/fhir"]) == 2


def test_a_run_that_reached_nothing_does_not_summarise_as_a_clean_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "3 endpoints checked, 0 below the threshold" over three endpoints nothing answered is a
    clean-looking summary of no measurement at all. The roll-up separates graded from unreached,
    and says in terms that this is not a clean bill of health."""
    registry = _registry(
        tmp_path,
        [
            {
                "id": "unreachable-one",
                "name": "One",
                "kind": "payer",
                "base_url": "https://example.invalid/a",
            },
            {
                "id": "unreachable-two",
                "name": "Two",
                "kind": "payer",
                "base_url": "https://example.invalid/b",
            },
        ],
    )
    code, _, _ = _run(tmp_path, registry)
    captured = capsys.readouterr()
    assert code == 0, "no threshold was set, so the exit code says nothing about reachability"
    assert "0 graded, 2 not reached on this run" in captured.out
    assert "unreachable-one, unreachable-two" in captured.out
    assert "not a clean bill of health" in captured.err
    # The clean-run phrasing must not appear at all.
    assert "fell below the threshold" not in captured.out


def test_min_grade_f_is_the_documented_way_to_fail_on_an_unreached_registry(
    tmp_path: Path,
) -> None:
    """The opt-in the roll-up points at has to actually work, or the message is a dead end."""
    registry = _registry(
        tmp_path,
        [
            {
                "id": "unreachable-one",
                "name": "One",
                "kind": "payer",
                "base_url": "https://example.invalid/a",
            }
        ],
    )
    assert _run(tmp_path, registry)[0] == 0
    assert _run(tmp_path, registry, "--min-grade", "F")[0] == 1


def test_a_partly_reached_registry_counts_only_what_it_graded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = json.loads(OPERATOR_REGISTRY.read_text())["endpoints"]
    entries.append(
        {
            "id": "unreachable-one",
            "name": "One",
            "kind": "payer",
            "base_url": "https://example.invalid/a",
        }
    )
    code, _, _ = _run(tmp_path, _registry(tmp_path, entries))
    captured = capsys.readouterr()
    assert code == 0
    assert "4 endpoint(s) in the registry: 3 graded, 1 not reached" in captured.out
    assert "0 of the 3 graded fell below" in captured.out


def test_an_unreadable_or_malformed_registry_is_refused_and_says_which(tmp_path: Path) -> None:
    """Each of these is a run that could not be made. None of them may read as a clean one."""
    missing = tmp_path / "nope.json"
    with pytest.raises(OperatorRegistryError, match="cannot read"):
        load_operator_registry(missing)
    assert main(["check", "--registry", str(missing)]) == 2

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(OperatorRegistryError, match="not valid JSON"):
        load_operator_registry(bad_json)

    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text('{"endpoints": "not a list"}', encoding="utf-8")
    with pytest.raises(OperatorRegistryError, match="endpoints"):
        load_operator_registry(wrong_shape)

    not_an_object = tmp_path / "item.json"
    not_an_object.write_text('{"endpoints": ["a string"]}', encoding="utf-8")
    with pytest.raises(OperatorRegistryError, match="not an object"):
        load_operator_registry(not_an_object)


def test_a_dimension_with_no_findings_is_skipped_rather_than_passed() -> None:
    """An empty dimension has nothing to say. A bare testcase would say it passed."""
    empty = DimensionScore(key="interop", title="Interop readiness", score=None, findings=())
    root = ET.fromstring(to_junit([_result(_card((empty,)))]))
    assert root.findall(".//failure") == []
    assert len(root.findall(".//skipped")) == 1
    assert "nothing to report" in (root.find(".//skipped").get("message") or "")


def test_an_unwritable_artifact_path_is_exit_2(tmp_path: Path) -> None:
    """A run that graded endpoints but could not write the artifact the caller asked for has
    not done what it was asked; exiting 0 would leave a build believing a file exists."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    code = main(
        [
            "check",
            "--registry",
            str(OPERATOR_REGISTRY),
            "--offline",
            "--fixtures",
            str(FIXTURES),
            "--junit",
            str(blocker / "sub" / "j.xml"),
        ]
    )
    assert code == 2
