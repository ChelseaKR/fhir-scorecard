"""What changed between two retrieved documents, or two runs, said in words.

``drift.py`` records *that* a declaration moved and which fingerprint keys moved with it. That is
the right amount of detail for a timeline and the wrong amount for the person who has to act on
it: "resource_count: 42 -> 41" does not say which resource left, and an operator running ``check``
nightly wants "Coverage no longer declares search-type" with the clause that defines it.

Three rules hold this module to observation.

**Nothing is inferred from silence.** A key absent on both sides is not mentioned. A key present
on one side and absent on the other is a change, and it is named as an addition or a removal
rather than as a move from a value nobody published.

**A document that could not be read diffs as unreadable against anything.** Not as an empty diff,
which would say the two agree, and not as "every resource was removed", which would say the
server withdrew what it is still serving. When either side is unreadable the report carries the
reason and *no field-level claims at all*; :func:`diff_capability` returns before it looks at a
single field. This is the same rule ``coverage.py`` and ``grading.py`` already apply: a
measurement this run was never entitled to make is not a measurement that failed.

**A vantage difference is not an endpoint change.** Two artifacts recorded from different vantage
points describe two journeys to one server, and the reconciliation rule in ``vantage.py`` is what
settles them. Handing them to this module produces "these were recorded from different vantages",
with that rule cited, rather than a list of things the endpoint is said to have done.

The fingerprint half of the comparison is :func:`drift.fingerprint_changes`, which the recorded
timeline and this verb both call, so there is one implementation of "what moved between two
fingerprints" and the two cannot drift apart. It lives in ``drift`` rather than here because
``vantage`` already imports that module and the dependency has to run in that direction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import (
    CapabilityFacts,
    SmartFacts,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.drift import fingerprint_changes
from fhir_scorecard.grading import WEIGHTED_DIMENSIONS

#: Spec clauses cited beside a change. These are the same URLs ``grading.py`` cites for the same
#: material, and ``test_diff.py`` asserts that they stay the same strings: a verb that cited a
#: different clause for the same fact than the grade does would be a second opinion, not a diff.
FHIR_CAPS = "https://hl7.org/fhir/R4/capabilitystatement.html"
FHIR_HTTP = "https://hl7.org/fhir/R4/http.html"
SMART_DISCOVERY = "https://hl7.org/fhir/smart-app-launch/conformance.html"
VANTAGE_RULE = "https://hl7.org/fhir/R4/http.html"

#: Re-exported so a reader of this module finds the fingerprint half where the rest of
#: the comparison lives. It is defined in ``drift`` because ``vantage`` imports that
#: module already, and the dependency has to run in that direction.
__all__ = ["fingerprint_changes"]

_DIMENSION_WEIGHTS = {key: weight for key, _, weight in WEIGHTED_DIMENSIONS}
_DIMENSION_TITLES = {key: title for key, title, _ in WEIGHTED_DIMENSIONS}


@dataclass(frozen=True)
class Change:
    """One thing that is different between the two sides.

    ``regression`` is narrow on purpose. It means the later side no longer has something the
    earlier side had: a resource, an interaction, a declared profile, a check that used to pass.
    An addition is never a regression, and neither is a measurement that stopped being available
    -- see :func:`_score_change`, where a score that went from a number to "not measured" is
    reported and deliberately not counted, because counting it would score an absence.
    """

    subject: str
    detail: str
    citation: str
    regression: bool = False

    def line(self) -> str:
        return f"{self.subject}: {self.detail} [{self.citation}]"


@dataclass(frozen=True)
class DiffReport:
    """The comparison, or the reason there is not one."""

    kind: str
    changes: tuple[Change, ...] = ()
    #: False when the two sides cannot be compared at all. ``changes`` is then always empty:
    #: an incomparable pair yields no field-level claims, only ``notes`` saying why.
    comparable: bool = True
    notes: tuple[str, ...] = ()

    @property
    def regressions(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.regression)

    def __post_init__(self) -> None:
        if not self.comparable and self.changes:
            raise ValueError(
                "an incomparable pair must carry no changes; reporting field-level differences "
                "for documents that could not be compared is the claim this refuses to make"
            )


def _incomparable(kind: str, *notes: str) -> DiffReport:
    return DiffReport(kind=kind, changes=(), comparable=False, notes=tuple(notes))


# ---------------------------------------------------------------------------
# CapabilityStatement
# ---------------------------------------------------------------------------


def _unreadable_reason(facts: CapabilityFacts | SmartFacts, side: str) -> str | None:
    """Why ``side`` cannot take part in a field-level comparison, or None if it can."""
    if not facts.observed:
        return f"{side}: no document was retrieved, so there is nothing to compare"
    if not facts.parsed:
        return f"{side}: could not be read ({facts.parse_error})"
    if isinstance(facts, CapabilityFacts) and not facts.resource_type_ok:
        return f"{side}: could not be read as a CapabilityStatement ({facts.parse_error})"
    return None


def _scalar_change(
    subject: str, before: object, after: object, citation: str, *, regression: bool = False
) -> Change | None:
    """A field that moved, or None where neither side published it.

    Absent on both sides is not a change and is not mentioned: two ``None`` values are equal, so
    the equality test below is what covers that case, and a separate ``both are None`` branch
    underneath it would be unreachable. Absent on *one* side is said as an appearance or a
    withdrawal rather than as a move from ``None``, which reads as a value rather than as silence.
    """
    if before == after:
        return None
    if before is None:
        return Change(subject, f"now declared as {after!r}, not declared before", citation)
    if after is None:
        return Change(subject, f"no longer declared (was {before!r})", citation, regression)
    return Change(subject, f"{before!r} -> {after!r}", citation)


def _resource_changes(before: CapabilityFacts, after: CapabilityFacts) -> list[Change]:
    """Resources and interactions gained and lost, named rather than counted."""
    before_res = dict(before.resource_interactions)
    after_res = dict(after.resource_interactions)
    changes: list[Change] = []
    for name in sorted(set(before_res) - set(after_res)):
        changes.append(Change(name, "no longer declared as a resource", FHIR_CAPS, regression=True))
    for name in sorted(set(after_res) - set(before_res)):
        changes.append(Change(name, "newly declared as a resource", FHIR_CAPS))
    for name in sorted(set(before_res) & set(after_res)):
        for interaction in sorted(set(before_res[name]) - set(after_res[name])):
            changes.append(
                Change(
                    f"{name} {interaction}",
                    "interaction no longer declared",
                    FHIR_CAPS,
                    regression=True,
                )
            )
        for interaction in sorted(set(after_res[name]) - set(before_res[name])):
            changes.append(Change(f"{name} {interaction}", "interaction newly declared", FHIR_CAPS))
    return changes


def diff_capability(before: CapabilityFacts, after: CapabilityFacts) -> DiffReport:
    """What the server's declaration gained and lost between the two documents."""
    reasons = [
        reason
        for reason in (
            _unreadable_reason(before, "first document"),
            _unreadable_reason(after, "second document"),
        )
        if reason is not None
    ]
    if reasons:
        # No field-level claims. A document that could not be read has not withdrawn anything.
        return _incomparable("capability", *reasons)

    changes: list[Change] = []

    for subject, attribute in (
        ("FHIR version", "fhir_version"),
        ("software name", "software_name"),
        ("software version", "software_version"),
    ):
        change = _scalar_change(
            subject, getattr(before, attribute), getattr(after, attribute), FHIR_CAPS
        )
        if change is not None:
            changes.append(change)

    if before.declares_oauth_security != after.declares_oauth_security:
        changes.append(
            Change(
                "OAuth security",
                "declared" if after.declares_oauth_security else "no longer declared",
                FHIR_CAPS,
                regression=not after.declares_oauth_security,
            )
        )

    changes.extend(_resource_changes(before, after))

    before_profiles = {canonical for _, canonical in before.conformance_profiles}
    after_profiles = {canonical for _, canonical in after.conformance_profiles}
    for canonical in sorted(before_profiles - after_profiles):
        changes.append(Change("profile", f"withdrawn: {canonical}", FHIR_CAPS, regression=True))
    for canonical in sorted(after_profiles - before_profiles):
        changes.append(Change("profile", f"newly declared: {canonical}", FHIR_CAPS))

    return DiffReport(kind="capability", changes=tuple(changes))


