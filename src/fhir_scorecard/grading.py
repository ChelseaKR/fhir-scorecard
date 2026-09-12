"""Deterministic grading: dimensions, findings with spec citations, and a letter grade.

Fail closed, and fail honestly. An endpoint no vantage could reach is published with the reason
and never disappears from the dataset, but it is also not graded: a run that retrieved no
document has nothing to say about what the document declares, and saying it anyway turns absence
of evidence into a finding against a named organization. Dimensions that were never observed
carry no score, no points, and no findings that read as absence.

The same rule holds one step in, where it is easier to miss: a document that *did* arrive and is
not a CapabilityStatement — an OperationOutcome under HTTP 200, a sign-in page, a search Bundle —
leaves every check that reads a CapabilityStatement with nothing to read. Those checks report
that (T0, I0) rather than reporting the dataclass defaults they would otherwise find, because
"no profile canonical declared in [five elements]" is a claim about a document, and there is no
document to make it about. Both findings carry the full weight the checks they replace would
have carried, so honesty here costs an endpoint nothing and earns it nothing.

No model, no heuristics that cannot be explained in one sentence next to a citation.
"""

from __future__ import annotations

from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts, SmartFacts
from fhir_scorecard.fetch import UNCLASSIFIED, FetchResult
from fhir_scorecard.vantage import Consensus

_FHIR_CAPS = "https://hl7.org/fhir/R4/capabilitystatement.html"
_FHIR_HTTP = "https://hl7.org/fhir/R4/http.html"
_SMART_DISCOVERY = "https://hl7.org/fhir/smart-app-launch/conformance.html"
_US_CORE = "https://hl7.org/fhir/us/core/"

_PROFILE_MARKERS = ("us/core", "us-core", "carin", "davinci", "da-vinci")

# The same names in prose rather than in a canonical URL. A document that says "CARIN" in its
# title is not declaring conformance, and this is never scored; it is the difference between a
# flat denial and a finding a payer can act on.
_PROSE_MARKERS = ("us core", "uscore", "us-core", "carin", "da vinci", "davinci", "da-vinci")

# Every element R4 gives a server to declare conformance in. I1 reads all of them before
# concluding anything, and its message names them, because "no recognized interoperability
# profiles declared" was a conclusion drawn from exactly one of them.
_PROFILE_ELEMENTS = (
    "rest.resource.supportedProfile",
    "rest.resource.profile",
    "instantiates",
    "imports",
    "meta.profile",
)

#: Published in place of a letter when a run observed nothing to grade. It is deliberately not a
#: letter: a reader compares an F against a C, and "F" was carrying two opposite meanings, one of
#: them a claim about a named payer that no measurement supported.
NOT_OBSERVED = "not observed"

_NOT_RETRIEVED_CODE = "NR"

# What I1 is worth when it runs, and what I3 is worth when it applies. Named because I0 has to
# carry exactly their sum when neither can run, or the dimension's denominator would move and a
# document nobody could read would change the letter.
_PROFILES_POINTS = 40
_OAUTH_POINTS = 25
_SMART_POINTS = 35


@dataclass(frozen=True)
class Finding:
    code: str
    ok: bool
    points: int
    max_points: int
    message: str
    citation: str
    # False when the check was not run because nothing was retrieved. Such a finding is neither
    # a pass nor a failure and must never be rendered as one.
    observed: bool = True
    # What this check would have been worth had it run. Zero for every check that did run, and
    # for a check that genuinely does not apply. Non-zero only on an `observed=False` finding,
    # where it is the sole record of how much of the dimension's scale was never measured --
    # without it, `max_points` reads as "this dimension is out of 65" and a percentage computed
    # over the remainder is not on the same scale as one computed over the whole.
    withheld_points: int = 0


