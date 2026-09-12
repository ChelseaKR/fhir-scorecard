"""JUnit XML and SARIF 2.1.0 for a multi-endpoint operator check.

Both formats exist to put this project's findings where a build already looks, and both have a
way of quietly undoing the distinction :mod:`fhir_scorecard.grading` is built around.

A ``Finding`` that was never made carries ``ok=False``, because there is no third value for a
boolean. It is ``observed`` that says whether the check ran. Rendering ``not ok`` as a JUnit
``<failure>`` would therefore report every check this run could not make as a check the endpoint
failed -- which is the defect that once moved a published letter from F to D, arriving by a new
route. So nothing here reads ``ok`` without reading ``observed`` first, and
``tests/test_ci_report.py`` holds that with a fixture whose only findings are unobserved.

The same split in SARIF: an unobserved finding is ``level: note`` carrying
``properties.severity: "not observed"``, which is a different value from a failing check's, so a
dashboard grouping by severity cannot collapse the two.

Determinism is a property of both renderings, because an operator diffing yesterday's artifact
against today's needs a change in the file to mean a change in the endpoint. Neither carries a
timestamp, both sort every collection they emit, and ``tests/test_ci_report.py`` renders twice
and compares bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from fhir_scorecard import __version__
from fhir_scorecard.gate import GateOutcome
from fhir_scorecard.grading import NOT_OBSERVED, DimensionScore, Finding, Scorecard
from fhir_scorecard.operator import OperatorEndpoint

#: Where a reader can find out what this tool measures. SARIF requires an absolute URI here.
_INFORMATION_URI = "https://fhir.chelseakr.com/"

#: The SARIF value for a check that ran and passed. `none` is a real level in the 2.1.0 enum,
#: and it is the honest one: a passing check is not a problem a dashboard should raise.
_LEVEL_PASSED = "none"
_LEVEL_FAILED = "error"
#: An unobserved check. `note` rather than a failure level, and paired with its own
#: `properties.severity` so nothing downstream has to infer the difference from the level alone.
_LEVEL_NOT_OBSERVED = "note"

SEVERITY_PASSED = "passed"
SEVERITY_FAILED = "failed"
SEVERITY_NOT_OBSERVED = "not observed"


@dataclass(frozen=True)
class EndpointResult:
    """One endpoint's graded result, as the registry run produced it."""

    entry: OperatorEndpoint
    card: Scorecard
    outcome: GateOutcome
    #: The threshold actually applied to this entry, after the per-entry override. Empty means
    #: the entry was informational.
    min_grade: str = ""
    #: Where this run measured from. Named in every skipped testcase, because an endpoint one
    #: network path could not reach is a fact about the path as much as about the endpoint.
    vantage: str = "unspecified"

    @property
    def observed(self) -> bool:
        """Whether this run has a grade for this endpoint at all.

        The unit of the claim is the endpoint, not the dimension. Rendering an endpoint nobody
        reached as a ``<failure>`` turns a blocked runner or a TLS-intercepting middlebox into a
        red build attributed to the endpoint. :mod:`fhir_scorecard.gate` says in terms that such
        a problem "reads as what it is rather than as something the endpoint did", so an entry
        graded ``not observed`` reports as not observed throughout, in both formats, with the
        retrieval error carried in the message rather than dropped.

        This used to be the *only* place that held that line. It said so here: when nothing was
        retrieved, ``reachability`` still held two findings that were ``observed=True`` and
        ``ok=False``, and this property covered for them. Every other surface published what
        those two findings said -- ``reachability_score: 0`` in the CSV and the JSON, a meter at
        zero and two red ✗ marks on the page (#135). The findings are ``observed=False`` now, so
        the branch below that reads ``finding.observed`` catches them on their own and this
        endpoint-level test is a second line rather than the first.

        The exit code is unaffected: a caller who set a threshold still fails, because
        :func:`fhir_scorecard.gate.evaluate` refuses to evaluate a threshold it has no grade
        for. What changes is only what the artifact calls it.
        """
        return self.card.grade != NOT_OBSERVED

    def retrieval_note(self) -> str:
        """What this run saw when it tried, for the skip messages."""
        reasons = sorted(
            {
                f.message
                for dimension in self.card.dimensions
                for f in dimension.findings
                if not f.ok
            }
        )
        return "; ".join(reasons)