def diff_smart(before: SmartFacts, after: SmartFacts) -> DiffReport:
    """What the SMART discovery document gained and lost."""
    reasons = [
        reason
        for reason in (
            _unreadable_reason(before, "first document"),
            _unreadable_reason(after, "second document"),
        )
        if reason is not None
    ]
    if reasons:
        return _incomparable("smart", *reasons)

    changes: list[Change] = []
    for subject, attribute in (
        ("authorization endpoint", "has_authorization_endpoint"),
        ("token endpoint", "has_token_endpoint"),
    ):
        was, now = getattr(before, attribute), getattr(after, attribute)
        if was != now:
            changes.append(
                Change(
                    subject,
                    "declared" if now else "no longer declared",
                    SMART_DISCOVERY,
                    regression=not now,
                )
            )
    return DiffReport(kind="smart", changes=tuple(changes))


# ---------------------------------------------------------------------------
# scorecards.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    """One published run, reduced to the two things a diff compares."""

    vantage: str | None
    #: (endpoint_id, dimension key, finding code) -> (ok, message, citation)
    findings: dict[tuple[str, str, str], tuple[bool, str, str]] = field(default_factory=dict)
    #: (endpoint_id, dimension key) -> score, which is None where the run published none
    scores: dict[tuple[str, str], int | None] = field(default_factory=dict)
    grades: dict[str, str] = field(default_factory=dict)


