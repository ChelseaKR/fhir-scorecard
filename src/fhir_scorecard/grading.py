"""Deterministic grading: dimensions, findings with spec citations, and a letter grade.

Fail closed: an unreachable endpoint is an F with a reason, never a hole in the dataset.
No model, no heuristics that cannot be explained in one sentence next to a citation.
"""

from __future__ import annotations

from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts, SmartFacts
from fhir_scorecard.fetch import FetchResult

_FHIR_CAPS = "https://hl7.org/fhir/R4/capabilitystatement.html"
_FHIR_HTTP = "https://hl7.org/fhir/R4/http.html"
_SMART_DISCOVERY = "https://hl7.org/fhir/smart-app-launch/conformance.html"
_US_CORE = "https://hl7.org/fhir/us/core/"

_PROFILE_MARKERS = ("us/core", "us-core", "carin", "davinci", "da-vinci")


@dataclass(frozen=True)
class Finding:
    code: str
    ok: bool
    points: int
    max_points: int
    message: str
    citation: str


@dataclass(frozen=True)
class DimensionScore:
    key: str
    title: str
    score: int
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class Scorecard:
    endpoint_id: str
    name: str
    grade: str
    reachable: bool
    dimensions: tuple[DimensionScore, ...]
    # Grades are only comparable within a kind; the report groups by it and never ranks across.
    kind: str = "reference"
    # Drift is informational, not scored (a capability change is often a legitimate upgrade).
    observed_since: str | None = None
    drift_events: tuple[str, ...] = ()


def _score(findings: list[Finding]) -> int:
    total = sum(f.max_points for f in findings)
    earned = sum(f.points for f in findings)
    return round(100 * earned / total) if total else 0


def grade_reachability(metadata: FetchResult) -> DimensionScore:
    findings: list[Finding] = []
    reachable = metadata.ok
    findings.append(Finding(
        code="R1", ok=reachable, points=60 if reachable else 0, max_points=60,
        message="/metadata answers with HTTP 2xx over HTTPS" if reachable
        else f"/metadata unreachable: {metadata.error or f'HTTP {metadata.status}'}",
        citation=_FHIR_HTTP,
    ))
    if reachable:
        # Latency is measured from a single vantage point per run, so bands are deliberately
        # coarse (2026-08-05): a ~1s network difference between vantages must not flip a grade.
        # The raw milliseconds are always reported for readers who care about the exact number.
        fast = metadata.elapsed_ms <= 3000
        acceptable = metadata.elapsed_ms <= 8000
        points = 40 if fast else (20 if acceptable else 0)
        findings.append(Finding(
            code="R2", ok=fast, points=points, max_points=40,
            message=f"/metadata responded in {metadata.elapsed_ms} ms (single vantage point)",
            citation=_FHIR_HTTP,
        ))
    else:
        findings.append(Finding(code="R2", ok=False, points=0, max_points=40,
                                message="latency unmeasured: endpoint unreachable",
                                citation=_FHIR_HTTP))
    return DimensionScore(key="reachability", title="Reachability",
                          score=_score(findings), findings=tuple(findings))