def _observed(findings: tuple[Finding, ...]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Split a dimension's findings into passed, failed and not-observed.

    The single place ``ok`` is read, and it is only ever read for a finding that was observed.
    """
    passed = [f for f in findings if f.observed and f.ok]
    failed = [f for f in findings if f.observed and not f.ok]
    unobserved = [f for f in findings if not f.observed]
    return passed, failed, unobserved


def _dimension_message(dimension: DimensionScore, failed: list[Finding]) -> str:
    codes = ", ".join(f"{f.code} ({f.citation})" for f in sorted(failed, key=lambda f: f.code))
    return f"{dimension.title}: {codes}"


def _skip_message(dimension: DimensionScore, unobserved: list[Finding], vantage: str) -> str:
    reasons = "; ".join(sorted({f.message for f in unobserved}))
    return (
        f"{dimension.title} was not measured on this run from vantage {vantage}: {reasons}. "
        "This is not a finding about what the endpoint publishes."
    )


def _unreached_skip(result: EndpointResult, dimension: DimensionScore) -> str:
    return (
        f"{dimension.title} was not measured: this run retrieved nothing from "
        f"{result.entry.base_url} from vantage {result.vantage} ({result.retrieval_note()}). "
        "That is a fact about this run's network path as much as about the endpoint, and it is "
        "not a finding about what the endpoint publishes."
    )


def _render_case(suite_el: ET.Element, result: EndpointResult, dimension: DimensionScore) -> str:
    """Render one endpoint-and-dimension testcase, and say which bucket it fell into."""
    case = ET.SubElement(
        suite_el, "testcase", {"classname": result.entry.endpoint_id, "name": dimension.key}
    )
    passed, failed, unobserved = _observed(dimension.findings)
    if not result.observed:
        # Nothing was retrieved from this endpoint at all, so this run has no grade for it and
        # no dimension of it was measured. See `EndpointResult.observed`.
        ET.SubElement(case, "skipped", {"message": _unreached_skip(result, dimension)})
        return "skipped"
    if failed:
        failure = ET.SubElement(
            case,
            "failure",
            {"type": "finding", "message": _dimension_message(dimension, failed)},
        )
        lines = [
            f"{f.code}: {f.message} [{f.citation}]" for f in sorted(failed, key=lambda f: f.code)
        ]
        if unobserved:
            # Stated, not folded into the failure. The reader has to be able to see that this
            # dimension's picture is incomplete without reading the unmeasured checks as ones
            # that failed.
            lines.append(
                f"{len(unobserved)} further check(s) in this dimension could not be made on "
                "this run and are not counted for or against it."
            )
        failure.text = "\n".join(lines)
        return "failures"
    if unobserved:
        ET.SubElement(
            case, "skipped", {"message": _skip_message(dimension, unobserved, result.vantage)}
        )
        return "skipped"
    if not passed:
        # A dimension with no findings at all must not masquerade as one that passed.
        ET.SubElement(
            case,
            "skipped",
            {
                "message": (
                    f"{dimension.title} produced no checks on this run, so there is nothing to "
                    "report for or against it."
                )
            },
        )
        return "skipped"
    return "passed"


def to_junit(results: list[EndpointResult]) -> str:
    """JUnit XML: a testsuite per kind, a testcase per endpoint and dimension.

    A dimension with an observed failing check is a ``<failure>`` naming only the checks that
    actually failed, and saying separately how many could not be made. A dimension with nothing
    observed, and every dimension of an endpoint this run did not reach, is ``<skipped>``.
    """
    suites: dict[str, list[EndpointResult]] = {}
    for result in results:
        suites.setdefault(result.entry.kind, []).append(result)

    root = ET.Element("testsuites", {"name": "fhir-scorecard"})
    totals = {"tests": 0, "failures": 0, "skipped": 0}
    for kind in sorted(suites):
        suite_el = ET.SubElement(root, "testsuite", {"name": kind})
        counts = {"tests": 0, "failures": 0, "skipped": 0}
        for result in sorted(suites[kind], key=lambda r: r.entry.endpoint_id):
            for dimension in result.card.dimensions:
                counts["tests"] += 1
                bucket = _render_case(suite_el, result, dimension)
                if bucket in counts:
                    counts[bucket] += 1
        for key in counts:
            suite_el.set(key, str(counts[key]))
            totals[key] += counts[key]
    for key in totals:
        root.set(key, str(totals[key]))
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _rules(results: list[EndpointResult]) -> list[dict[str, object]]:
    """One SARIF rule per finding code this run actually produced.

    Derived from the run rather than from a hand-kept catalogue, so a new finding code cannot
    arrive with no rule behind it and a retired one cannot linger as a rule nothing emits.
    """
    catalogue: dict[str, Finding] = {}
    for result in results:
        for dimension in result.card.dimensions:
            for finding in dimension.findings:
                catalogue.setdefault(finding.code, finding)
    return [
        {
            "id": code,
            "name": code,
            "shortDescription": {"text": catalogue[code].message},
            "helpUri": catalogue[code].citation,
            "properties": {"maxPoints": catalogue[code].max_points},
        }
        for code in sorted(catalogue)
    ]


def _result_entry(
    result: EndpointResult, dimension: DimensionScore, finding: Finding
) -> dict[str, object]:
    if not result.observed:
        # The whole endpoint went unobserved, so every finding about it is a note, including
        # the reachability findings this vantage did make. See `EndpointResult.observed`.
        level, severity = _LEVEL_NOT_OBSERVED, SEVERITY_NOT_OBSERVED
        text = (
            f"{result.entry.name} ({result.entry.endpoint_id}), {dimension.title}: "
            f"{finding.message} This run retrieved nothing from {result.entry.base_url} from "
            f"vantage {result.vantage}, so it has no grade for this endpoint."
        )
    elif not finding.observed:
        level, severity = _LEVEL_NOT_OBSERVED, SEVERITY_NOT_OBSERVED
        text = (
            f"{result.entry.name} ({result.entry.endpoint_id}), {dimension.title}: "
            f"{finding.message} This check was not made on this run from vantage "
            f"{result.vantage}, and is neither a pass nor a failure."
        )
    else:
        level = _LEVEL_PASSED if finding.ok else _LEVEL_FAILED
        severity = SEVERITY_PASSED if finding.ok else SEVERITY_FAILED
        text = (
            f"{result.entry.name} ({result.entry.endpoint_id}), {dimension.title}: "
            f"{finding.message}"
        )
    return {
        "ruleId": finding.code,
        "level": level,
        "message": {"text": text},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": result.entry.base_url},
                }
            }
        ],
        "properties": {
            "severity": severity,
            "endpointId": result.entry.endpoint_id,
            "kind": result.entry.kind,
            "dimension": dimension.key,
            "citation": finding.citation,
            "observed": finding.observed,
            "points": finding.points,
            "maxPoints": finding.max_points,
            "withheldPoints": finding.withheld_points,
        },
    }


def to_sarif(results: list[EndpointResult]) -> str:
    """SARIF 2.1.0 with one result per finding, each carrying its citation.

    No timestamp anywhere: SARIF permits an ``invocation`` block with times, and omitting it is
    what makes two runs over the same documents produce the same bytes.
    """
    entries: list[dict[str, object]] = []
    for result in sorted(results, key=lambda r: r.entry.endpoint_id):
        for dimension in result.card.dimensions:
            for finding in sorted(dimension.findings, key=lambda f: f.code):
                entries.append(_result_entry(result, dimension, finding))
    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "fhir-scorecard",
                        "version": __version__,
                        "informationUri": _INFORMATION_URI,
                        "rules": _rules(results),
                    }
                },
                "results": entries,
            }
        ],
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"