@dataclass(frozen=True)
class DimensionScore:
    key: str
    title: str
    # None when this dimension has no score on the published scale: either nothing in it was
    # observed at all, or part of its scale was never measured (see `withheld_points`). Zero is
    # a measurement; neither of these is one, and coverage.py's rule applies here too: a
    # measurement we were never entitled to make must not be counted as a measurement that
    # failed -- nor as one that passed.
    score: int | None
    findings: tuple[Finding, ...]
    # Points belonging to checks this run could not make. `letter` uses them to bound the
    # weighted score from both sides; nothing else may treat them as earned or as forfeited.
    withheld_points: int = 0


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
    # Declarations this endpoint has returned to rather than advanced past. Reported separately
    # from drift_events on purpose: one address in front of two backends is a different fact
    # about the endpoint from a publisher shipping a release, and repeating the second every
    # time the probe lands on the other backend crowds out the releases that are real.
    drift_alternations: tuple[str, ...] = ()
    # Rolling reachability across recorded runs. Informational until enough observations exist;
    # a percentage off two data points would be noise dressed as a metric.
    availability: str = ""
    # Why this endpoint was not reached, from the closed vocabulary in
    # :data:`fhir_scorecard.fetch.FAILURE_KINDS` (#117). Empty whenever it *was* reached, and a
    # tuple rather than a value because vantages can disagree and this project publishes the
    # disagreement instead of resolving it.
    #
    # This field decides nothing. It carries the distinction between "the payer requires
    # credentials" and "the public record is broken" as data so both can be counted; whether the
    # first is a finding about the payer is a question `data/CANDIDATES.md` and
    # `docs/SAMPLING-FRAME.md` answer differently, and neither this field nor any wording built
    # on it settles that.
    failure_kinds: tuple[str, ...] = ()


def _withheld(findings: list[Finding]) -> int:
    """Points belonging to checks this run could not make."""
    return sum(f.withheld_points for f in findings)


def _score(findings: list[Finding]) -> int | None:
    """Percentage of the dimension's scale that was earned, or None when there is no such
    percentage to report.

    A dimension whose checks never ran has no denominator. Rounding that to zero is what made an
    unreachable endpoint look like an endpoint that published nothing.

    A dimension where *some* check could not run has a denominator that moved, which is worse,
    because it still produces a number. Dividing the earned points by what happened to be on the
    table means a check that could not be made is excluded from the denominator, and excluding a
    check the endpoint was failing *raises* the percentage. Measured on this grader before the
    fix: a payer scoring I1 40/40 and I3 0/25 scored interop 40 when its SMART document was
    retrieved and unusable, and 62 when no vantage retrieved it at all -- the same evidence about
    the endpoint, twenty-two points better for the absence of a document. Weighted, that moved a
    published letter from F to D.

    Neither substitute is available: crediting the withheld points reads absence as a pass, and
    forfeiting them reads absence as a failure, and this project refuses both. So there is no
    percentage, and `letter` bounds the grade from the findings instead.
    """
    if _withheld(findings):
        return None
    total = sum(f.max_points for f in findings)
    earned = sum(f.points for f in findings)
    return round(100 * earned / total) if total else None


def _not_retrieved(key: str, title: str, what: str, citation: str) -> DimensionScore:
    """A whole dimension that was not observed: one neutral finding, no score, no points."""
    return DimensionScore(
        key=key,
        title=title,
        score=None,
        findings=(
            Finding(
                code=_NOT_RETRIEVED_CODE,
                ok=False,
                points=0,
                max_points=0,
                message=what,
                citation=citation,
                observed=False,
            ),
        ),
    )