def _read_run(payload: dict[str, Any]) -> _Run:
    findings: dict[tuple[str, str, str], tuple[bool, str, str]] = {}
    scores: dict[tuple[str, str], int | None] = {}
    grades: dict[str, str] = {}
    raw_vantage = payload.get("vantage")
    for card in payload.get("scorecards", []):
        if not isinstance(card, dict):
            continue
        endpoint_id = str(card.get("endpoint_id", ""))
        if isinstance(card.get("grade"), str):
            grades[endpoint_id] = card["grade"]
        for dimension in card.get("dimensions", []):
            if not isinstance(dimension, dict):
                continue
            key = str(dimension.get("key", ""))
            raw_score = dimension.get("score")
            scores[(endpoint_id, key)] = raw_score if isinstance(raw_score, int) else None
            for finding in dimension.get("findings", []):
                if not isinstance(finding, dict):
                    continue
                findings[(endpoint_id, key, str(finding.get("code", "")))] = (
                    bool(finding.get("ok")),
                    str(finding.get("message", "")),
                    str(finding.get("citation", "")),
                )
    return _Run(
        vantage=raw_vantage if isinstance(raw_vantage, str) else None,
        findings=findings,
        scores=scores,
        grades=grades,
    )


def _score_change(
    endpoint_id: str, key: str, before: int | None, after: int | None
) -> Change | None:
    """A dimension's score, moved.

    The two directions that involve ``None`` are the interesting ones. A dimension publishes no
    score when part of its scale was never measured, so ``60 -> None`` is not a fall to zero and
    ``None -> 60`` is not a rise from it; in both cases one side is not on the published scale at
    all. They are reported, because a reader needs to know the comparison stopped being possible,
    and they are **not** regressions, because calling the loss of a measurement a regression is
    scoring an absence. That is the defect this repository fixed in ``grading.letter``.
    """
    if before == after:
        return None
    title = _DIMENSION_TITLES.get(key, key)
    weight = _DIMENSION_WEIGHTS.get(key)
    weighted = f", weight {weight:g}" if weight is not None else ""
    if before is None or after is None:
        missing = "the first run" if before is None else "the second run"
        return Change(
            f"{endpoint_id} {title}",
            f"not comparable: {missing} published no score for this dimension"
            f" (other side {after if before is None else before}){weighted}",
            FHIR_CAPS,
        )
    direction = "fell" if after < before else "rose"
    return Change(
        f"{endpoint_id} {title}",
        f"score {direction} {before} -> {after}{weighted}",
        FHIR_CAPS,
        regression=after < before,
    )


