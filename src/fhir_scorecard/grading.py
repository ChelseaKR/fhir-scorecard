"""Deterministic grading: dimensions, findings with spec citations, and a letter grade.

Fail closed: an unreachable endpoint is an F with a reason, never a hole in the dataset.
No model, no heuristics that cannot be explained in one sentence next to a citation.
"""

from __future__ import annotations

from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts, SmartFacts
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.vantage import Consensus

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
    # Multi-vantage reconciliation summary, when more than one vantage reported.
    vantage_note: str = ""
    # Grades are only comparable within a kind; the report groups by it and never ranks across.
    kind: str = "reference"
    # Drift is informational, not scored (a capability change is often a legitimate upgrade).
    observed_since: str | None = None
    drift_events: tuple[str, ...] = ()
    # Rolling reachability across recorded runs. Informational until enough observations exist;
    # a percentage off two data points would be noise dressed as a metric.
    availability: str = ""


def _score(findings: list[Finding]) -> int:
    total = sum(f.max_points for f in findings)
    earned = sum(f.points for f in findings)
    return round(100 * earned / total) if total else 0


def grade_reachability(metadata: FetchResult, *, vantage: str = "unspecified",
                       consensus: Consensus | None = None) -> DimensionScore:
    """Grade reachability, preferring a multi-vantage consensus when one is available.

    One vantage reaching an endpoint settles that it is reachable; one vantage failing settles
    nothing. Single-vantage runs fall back to what this run saw, and say so.
    """
    findings: list[Finding] = []
    reachable = consensus.reachable if consensus is not None else metadata.ok
    if consensus is not None:
        r1_message = ("/metadata answers with HTTP 2xx over HTTPS: " + consensus.detail
                      if reachable else "/metadata " + consensus.detail)
    elif reachable:
        r1_message = "/metadata answers with HTTP 2xx over HTTPS"
    else:
        r1_message = f"/metadata unreachable: {metadata.error or f'HTTP {metadata.status}'}"
    findings.append(Finding(code="R1", ok=reachable, points=60 if reachable else 0,
                            max_points=60, message=r1_message, citation=_FHIR_HTTP))
    if reachable:
        # Latency is measured from a single vantage point per run, so bands are deliberately
        # coarse (2026-08-05): a ~1s network difference between vantages must not flip a grade.
        # The raw milliseconds are always reported for readers who care about the exact number.
        elapsed = consensus.elapsed_ms if consensus is not None else metadata.elapsed_ms
        fast = elapsed <= 3000
        acceptable = elapsed <= 8000
        points = 40 if fast else (20 if acceptable else 0)
        if consensus is not None and consensus.vantages > 1:
            # Distinct vantages, and how many networks they sit on: three runner images on one
            # provider's network are one network path sampled three times, and a median over
            # them is not a median over three networks.
            where = f"median across {consensus.agreeing} reachable vantages"
            where += (" on one network" if consensus.networks == 1
                      else f" across {consensus.networks} networks")
        else:
            where = f"single vantage point: {vantage}"
        findings.append(Finding(
            code="R2", ok=fast, points=points, max_points=40,
            message=f"/metadata responded in {elapsed} ms ({where})",
            citation=_FHIR_HTTP,
        ))
    else:
        findings.append(Finding(code="R2", ok=False, points=0, max_points=40,
                                message="latency unmeasured: endpoint unreachable",
                                citation=_FHIR_HTTP))
    return DimensionScore(key="reachability", title="Reachability",
                          score=_score(findings), findings=tuple(findings))


def grade_transparency(facts: CapabilityFacts, *,
                       version_prefix: str = "4.") -> DimensionScore:
    findings: list[Finding] = []
    if not facts.parsed or not facts.resource_type_ok:
        findings.append(Finding(
            code="T0", ok=False, points=0, max_points=100,
            message=f"CapabilityStatement unparseable: {facts.parse_error}",
            citation=_FHIR_CAPS,
        ))
        return DimensionScore(key="transparency", title="Capability transparency",
                              score=0, findings=tuple(findings))

    # Check the server against the release it intends to serve, not against R4 unconditionally
    # (calibration 2026-08-05): an endpoint registered as R5 declaring 5.0.0 is correct, and
    # marking it down for not being R4 would measure the wrong thing.
    version_ok = (facts.fhir_version or "").startswith(version_prefix)
    findings.append(Finding(code="T1", ok=version_ok, points=30 if version_ok else 0,
                            max_points=30,
                            message=(f"fhirVersion declared: {facts.fhir_version!r} "
                                     f"(expected {version_prefix}x)"),
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


def grade_interop(facts: CapabilityFacts, smart: SmartFacts, *,
                  kind: str = "reference") -> DimensionScore:
    findings: list[Finding] = []
    profiles = [p.lower() for p in facts.supported_profiles]
    named = any(marker in p for p in profiles for marker in _PROFILE_MARKERS)
    findings.append(Finding(code="I1", ok=named, points=40 if named else 0, max_points=40,
                            message=("US Core / CARIN / Da Vinci profiles declared" if named
                                     else "no recognized interoperability profiles declared"),
                            citation=_US_CORE))

    # Provider Directory APIs are required to be reachable without authentication, so absence of
    # SMART/OAuth is the correct design there, not a deficiency (calibration 2026-08-05). Scoring
    # them on an authorization surface they must not have would penalize compliant behavior, so
    # those findings are reported as not applicable and carry no points either way.
    public_by_design = kind == "payer_provider_directory"
    if public_by_design:
        findings.append(Finding(
            code="I2", ok=True, points=0, max_points=0,
            message="SMART discovery not applicable: a Provider Directory API is public by design",
            citation=_SMART_DISCOVERY))
        findings.append(Finding(
            code="I3", ok=True, points=0, max_points=0,
            message="OAuth security not applicable: a Provider Directory API is public by design",
            citation=_SMART_DISCOVERY))
    else:
        smart_ok = smart.parsed and smart.has_authorization_endpoint and smart.has_token_endpoint
        findings.append(Finding(code="I2", ok=smart_ok, points=35 if smart_ok else 0,
                                max_points=35,
                                message=("SMART discovery document present and complete"
                                         if smart_ok
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
                    version_prefix: str = "4.",
                    vantage: str = "unspecified",
                    consensus: Consensus | None = None,
                    observed_since: str | None = None,
                    drift_events: tuple[str, ...] = (),
                    availability: str = "") -> Scorecard:
    dimensions = (
        grade_reachability(metadata, vantage=vantage, consensus=consensus),
        grade_transparency(facts, version_prefix=version_prefix),
        grade_interop(facts, smart, kind=kind),
    )
    return Scorecard(
        endpoint_id=endpoint_id,
        name=name,
        # The reconciled view decides the grade: an endpoint another vantage reached is not an
        # F just because this network could not get to it.
        grade=letter(dimensions,
                     reachable=consensus.reachable if consensus is not None else metadata.ok),
        reachable=consensus.reachable if consensus is not None else metadata.ok,
        dimensions=dimensions,
        kind=kind,
        vantage_note=consensus.detail if consensus is not None and consensus.vantages > 1 else "",
        observed_since=observed_since,
        drift_events=drift_events,
        availability=availability,
    )