def grade_reachability(
    metadata: FetchResult, *, vantage: str = "unspecified", consensus: Consensus | None = None
) -> DimensionScore:
    """Grade reachability, preferring a multi-vantage consensus when one is available.

    One vantage reaching an endpoint settles that it is reachable; one vantage failing settles
    nothing. Single-vantage runs fall back to what this run saw, and say so.
    """
    findings: list[Finding] = []
    reachable = consensus.reachable if consensus is not None else metadata.ok
    if consensus is not None:
        r1_message = (
            "/metadata answers with HTTP 2xx over HTTPS: " + consensus.detail
            if reachable
            else "/metadata " + consensus.detail
        )
    elif reachable:
        r1_message = "/metadata answers with HTTP 2xx over HTTPS"
    else:
        r1_message = f"/metadata unreachable: {metadata.error or f'HTTP {metadata.status}'}"
    findings.append(
        Finding(
            code="R1",
            ok=reachable,
            points=60 if reachable else 0,
            # Out of the denominator when no vantage reached the endpoint, for the reason the
            # whole module gives one step in: R1 asks whether /metadata answers 2xx, and a run
            # where every vantage sits on one network cannot answer that question -- only the
            # narrower "it did not answer *here*", which is what the message already says.
            # Scored, the two findings summed to 0 out of 100 and `_score` published a bare **0**
            # beside a named health insurer while transparency and interop published nothing at
            # all for the identical condition. Measured 2026-09-12: 14 of 81 endpoints carried
            # that zero, and re-probing them from a residential network returned HTTP 200 and
            # HTTP 204 for two of them -- R1's own pass criterion.
            max_points=60 if reachable else 0,
            message=r1_message,
            citation=_FHIR_HTTP,
            observed=reachable,
            # The full 60 when it could not be asked, so the dimension's scale stays recoverable
            # and a reader can see that *all* of it went unmeasured rather than inferring it from
            # an absent number.
            withheld_points=0 if reachable else 60,
        )
    )
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
            where += (
                " on one network"
                if consensus.networks == 1
                else f" across {consensus.networks} networks"
            )
        else:
            where = f"single vantage point: {vantage}"
        findings.append(
            Finding(
                code="R2",
                ok=fast,
                points=points,
                max_points=40,
                message=f"/metadata responded in {elapsed} ms ({where})",
                citation=_FHIR_HTTP,
            )
        )
    else:
        # The message has always said this was not measured. Everything else about the finding
        # said it was: `observed` defaulted True, so `_withheld` returned 0, `_score` divided
        # 0 earned points by a denominator of 100 and published **0**, and `_finding_mark`
        # rendered a red "✗ Needs attention" beside the sentence "latency unmeasured". A check
        # that says in words that it did not run must not also be scored as one that ran and
        # failed.
        findings.append(
            Finding(
                code="R2",
                ok=False,
                points=0,
                max_points=0,
                message="latency unmeasured: endpoint unreachable",
                citation=_FHIR_HTTP,
                observed=False,
                withheld_points=40,
            )
        )
    return DimensionScore(
        key="reachability",
        title="Reachability",
        score=_score(findings),
        findings=tuple(findings),
        withheld_points=_withheld(findings),
    )