def _finding_changes(first: _Run, second: _Run) -> list[Change]:
    """Findings that appeared, disappeared, or flipped, keyed by endpoint, dimension and code."""
    changes: list[Change] = []
    for key in sorted(set(first.findings) | set(second.findings)):
        endpoint_id, dimension, code = key
        was, now = first.findings.get(key), second.findings.get(key)
        subject = f"{endpoint_id} {_DIMENSION_TITLES.get(dimension, dimension)} {code}"
        if was is None and now is not None:
            changes.append(
                Change(subject, f"finding appeared: {now[1]}", now[2] or FHIR_CAPS, not now[0])
            )
        elif now is None and was is not None:
            changes.append(Change(subject, f"finding disappeared: {was[1]}", was[2] or FHIR_CAPS))
        elif was is not None and now is not None and was[0] != now[0]:
            verdict = "now passes" if now[0] else "now fails"
            changes.append(Change(subject, f"{verdict}: {now[1]}", now[2] or FHIR_CAPS, not now[0]))
    return changes


def diff_scorecards(before: dict[str, Any], after: dict[str, Any]) -> DiffReport:
    """Findings that appeared or disappeared, and dimension scores that moved."""
    first, second = _read_run(before), _read_run(after)
    if first.vantage is not None and second.vantage is not None and first.vantage != second.vantage:
        # Two journeys to one server. Which of them is the endpoint's truth is vantage.py's
        # question, and answering it here by subtraction would publish a network difference as a
        # thing the payer did.
        return _incomparable(
            "scorecards",
            f"these runs were recorded from different vantages ({first.vantage} and "
            f"{second.vantage}); reconciling them is vantage.reconcile's rule, and a difference "
            "between vantages is not a change the endpoint made",
        )

    changes: list[Change] = []
    for endpoint_id in sorted(set(first.grades) | set(second.grades)):
        was, now = first.grades.get(endpoint_id), second.grades.get(endpoint_id)
        change = _scalar_change(f"{endpoint_id} grade", was, now, FHIR_CAPS)
        if change is not None:
            changes.append(change)

    for key in sorted(set(first.scores) | set(second.scores)):
        endpoint_id, dimension = key
        if key not in first.scores or key not in second.scores:
            continue
        change = _score_change(endpoint_id, dimension, first.scores[key], second.scores[key])
        if change is not None:
            changes.append(change)

    changes.extend(_finding_changes(first, second))
    return DiffReport(kind="scorecards", changes=tuple(changes))


# ---------------------------------------------------------------------------
# probes-*.json
# ---------------------------------------------------------------------------


def _probe_vantages(payload: dict[str, Any]) -> set[str]:
    probes = payload.get("probes")
    found: set[str] = set()
    if isinstance(probes, dict):
        for probe in probes.values():
            if isinstance(probe, dict) and isinstance(probe.get("vantage"), str):
                found.add(probe["vantage"])
    return found