def grade_transparency(facts: CapabilityFacts) -> DimensionScore:
    findings: list[Finding] = []
    if not facts.parsed or not facts.resource_type_ok:
        findings.append(Finding(
            code="T0", ok=False, points=0, max_points=100,
            message=f"CapabilityStatement unparseable: {facts.parse_error}",
            citation=_FHIR_CAPS,
        ))
        return DimensionScore(key="transparency", title="Capability transparency",
                              score=0, findings=tuple(findings))

    r4 = (facts.fhir_version or "").startswith("4.")
    findings.append(Finding(code="T1", ok=r4, points=30 if r4 else 0, max_points=30,
                            message=f"fhirVersion declared: {facts.fhir_version!r}",
                            citation=_FHIR_CAPS))
    sw = facts.software_name is not None and facts.software_version is not None
    findings.append(Finding(code="T2", ok=sw, points=20 if sw else 0, max_points=20,
                            message="software name and version declared" if sw
                            else "software name/version missing",
                            citation=_FHIR_CAPS))
    # Calibration (2026-08-05): breadth alone under-credits deliberately narrow APIs. CMS Blue
    # Button 2.0 declares exactly three resource types by design (Patient/Coverage/EOB) with
    # every one fully documented; that is transparent, not deficient. Narrow-but-complete
    # (2-4 resource types, all documenting their interactions) earns full points.
    narrow_but_complete = (2 <= facts.resource_count < 5
                           and facts.resources_with_interactions == facts.resource_count)
    enough = facts.resource_count >= 5 or narrow_but_complete
    findings.append(Finding(code="T3", ok=enough, points=25 if enough else 0, max_points=25,
                            message=f"{facts.resource_count} resource types declared"
                            + (" (narrow but fully documented)" if narrow_but_complete else ""),
                            citation=_FHIR_CAPS))
    covered = (facts.resource_count > 0
               and facts.resources_with_interactions >= 0.8 * facts.resource_count)
    findings.append(Finding(code="T4", ok=covered, points=25 if covered else 0, max_points=25,
                            message=(f"{facts.resources_with_interactions}/{facts.resource_count} "
                                     "declared resources document their interactions"),
                            citation=_FHIR_CAPS))
    return DimensionScore(key="transparency", title="Capability transparency",
                          score=_score(findings), findings=tuple(findings))


def grade_interop(facts: CapabilityFacts, smart: SmartFacts) -> DimensionScore:
    findings: list[Finding] = []
    profiles = [p.lower() for p in facts.supported_profiles]
    named = any(marker in p for p in profiles for marker in _PROFILE_MARKERS)
    findings.append(Finding(code="I1", ok=named, points=40 if named else 0, max_points=40,
                            message=("US Core / CARIN / Da Vinci profiles declared" if named
                                     else "no recognized interoperability profiles declared"),
                            citation=_US_CORE))
    smart_ok = smart.parsed and smart.has_authorization_endpoint and smart.has_token_endpoint
    findings.append(Finding(code="I2", ok=smart_ok, points=35 if smart_ok else 0, max_points=35,
                            message=("SMART discovery document present and complete" if smart_ok
                                     else "SMART .well-known/smart-configuration absent or "
                                          "incomplete"),
                            citation=_SMART_DISCOVERY))
    findings.append(Finding(code="I3", ok=facts.declares_oauth_security,
                            points=25 if facts.declares_oauth_security else 0, max_points=25,
                            message=("OAuth/SMART security service declared in "
                                     "CapabilityStatement" if facts.declares_oauth_security
                                     else "no OAuth security service declared"),
                            citation=_SMART_DISCOVERY))
    return DimensionScore(key="interop", title="Interop readiness",
                          score=_score(findings), findings=tuple(findings))


_WEIGHTS = {"reachability": 0.35, "transparency": 0.35, "interop": 0.30}


def letter(dimensions: tuple[DimensionScore, ...], *, reachable: bool) -> str:
    if not reachable:
        return "F"
    weighted = sum(d.score * _WEIGHTS.get(d.key, 0.0) for d in dimensions)
    if weighted >= 90:
        return "A"
    if weighted >= 80:
        return "B"
    if weighted >= 70:
        return "C"
    if weighted >= 60:
        return "D"
    return "F"


def build_scorecard(endpoint_id: str, name: str, metadata: FetchResult,
                    facts: CapabilityFacts, smart: SmartFacts, *,
                    kind: str = "reference",
                    observed_since: str | None = None,
                    drift_events: tuple[str, ...] = ()) -> Scorecard:
    dimensions = (
        grade_reachability(metadata),
        grade_transparency(facts),
        grade_interop(facts, smart),
    )
    return Scorecard(
        endpoint_id=endpoint_id,
        name=name,
        grade=letter(dimensions, reachable=metadata.ok),
        reachable=metadata.ok,
        dimensions=dimensions,
        kind=kind,
        observed_since=observed_since,
        drift_events=drift_events,
    )