def grade_transparency(facts: CapabilityFacts, *, version_prefix: str = "4.") -> DimensionScore:
    findings: list[Finding] = []
    if not facts.observed:
        # Nothing arrived. Every check below this line is a statement about a document, and we
        # do not have one; the four findings this used to publish read as "the payer declares
        # none of this" and were disproved by the project's own history file.
        return _not_retrieved(
            "transparency",
            "Capability transparency",
            facts.parse_error or "no CapabilityStatement was retrieved on this run",
            _FHIR_CAPS,
        )
    if not facts.parsed or not facts.resource_type_ok:
        findings.append(
            Finding(
                code="T0",
                ok=False,
                points=0,
                max_points=100,
                message=f"CapabilityStatement unparseable: {facts.parse_error}",
                citation=_FHIR_CAPS,
            )
        )
        return DimensionScore(
            key="transparency", title="Capability transparency", score=0, findings=tuple(findings)
        )

    # Check the server against the release it intends to serve, not against R4 unconditionally
    # (calibration 2026-08-05): an endpoint registered as R5 declaring 5.0.0 is correct, and
    # marking it down for not being R4 would measure the wrong thing.
    version_ok = (facts.fhir_version or "").startswith(version_prefix)
    findings.append(
        Finding(
            code="T1",
            ok=version_ok,
            points=30 if version_ok else 0,
            max_points=30,
            message=(f"fhirVersion declared: {facts.fhir_version!r} (expected {version_prefix}x)"),
            citation=_FHIR_CAPS,
        )
    )
    sw = facts.software_name is not None and facts.software_version is not None
    findings.append(
        Finding(
            code="T2",
            ok=sw,
            points=20 if sw else 0,
            max_points=20,
            message="software name and version declared" if sw else "software name/version missing",
            citation=_FHIR_CAPS,
        )
    )
    # Calibration (2026-08-05): breadth alone under-credits deliberately narrow APIs. CMS Blue
    # Button 2.0 declares exactly three resource types by design (Patient/Coverage/EOB) with
    # every one fully documented; that is transparent, not deficient. Narrow-but-complete
    # (2-4 resource types, all documenting their interactions) earns full points.
    narrow_but_complete = (
        2 <= facts.resource_count < 5 and facts.resources_with_interactions == facts.resource_count
    )
    enough = facts.resource_count >= 5 or narrow_but_complete
    findings.append(
        Finding(
            code="T3",
            ok=enough,
            points=25 if enough else 0,
            max_points=25,
            message=f"{facts.resource_count} resource types declared"
            + (" (narrow but fully documented)" if narrow_but_complete else ""),
            citation=_FHIR_CAPS,
        )
    )
    covered = (
        facts.resource_count > 0 and facts.resources_with_interactions >= 0.8 * facts.resource_count
    )
    findings.append(
        Finding(
            code="T4",
            ok=covered,
            points=25 if covered else 0,
            max_points=25,
            message=(
                f"{facts.resources_with_interactions}/{facts.resource_count} "
                "declared resources document their interactions"
            ),
            citation=_FHIR_CAPS,
        )
    )
    return DimensionScore(
        key="transparency",
        title="Capability transparency",
        score=_score(findings),
        findings=tuple(findings),
        withheld_points=_withheld(findings),
    )


def _profiles_finding(facts: CapabilityFacts) -> Finding:
    """I1, saying what was checked and what was found there.

    The old failure message was "no recognized interoperability profiles declared", asserted
    after reading ``rest.resource.supportedProfile`` and nothing else. Two things were wrong with
    it: it claimed more than it had checked, and it told a payer nothing about which element to
    populate.
    """
    matched = sorted(
        {
            element
            for element, url in facts.conformance_profiles
            if any(marker in url.lower() for marker in _PROFILE_MARKERS)
        }
    )
    if matched:
        message = "US Core / CARIN / Da Vinci profiles declared in " + ", ".join(matched)
    elif facts.conformance_profiles:
        where = sorted({element for element, _ in facts.conformance_profiles})
        message = (
            f"{len(facts.conformance_profiles)} profile canonical(s) declared in "
            f"{', '.join(where)}, none of them US Core, CARIN, or Da Vinci; also checked "
            + ", ".join(e for e in _PROFILE_ELEMENTS if e not in where)
        )
    else:
        message = (
            "no profile canonical declared in "
            + ", ".join(_PROFILE_ELEMENTS[:-1])
            + f", or {_PROFILE_ELEMENTS[-1]}"
        )
    return Finding(
        code="I1",
        ok=bool(matched),
        points=_PROFILES_POINTS if matched else 0,
        max_points=_PROFILES_POINTS,
        message=message,
        citation=_US_CORE,
    )