def diff_probes(before: dict[str, Any], after: dict[str, Any]) -> DiffReport:
    """Two probe artifacts.

    When they carry different vantage labels the honest answer is that they are not a before and
    an after; they are two places looking at the same server on possibly the same day.
    """
    first, second = _probe_vantages(before), _probe_vantages(after)
    if first and second and first != second:
        return _incomparable(
            "probes",
            f"these artifacts were recorded from different vantages "
            f"({', '.join(sorted(first))} and {', '.join(sorted(second))}); vantage.reconcile is "
            "what settles a disagreement between vantages, and a difference between them is not "
            "a change the endpoint made",
        )
    changes: list[Change] = []
    raw_before, raw_after = before.get("probes"), after.get("probes")
    before_probes: dict[str, Any] = raw_before if isinstance(raw_before, dict) else {}
    after_probes: dict[str, Any] = raw_after if isinstance(raw_after, dict) else {}
    for endpoint_id in sorted(set(before_probes) | set(after_probes)):
        was = before_probes.get(endpoint_id)
        now = after_probes.get(endpoint_id)
        if not isinstance(was, dict) or not isinstance(now, dict):
            continue
        change = _scalar_change(
            f"{endpoint_id} reachable", was.get("reachable"), now.get("reachable"), FHIR_HTTP
        )
        if change is not None:
            changes.append(
                Change(
                    change.subject,
                    change.detail,
                    change.citation,
                    regression=not now.get("reachable"),
                )
            )
    return DiffReport(kind="probes", changes=tuple(changes))


# ---------------------------------------------------------------------------
# Reading a side, and rendering
# ---------------------------------------------------------------------------


def classify(raw: bytes) -> str:
    """What kind of artifact ``raw`` is, by what it contains rather than by its filename."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "unreadable"
    if not isinstance(payload, dict):
        return "unreadable"
    if isinstance(payload.get("scorecards"), list):
        return "scorecards"
    if isinstance(payload.get("probes"), dict):
        return "probes"
    if "resourceType" in payload:
        return "capability"
    if {"authorization_endpoint", "token_endpoint", "capabilities"} & set(payload):
        return "smart"
    return "unreadable"


def diff_bytes(before: bytes, after: bytes) -> DiffReport:
    """Compare two artifacts, deciding what they are from their content."""
    first, second = classify(before), classify(after)
    kinds = {first, second} - {"unreadable"}
    if len(kinds) > 1:
        return _incomparable(
            "mixed",
            f"these are different kinds of artifact ({first} and {second}); a diff between them "
            "would have to invent a correspondence neither document states",
        )
    if not kinds:
        # Neither side could be identified. Capability is the pair's kind by default because it
        # is the document the verb is pointed at most, and the report says only that.
        return _incomparable(
            "unreadable",
            "first document: could not be read as any artifact this tool publishes",
            "second document: could not be read as any artifact this tool publishes",
        )
    kind = kinds.pop()
    if kind == "capability":
        return diff_capability(parse_capability(before), parse_capability(after))
    if kind == "smart":
        return diff_smart(parse_smart(before), parse_smart(after))
    try:
        first_payload = json.loads(before.decode("utf-8"))
        second_payload = json.loads(after.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return _incomparable(kind, f"one side could not be read ({type(exc).__name__})")
    if not isinstance(first_payload, dict) or not isinstance(second_payload, dict):
        return _incomparable(kind, "one side is not a JSON object")
    if kind == "scorecards":
        return diff_scorecards(first_payload, second_payload)
    return diff_probes(first_payload, second_payload)


def diff_paths(before: Path, after: Path) -> DiffReport:
    return diff_bytes(before.read_bytes(), after.read_bytes())


def render_text(report: DiffReport) -> str:
    """One line per change, deterministic for the same inputs."""
    lines: list[str] = []
    if not report.comparable:
        lines.append(f"no comparison was made ({report.kind})")
        lines.extend(f"  {note}" for note in report.notes)
        return "\n".join(lines) + "\n"
    lines.extend(f"  {note}" for note in report.notes)
    if not report.changes:
        lines.append("no change")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(report.changes)} change(s)")
    lines.extend(f"  {change.line()}" for change in report.changes)
    regressions = report.regressions
    if regressions:
        lines.append(f"{len(regressions)} of them removed something the earlier side had")
    return "\n".join(lines) + "\n"


def render_json(report: DiffReport) -> str:
    payload = {
        "kind": report.kind,
        "comparable": report.comparable,
        "notes": list(report.notes),
        "changes": [
            {
                "subject": change.subject,
                "detail": change.detail,
                "citation": change.citation,
                "regression": change.regression,
            }
            for change in report.changes
        ],
        "regression_count": len(report.regressions),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