def _unreadable_capability(facts: CapabilityFacts, *, max_points: int) -> Finding:
    """I0: a document arrived and is not a CapabilityStatement, so no conformance check ran.

    I1 and I3 both read a CapabilityStatement and nothing else. When one was retrieved and turned
    out to be an OperationOutcome, a sign-in page, or a JSON object of some other resourceType,
    those two checks used to run anyway, against a ``CapabilityFacts`` whose fields were dataclass
    defaults, and publish "no profile canonical declared in rest.resource.supportedProfile,
    rest.resource.profile, instantiates, imports, or meta.profile" and "no OAuth security service
    declared". Both read as findings about what a named organization published; neither was
    measured. I1's message names five elements as checked, and there was no document to check them
    in. Real payer servers answer ``/metadata`` with an OperationOutcome under HTTP 200, so this is
    an ordinary response shape, not a hypothetical one.

    ``grade_transparency`` has always handled the same input with one honest finding (T0) carrying
    the dimension's whole weight, and this is that treatment for interop. It carries exactly the
    points I1 and I3 would have carried, so the dimension score and the letter are unchanged: the
    endpoint is not newly credited or newly penalized, and what changes is only that the page
    states what happened instead of asserting two things nobody checked. The SMART document is a
    separate retrieval and keeps being graded on its own evidence (I2).
    """
    return Finding(
        code="I0",
        ok=False,
        points=0,
        max_points=max_points,
        message=(
            f"CapabilityStatement unreadable ({facts.parse_error}), so no conformance "
            "declaration and no declared security service could be read from it"
        ),
        citation=_US_CORE,
    )


def _oauth_finding(facts: CapabilityFacts) -> Finding:
    """I3, read from the CapabilityStatement. Only called when there is one to read."""
    declared = facts.declares_oauth_security
    return Finding(
        code="I3",
        ok=declared,
        points=_OAUTH_POINTS if declared else 0,
        max_points=_OAUTH_POINTS,
        message=(
            "OAuth/SMART security service declared in CapabilityStatement"
            if declared
            else "no OAuth security service declared"
        ),
        citation=_SMART_DISCOVERY,
    )


def _prose_only_note(facts: CapabilityFacts, *, declared: bool) -> Finding | None:
    """I4: the document names an implementation guide in prose but declares no profile.

    Worth zero points, deliberately. ``implementation.description`` is prose and
    ``supportedProfile`` is a machine-readable conformance claim, and the difference is the whole
    point of the element. But Aetna's document says CARIN three times and US Core once, and
    publishing a flat "no recognized interoperability profiles declared" about it invites a reader
    to conclude something the document contradicts. This says what is actually the case, and what
    would fix it.
    """
    if declared:
        return None
    prose = {
        "implementation.description": facts.implementation_description,
        "title": facts.title,
        "name": facts.name,
    }
    named_in = sorted(
        element
        for element, text in prose.items()
        if text and any(marker in text.lower() for marker in _PROSE_MARKERS)
    )
    if not named_in:
        return None
    return Finding(
        code="I4",
        ok=False,
        points=0,
        max_points=0,
        message=(
            f"informational: {', '.join(named_in)} names US Core, CARIN, or Da Vinci in "
            "prose, but no profile canonical is declared in any conformance element. Prose "
            "is not a conformance claim and scores nothing either way; adding "
            "rest.resource.supportedProfile entries would make the claim machine-readable"
        ),
        citation=_US_CORE,
    )


def grade_interop(
    facts: CapabilityFacts, smart: SmartFacts, *, kind: str = "reference"
) -> DimensionScore:
    findings: list[Finding] = []
    if not facts.observed:
        return _not_retrieved(
            "interop",
            "Interop readiness",
            facts.parse_error or "no CapabilityStatement was retrieved on this run",
            _US_CORE,
        )

    # A Provider Directory API is meant to be readable by anyone, so absence of SMART/OAuth is
    # the correct design there rather than a deficiency (calibration 2026-08-05). Where it is a
    # requirement it is 42 CFR 422.120's, which reaches Medicare Advantage organizations, with
    # parallel provisions for Medicaid (431.70) and CHIP (457.760); it is not required of QHP
    # issuers by 45 CFR 156.221, which is a Patient Access section. The scoring does not depend
    # on which: a directory published without an authorization surface is doing the right thing
    # whether a rule compelled it or the publisher chose it, and scoring it on an authorization
    # surface it should not have would penalize that. Reported as not applicable, no points
    # either way.
    public_by_design = kind == "payer_provider_directory"

    # Something arrived, but everything I1 and I3 read lives inside a CapabilityStatement. If it
    # is not one, those checks did not run, and running them anyway published two claims about a
    # named organization that came from dataclass defaults rather than from a document.
    readable = facts.parsed and facts.resource_type_ok
    if readable:
        profiles = _profiles_finding(facts)
        findings.append(profiles)
        prose_note = _prose_only_note(facts, declared=profiles.ok)
        if prose_note is not None:
            findings.append(prose_note)
    else:
        findings.append(
            _unreadable_capability(
                facts,
                # Exactly what I1 and the applicable I3 would have been worth, so the denominator
                # does not move and an unreadable document cannot change a letter.
                max_points=_PROFILES_POINTS + (0 if public_by_design else _OAUTH_POINTS),
            )
        )

    if public_by_design:
        findings.append(
            Finding(
                code="I2",
                ok=True,
                points=0,
                max_points=0,
                message="SMART discovery not applicable: a Provider Directory API is public by design",
                citation=_SMART_DISCOVERY,
            )
        )
        findings.append(
            Finding(
                code="I3",
                ok=True,
                points=0,
                max_points=0,
                message="OAuth security not applicable: a Provider Directory API is public by design",
                citation=_SMART_DISCOVERY,
            )
        )
        return DimensionScore(
            key="interop",
            title="Interop readiness",
            score=_score(findings),
            findings=tuple(findings),
            withheld_points=_withheld(findings),
        )

    if not smart.observed:
        # The CapabilityStatement came from a vantage that did not carry the SMART document, so
        # this run never asked for it. "Absent" would be a claim about the endpoint.
        findings.append(
            Finding(
                code="I2",
                ok=False,
                points=0,
                max_points=0,
                observed=False,
                # Out of the dimension's denominator, because nothing was measured -- and
                # recorded here, because leaving no trace of it is what let the remaining
                # checks be divided by a smaller number and come out higher.
                withheld_points=_SMART_POINTS,
                message=(
                    "no vantage retrieved .well-known/smart-configuration on this run, so "
                    "whether it is published is unknown"
                ),
                citation=_SMART_DISCOVERY,
            )
        )
    else:
        smart_ok = smart.parsed and smart.has_authorization_endpoint and smart.has_token_endpoint
        findings.append(
            Finding(
                code="I2",
                ok=smart_ok,
                points=_SMART_POINTS if smart_ok else 0,
                max_points=_SMART_POINTS,
                message=(
                    "SMART discovery document present and complete"
                    if smart_ok
                    else "SMART .well-known/smart-configuration absent or incomplete"
                ),
                citation=_SMART_DISCOVERY,
            )
        )
    if readable:
        findings.append(_oauth_finding(facts))
    return DimensionScore(
        key="interop",
        title="Interop readiness",
        score=_score(findings),
        findings=tuple(findings),
        withheld_points=_withheld(findings),
    )


#: The three dimensions in published order, each with the title the site prints and the weight
#: :func:`letter` actually applies.
#:
#: One definition, because there used to be two. The methodology page stated "35% / 35% / 30%"
#: as literal HTML, and nothing connected those characters to this mapping: reordering the
#: weights here left the whole suite green while the site went on publishing the old split as
#: its method. A page that describes a calculation it is not reading is the one kind of drift
#: this project cannot detect by reading either artifact alone, so the page renders from here.
WEIGHTED_DIMENSIONS: tuple[tuple[str, str, float], ...] = (
    ("reachability", "Reachability", 0.35),
    ("transparency", "Capability transparency", 0.35),
    ("interop", "Interop readiness", 0.30),
)

_WEIGHTS = {key: weight for key, _, weight in WEIGHTED_DIMENSIONS}


def _band(weighted: float) -> str:
    if weighted >= 90:
        return "A"
    if weighted >= 80:
        return "B"
    if weighted >= 70:
        return "C"
    if weighted >= 60:
        return "D"
    return "F"


def _dimension_bounds(dimension: DimensionScore) -> tuple[float, float] | None:
    """The lowest and highest this dimension could score, given what was measured.

    Equal when everything was measured, which is every dimension of every endpoint whose
    documents were all retrieved. They separate only when a check could not be made, and the
    width between them is exactly the part of the scale this run has nothing to say about.
    """
    if dimension.score is not None:
        return (float(dimension.score), float(dimension.score))
    withheld = dimension.withheld_points
    if not withheld:
        # Nothing in this dimension was observed at all; there is no bound to give.
        return None
    earned = sum(f.points for f in dimension.findings)
    total = sum(f.max_points for f in dimension.findings) + withheld
    if not total:
        return None
    return (100 * earned / total, 100 * (earned + withheld) / total)


def letter(dimensions: tuple[DimensionScore, ...], *, reachable: bool) -> str:
    """A letter, or NOT_OBSERVED when this run cannot pin one down.

    ``F`` used to mean two opposite things: an endpoint that answered and scored badly, and an
    endpoint nobody could reach. Only the first is a statement about the endpoint, and the site
    rendered both with one sentence about a network. They are now different values.

    The second confusion was quieter and ran the other way. A check that could not be made was
    dropped from its dimension's denominator, so the remaining checks were divided by a smaller
    number -- and dropping a check the endpoint was failing *raised* the grade. An endpoint whose
    SMART document no vantage retrieved came out better than the same endpoint with a SMART
    document that arrived and was unusable.

    So the weighted score is bounded rather than computed: once from the assumption that every
    unmade check would have failed, once from the assumption that every one would have passed.
    When both land in the same band, that band is the grade and nothing about it is a guess --
    which is every endpoint whose documents were all retrieved, since the bounds are then equal.
    When they land in different bands, this run does not know which letter is true, and the
    honest publication is that it does not.
    """
    if not reachable:
        return NOT_OBSERVED
    low = high = 0.0
    for dimension in dimensions:
        bounds = _dimension_bounds(dimension)
        if bounds is None:
            return NOT_OBSERVED
        weight = _WEIGHTS.get(dimension.key, 0.0)
        low += bounds[0] * weight
        high += bounds[1] * weight
    lowest, highest = _band(low), _band(high)
    return lowest if lowest == highest else NOT_OBSERVED


def build_scorecard(
    endpoint_id: str,
    name: str,
    metadata: FetchResult,
    facts: CapabilityFacts,
    smart: SmartFacts,
    *,
    kind: str = "reference",
    version_prefix: str = "4.",
    vantage: str = "unspecified",
    consensus: Consensus | None = None,
    observed_since: str | None = None,
    drift_events: tuple[str, ...] = (),
    drift_alternations: tuple[str, ...] = (),
    availability: str = "",
) -> Scorecard:
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
        grade=letter(
            dimensions, reachable=consensus.reachable if consensus is not None else metadata.ok
        ),
        reachable=consensus.reachable if consensus is not None else metadata.ok,
        dimensions=dimensions,
        kind=kind,
        vantage_note=consensus.detail if consensus is not None and consensus.vantages > 1 else "",
        observed_since=observed_since,
        drift_events=drift_events,
        drift_alternations=drift_alternations,
        availability=availability,
        # From the consensus when several vantages reported, so a disagreement survives; from
        # this run's own result when it is the only witness. Empty when the endpoint was
        # reached: there is no failure to name, and an empty tuple is not a population.
        failure_kinds=_failure_kinds(metadata, consensus),
    )


def _failure_kinds(metadata: FetchResult, consensus: Consensus | None) -> tuple[str, ...]:
    """What stopped this endpoint being reached, or nothing when it was reached.

    Gated on the *reconciled* reachability, not on ``metadata.ok``. An endpoint this vantage
    could not reach but another one did is reachable, and attaching this vantage's 403 to it
    would file a working endpoint under a failure population -- the 2026-08-05 misdiagnosis
    with a new field to express itself through.
    """
    if consensus is not None:
        return consensus.failure_kinds
    if metadata.ok:
        return ()
    return (metadata.failure_kind or UNCLASSIFIED,)
