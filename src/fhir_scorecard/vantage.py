"""Reconcile probe results from several vantage points into one honest reachability fact.

A single probing network is an unreliable narrator. On 2026-08-05 a live payer endpoint was
recorded as dead because a middlebox on the probing network intercepted TLS; the endpoint was
fine and the network was not. Probing from more than one place and reconciling the results is
the fix, and it is the difference between "this endpoint is down" and "I could not get there
from here".

The reconciliation rule is deliberately asymmetric. **One vantage reaching an endpoint proves it
is reachable; one vantage failing proves nothing.** Unreachability is a claim about the world and
needs agreement across vantages; reachability is a demonstrated fact and needs only one witness.

Two things this module now refuses to overstate, because the published sentence is read as a
measurement:

* **A vantage is counted once.** CI merged the publishing run's own probe with the probe
  artifacts, and the publishing run carried the same label as one of them, so every card said
  "reachable from all 4 vantage(s)" when three vantages reported. Duplicate labels are collapsed
  into one observation before anything is counted or averaged.
* **Vantages are counted separately from networks.** A vantage label is ``<network>/<host>``, and
  three hosts on one provider's network are one network's opinion sampled three times. The
  failure modes a payer edge actually applies (source-address and ASN rules, bot filters, geo
  rules, rate limits) are correlated across all of them, so the consensus says how many networks
  were behind a result and never calls one network several.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import parse_capability
from fhir_scorecard.drift import fingerprint
from fhir_scorecard.fetch import UNCLASSIFIED, normalise_failure_kind


@dataclass(frozen=True)
class VantageProbe:
    """One vantage's observation of one endpoint.

    A probe that reached the endpoint carries the documents it retrieved. Without them a merge
    can establish that an endpoint is *up* while still scoring its content zero, which would
    report an F for material the probing vantage simply never received: the original mistake
    wearing a different hat.
    """

    vantage: str
    reachable: bool
    elapsed_ms: int
    error: str | None = None
    capability: str | None = None  # raw /metadata body, when this vantage retrieved it
    smart: str | None = None  # raw SMART discovery body, when retrieved
    # The HTTP status, when the endpoint answered with one. ``reachable`` is 2xx-only, which is
    # the right test for "did we get a document", and the wrong one for "is this endpoint up".
    # A server that replies 415 or 403 has completed DNS, TCP, TLS and HTTP; it is running and
    # refusing this particular request, which is a different published sentence from one this
    # run could not reach. Carried so the merge can tell those apart. See :func:`reconcile`.
    status: int | None = None
    # Which named condition stopped this vantage, from :data:`fhir_scorecard.fetch.FAILURE_KINDS`
    # (#117). ``error`` is the sentence a reader is shown and stays exactly what it was; this is
    # the same fact as data, so populations can be counted without parsing prose. ``None`` on a
    # probe that reached the endpoint -- there is no failure to classify -- and never a
    # placeholder, so "reached" and "failed for a reason nobody named" stay apart.
    failure_kind: str | None = None
    # Whether this vantage asked for ``/.well-known/smart-configuration``. ``smart`` being None
    # says only that no document came back, and that is two opposite facts wearing one value: a
    # vantage that requested it and was answered 404 has *observed* that it is not served, and a
    # vantage that never asked has observed nothing. Measured on the live site 2026-09-12:
    # eighteen of eighty-one endpoints published no letter at all because the merge could only
    # read the second, weaker meaning -- including hapi-fhir-r4, whose /metadata answered 200
    # from all three vantages and whose SMART document answered 404 from all three.
    #
    # Default False, which is the conservative reading and the only safe one for a file this
    # project did not write: a probe file from an older revision, or from a vantage this project
    # does not operate (#100), carries no such field, and nothing may be concluded from its
    # silence. Every probe this repository writes sets it True, because
    # ``cli._grade_endpoint`` requests both documents unconditionally.
    smart_requested: bool = False

    @property
    def network(self) -> str:
        """The network this vantage sits on, by the ``<network>/<host>`` label convention.

        ``github-actions/ubuntu-latest`` and ``github-actions/macos-latest`` are two hosts on one
        network; ``davis-ca/residential`` is a different one. A label with no separator is its
        own network, which is the conservative reading: it never merges two things into one.
        """
        return self.vantage.split("/", 1)[0] or self.vantage


@dataclass(frozen=True)
class VantageReport:
    """What one vantage saw, kept as its own row rather than folded into a verdict.

    :func:`reconcile` exists to produce one endpoint-level fact from several vantages, and for
    reachability that fact is sound: one vantage reaching an endpoint settles that it is up. What
    it cannot do is carry *disagreement*, and on a scorecard about reachability the disagreement
    is the most informative thing a run produces.

    Measured 2026-09-12 across all 81 registry endpoints, three GitHub-hosted vantages against
    one residential vantage: three disagreed, and **they did not disagree in one direction**.
    ``ambetter-centene-provider-directory`` answered residentially and 403'd from all three
    runners; ``capital-bluecross`` and ``chg-provider-directory`` did the reverse, the latter two
    being the 2026-08-05 TLS-interception incident still live on that residential network. So
    there is no vantage that is right, and electing one would relocate the original misdiagnosis
    onto three different named companies rather than remove it.

    These rows are what lets the site say "reachable from 2 of 3 vantages" instead of picking a
    winner. They are published, never scored.
    """

    vantage: str
    network: str
    reachable: bool
    #: The HTTP status this vantage received, when it received one at all. Present on a refusal
    #: that completed an HTTP exchange, which is a materially different fact from a connection
    #: that never got that far.
    status: int | None = None
    #: From the closed vocabulary in :data:`fhir_scorecard.fetch.FAILURE_KINDS`. ``None`` on a
    #: vantage that reached the endpoint: there is no failure to classify.
    failure_kind: str | None = None
    #: Milliseconds, and ``None`` rather than ``0`` when nothing was measured. A latency nobody
    #: recorded published as zero is the exact coercion ``probe_entry_failure`` exists to refuse,
    #: and it would be the fastest possible reading of a probe that never completed.
    elapsed_ms: int | None = None
    #: The sentence this vantage reported, verbatim. ``None`` when it reached the endpoint.
    error: str | None = None


def _report_for(probe: VantageProbe) -> VantageReport:
    """One published row from one collapsed probe."""
    return VantageReport(
        vantage=probe.vantage,
        network=probe.network,
        reachable=probe.reachable,
        status=probe.status,
        # A probe that reached has no failure to classify, and a placeholder here would file a
        # working endpoint into a failure population.
        failure_kind=None if probe.reachable else probe.failure_kind,
        # Only from a vantage that reached: a failed probe's elapsed time measures how long this
        # project waited, not how fast the endpoint is.
        elapsed_ms=probe.elapsed_ms if probe.reachable else None,
        error=None if probe.reachable else probe.error,
    )


@dataclass(frozen=True)
class Consensus:
    reachable: bool
    elapsed_ms: int
    vantages: int  # distinct vantages, after duplicate labels are collapsed
    agreeing: int
    detail: str
    # Distinct networks behind those vantages. Several hosts on one provider's network share its
    # address space, its reputation, and any rule a payer edge applies to it, so this is the
    # number that says how independent the agreement actually was.
    networks: int = 0
    # Documents from whichever vantage retrieved them, so content can be graded even when the
    # local vantage was blocked.
    capability: str | None = None
    smart: str | None = None
    # Whether any vantage that *reached* the endpoint asked for the SMART discovery document.
    # Read together with ``smart``: both set means a document came back, ``smart`` None with this
    # True means every vantage that got the CapabilityStatement asked for the SMART document and
    # none was served one -- which is an observation -- and both falsy means this run does not
    # know whether it was ever requested, which is not.
    #
    # Computed over the reached vantages only, matching the branch in ``cli._grade_endpoint``
    # that it exists to make reproducible: a vantage that could not complete a connection asked
    # for nothing it can report on.
    smart_requested: bool = False
    # How many vantages got an HTTP answer of any status, including the ones whose answer was a
    # refusal. Separate from ``agreeing``, which counts vantages that retrieved a document.
    answered: int = 0
    # Every condition observed when NO vantage reached the endpoint, sorted, deduplicated, and
    # never reduced to one. Three vantages reporting three different kinds is a disagreement and
    # is published as all three: picking a winner here would be the same mistake as calling three
    # hosts on one network three networks, one level down. Empty whenever any vantage reached,
    # because then there is a measurement and the failures are a fact about those vantages.
    failure_kinds: tuple[str, ...] = ()
    #: Every reporting vantage's own result, in vantage order, after duplicate labels are
    #: collapsed. Always populated when any vantage reported, whether they agreed or not: the
    #: agreement is as much a published fact as the disagreement, and a surface that only showed
    #: the rows when they differed would make "3 of 3" unavailable to a reader.
    reports: tuple[VantageReport, ...] = ()
    # Set when reachable vantages returned CapabilityStatements that are not byte-identical.
    # One hostname in front of two backends is the alternation story this project already tells
    # over time; seen across vantages in a single run it is the same fact, and discarding it
    # silently is how the wrong one of the two gets published as the endpoint's declaration.
    declaration_disagreement: str | None = None

    @property
    def unanimous(self) -> bool:
        return self.vantages > 0 and self.agreeing == self.vantages


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def collapse_by_vantage(probes: list[VantageProbe]) -> list[VantageProbe]:
    """One observation per vantage label, so no vantage is counted twice.

    CI produced four probes from three vantages: the publishing run probed under
    ``github-actions/ubuntu-latest`` and then merged the artifact written under the same label.
    Counting that twice inflated the published vantage count, gave one network path double weight
    in the median latency, and would have made a single blocked vantage read as two independent
    failures. Same label, same network path, one observation.

    Within a label the asymmetry still holds: if any sample reached the endpoint, that vantage
    reached it, and its latency is the median of the samples that did.
    """
    by_vantage: dict[str, list[VantageProbe]] = {}
    for probe in probes:
        by_vantage.setdefault(probe.vantage, []).append(probe)

    collapsed: list[VantageProbe] = []
    for vantage, group in by_vantage.items():
        if len(group) == 1:
            collapsed.append(group[0])
            continue
        reached = [p for p in group if p.reachable]
        if reached:
            collapsed.append(
                VantageProbe(
                    vantage=vantage,
                    reachable=True,
                    elapsed_ms=_median([p.elapsed_ms for p in reached]),
                    error=None,
                    # ``is not None``, not truthiness: a sample that reached the endpoint and
                    # got back an empty body has *retrieved* a document, just an empty one, and
                    # that is a different fact from a sample that retrieved nothing at all. A
                    # bare truthiness check treated an empty string the same as "no document",
                    # which let a reachable, genuinely-empty response fall back to a later
                    # sample's None and disappear.
                    capability=next(
                        (p.capability for p in reached if p.capability is not None), None
                    ),
                    smart=next((p.smart for p in reached if p.smart is not None), None),
                    status=next((p.status for p in reached if p.status is not None), None),
                    # Carried, not defaulted. This branch rebuilds the dataclass field by field,
                    # so a field it forgets reads back as its default -- and for this one the
                    # default is the value that suppresses a grade. One sample of a vantage
                    # having asked is enough for that vantage to have asked.
                    smart_requested=any(p.smart_requested for p in reached),
                )
            )
        else:
            errors = sorted({p.error for p in group if p.error})
            # One vantage, one condition -- when its samples agree. When they do not, this
            # vantage did not establish a condition, and ``unclassified`` says exactly that.
            # It is not the nearest label: it is the absence of one, and the joined sentence
            # above still names every condition the samples reported.
            kinds = sorted({p.failure_kind for p in group if p.failure_kind})
            collapsed.append(
                VantageProbe(
                    vantage=vantage,
                    reachable=False,
                    elapsed_ms=0,
                    error="; ".join(errors) or None,
                    # An answering-but-refusing sample still demonstrates the endpoint is up,
                    # so its status outlives the collapse even though none of the samples in
                    # this group retrieved a document.
                    status=next((p.status for p in group if p.status is not None), None),
                    failure_kind=kinds[0] if len(kinds) == 1 else UNCLASSIFIED,
                )
            )
    return collapsed


def reconcile(raw_probes: list[VantageProbe]) -> Consensus:
    """Combine per-vantage probes. Reaching an endpoint from anywhere settles that it is up."""
    if not raw_probes:
        return Consensus(
            reachable=False,
            elapsed_ms=0,
            vantages=0,
            agreeing=0,
            networks=0,
            detail="no vantage reported",
            # Not ``("unclassified",)``. No vantage reported, so there is no failure to classify
            # and no population this endpoint belongs to; an empty tuple says that and a
            # one-element tuple would put it in a bucket it never entered.
            failure_kinds=(),
        )

    probes = collapse_by_vantage(raw_probes)
    networks = sorted({p.network for p in probes})
    reached = [p for p in probes if p.reachable]
    failed = [p for p in probes if not p.reachable]
    if not reached:
        # Every vantage failed. Even now this is only unreachability *from here*: when every
        # vantage sits on one network, a filter applied to that network's address space and an
        # endpoint that is genuinely down produce the identical result, and the sentence has to
        # say so rather than settle it. The failure modes are shown either way: identical errors
        # everywhere read very differently from a scattered mix.
        joined = "; ".join(sorted({p.error or "unknown" for p in failed}))
        # Every condition seen, never one. A run where one vantage got a 401 and two got a DNS
        # failure has observed two different facts about two different actors, and reducing them
        # to whichever came first would publish the endpoint into one population and delete the
        # evidence it was ever in the other.
        kinds = tuple(sorted({p.failure_kind or UNCLASSIFIED for p in failed}))
        answered = [p for p in failed if p.status is not None]
        if answered:
            # The endpoint answered. Whatever else this run failed to do, it did not fail to
            # reach the host: an HTTP status means DNS resolved, TCP connected, TLS completed
            # and the server chose a response. Saying "cannot separate down from blocked" here
            # would be the 2026-08-05 misdiagnosis with its sign reversed - and it would print
            # the very status that disproves it. What this run did not get is a document.
            statuses = ", ".join(
                str(s) for s in sorted({p.status for p in answered if p.status is not None})
            )
            detail = (
                f"answered HTTP {statuses} from {len(answered)} of {len(probes)} vantages but "
                f"returned no usable document: the endpoint is running and refusing this "
                f"request, which is not the same as being unreachable: {joined}"
            )
        elif len(networks) == 1:
            detail = (
                f"not reached from any of the {len(probes)} vantages tried, all on one "
                f"network ({networks[0]}), so this run cannot separate an endpoint that is "
                f"down from one that does not answer this network: {joined}"
            )
        else:
            detail = (
                f"not reached from any of the {len(probes)} vantages tried, across "
                f"{len(networks)} networks: {joined}"
            )
        if len(kinds) > 1:
            # Said in the sentence as well as carried in the data, because a reader looking at
            # one endpoint sees the sentence. Vantages that disagree about *why* they failed have
            # not established one condition, and a page that showed only the first would be
            # asserting agreement that this run does not have.
            detail = (
                f"{detail}. The vantages did not agree on why: "
                f"{', '.join(kinds)} were each reported, so this run establishes no single "
                f"condition"
            )
        return Consensus(
            reachable=False,
            elapsed_ms=0,
            vantages=len(probes),
            agreeing=0,
            networks=len(networks),
            detail=detail,
            answered=len(answered),
            failure_kinds=kinds,
            reports=tuple(_report_for(p) for p in probes),
        )

    # Median latency across the vantages that succeeded: one slow network path should not
    # define the number, and neither should one unusually fast one.
    median = _median([p.elapsed_ms for p in reached])

    reached_networks = sorted({p.network for p in reached})

    if failed:
        names = ", ".join(sorted(p.vantage for p in failed))
        why = "; ".join(sorted({p.error or "unknown" for p in failed}))
        # No attribution. This used to close with "which is a property of that network rather
        # than of the endpoint", and that is a claim no run can make: a 403, a 429 or a geo
        # rule is the endpoint's policy toward that source, not the network misbehaving. The
        # asymmetry this module rests on is that one vantage failing proves nothing - which
        # cuts both ways, and means it cannot prove whose fault the failure was either.
        detail = (
            f"reachable from {len(reached)} of {len(probes)} vantages; "
            f"not reached from {names} ({why})"
        )
    elif len(probes) > 1 and len(networks) == 1:
        detail = (
            f"reachable from all {len(probes)} vantages, which are {len(probes)} hosts on "
            f"one network ({networks[0]}): one network's view sampled {len(probes)} times, "
            f"not {len(probes)} independent networks"
        )
    elif len(probes) > 1:
        detail = f"reachable from all {len(probes)} vantages across {len(networks)} networks"
    else:
        detail = f"reachable from {probes[0].vantage}"

    capability, disagreement = _agreed_capability(reached)
    if disagreement is not None:
        # Published, not just recorded. Two vantages seeing two different declarations in one
        # run is the same fact the alternation rule reports over time - one hostname in front
        # of more than one backend - and a reader looking at a grade derived from one of them
        # is entitled to know the other existed.
        detail = f"{detail}. {disagreement}"
    return Consensus(
        reachable=True,
        elapsed_ms=median,
        vantages=len(probes),
        agreeing=len(reached),
        # Counted over the vantages that actually reached the endpoint, not over every vantage
        # that reported. A median computed from one reachable vantage was being published as
        # "median across 1 reachable vantages across 3 networks", which describes a breadth of
        # agreement the number does not have.
        networks=len(reached_networks),
        detail=detail,
        capability=capability,
        # Borrowed independently of the CapabilityStatement. It used to be taken from whichever
        # probe supplied the capability, so a vantage-local block on /.well-known - a WAF rule,
        # a transient 5xx - discarded a peer's complete SMART document and published "absent or
        # incomplete" about a named payer. That is up to 35 of 100 interop points, wider than a
        # letter band, decided by which probe happened to be first.
        smart=next((p.smart for p in reached if p.smart is not None), None),
        # One vantage that asked settles that it was asked, the same asymmetry this module rests
        # on everywhere else. A peer that cannot say whether it asked does not unsettle a peer
        # that did, and a run where nobody can say keeps the honest third state.
        smart_requested=any(p.smart_requested for p in reached),
        answered=sum(1 for p in probes if p.status is not None or p.reachable),
        declaration_disagreement=disagreement,
        # Over `probes`, not `reached`: a vantage that failed while others succeeded is exactly
        # the row a reader most needs, and building this from the reached set would publish
        # unanimity that this run did not observe.
        reports=tuple(_report_for(p) for p in probes),
    )


def _declaration_key(document: str) -> str:
    """What this document *declares*, as a comparable key, ignoring how it was rendered.

    Compared on the drift fingerprint rather than on bytes, and the difference is not academic.
    ``drift.py`` fingerprints declared facts precisely so "a server that merely re-renders its
    CapabilityStatement does not read as changed", and byte equality across vantages fails that
    test for the same reasons it fails across days: a generation timestamp, a request id, a
    load balancer serving two equally-current renderings, or a dict that serialised in a
    different order. Measured on the live registry, byte comparison called 19 of 45 endpoints
    disagreeing in one run - including three-of-three unique documents from a reference server
    that plainly does not serve three different declarations.

    A document that cannot be parsed is keyed by its own bytes: two unparseable responses are
    only the same non-answer if they are the same non-answer.
    """
    facts = parse_capability(document.encode("utf-8"))
    if not facts.parsed or not facts.resource_type_ok:
        return "unparseable:" + document
    return json.dumps(fingerprint(facts), sort_keys=True)


def _agreed_capability(reached: list[VantageProbe]) -> tuple[str | None, str | None]:
    """The declaration the most vantages returned, and a note when they did not all agree.

    Selection used to be ``next(p for p in reached if p.capability is not None)`` - first in
    list order, and list order is the order ``probes/*.json`` happened to glob. A vantage whose
    network answers with a 200 interstitial sorts before a vantage that retrieved the real
    CapabilityStatement purely on filename, and then defines the grade, every finding, and the
    drift fingerprint that decides whether this endpoint is recorded as having changed.

    Majority instead, over declarations rather than bytes (see :func:`_declaration_key`), ties
    broken by vantage label so the result is deterministic rather than merely different.
    ``is not None``, not truthiness, throughout: a vantage that reached the endpoint and got
    back an empty body retrieved a document, just an empty one, and that is a different fact
    from a vantage that retrieved nothing at all.
    """
    holders = [p for p in reached if p.capability is not None]
    if not holders:
        return None, None

    by_declaration: dict[str, list[VantageProbe]] = {}
    for probe in holders:
        by_declaration.setdefault(_declaration_key(probe.capability or ""), []).append(probe)

    def rank(item: tuple[str, list[VantageProbe]]) -> tuple[int, str]:
        _, group = item
        return (-len(group), min(p.vantage for p in group))

    _, group = min(by_declaration.items(), key=rank)
    # Within the winning group every document declares the same thing, so which one is graded
    # cannot change a finding; pick by vantage label so the published bytes are still stable.
    document = min(group, key=lambda p: p.vantage).capability
    if len(by_declaration) == 1:
        return document, None

    counts = "; ".join(
        f"{', '.join(sorted(p.vantage for p in g))}"
        for _, g in sorted(by_declaration.items(), key=rank)
    )
    if len(group) == 1:
        agreement = "no two agreed, so the first by vantage name is graded"
    else:
        agreement = f"the {len(group)} agreeing vantages are graded"
    disagreement = (
        f"{len(holders)} vantages returned {len(by_declaration)} different declarations this "
        f"run ({counts}); {agreement}"
    )
    return document, disagreement


def write_probes(path: Path, vantage: str, probes: dict[str, VantageProbe]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "vantage": vantage,
                "probes": {eid: asdict(p) for eid, p in sorted(probes.items())},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def probe_entry_failure(entry: dict[str, Any]) -> str | None:
    """Why this probe entry is not a measurement, or ``None`` when it is one.

    Two fields decide whether an entry can be read at all, and both used to be coerced.

    ``elapsed_ms`` was read as ``int(entry.get("elapsed_ms") or 0)``. An entry that carried
    no latency -- or carried ``null``, or a string -- therefore arrived as **0 ms**, and
    :func:`reconcile` puts every reachable probe's ``elapsed_ms`` into a median that
    :mod:`fhir_scorecard.grading` bands at 3000 ms and 8000 ms for R2. Zero is below every
    band, so a missing measurement was not merely wrong, it was the fastest possible
    reading, and it pulled the median down and the grade up. A latency nobody recorded is
    absence; published as ``0`` it is a measurement, and one that flatters the endpoint.

    ``reachable`` was read as ``bool(entry.get("reachable"))``. Every non-empty string is
    truthy in Python, so an entry carrying ``"reachable": "false"`` -- which is what a
    hand-written file, or a writer in a language where JSON booleans stringify, produces --
    was read as reachable.

    Neither has bitten yet: :func:`write_probes` serialises a dataclass, so every file this
    project has written carries a real boolean and a real integer. The path that makes it
    live is #100, where a vantage this project does not operate posts a probe file for the
    publishing run to admit. A file from a foreign writer is exactly the input these two
    coercions were waiting for, and it would arrive at a grade rather than at an error.

    So an entry that is not readable as a measurement is skipped, under the rule
    :func:`load_probe_files` already states: losing one vantage degrades the consensus, it
    does not abort the run. An endpoint that loses every vantage this way is *not observed*,
    which this project renders as itself and never as unreachable or as a zero.
    """
    reachable = entry.get("reachable")
    if not isinstance(reachable, bool):
        return (
            f"'reachable' is {reachable!r}, which is not true or false. Read as a "
            "truthiness test, any non-empty string here would count as reached"
        )
    elapsed = entry.get("elapsed_ms")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        return (
            f"'elapsed_ms' is {elapsed!r}, which is not a whole number of milliseconds. "
            "Coerced to 0 it would be the fastest reading this grader can record"
        )
    return None


def load_probe_files(paths: list[Path]) -> dict[str, list[VantageProbe]]:
    """Load several vantages' probe files, keyed by endpoint id.

    A malformed or empty file is skipped rather than aborting the merge: losing one vantage
    should degrade the consensus, not the run. The same rule applies one level down, to an
    entry inside an otherwise readable file -- see :func:`probe_entry_failure`. Each skip is
    printed, because a probe that vanished silently is indistinguishable from one that was
    never sent.
    """
    by_endpoint: dict[str, list[VantageProbe]] = {}
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        vantage = str(raw.get("vantage") or path.stem)
        probes = raw.get("probes")
        if not isinstance(probes, dict):
            continue
        for endpoint_id, entry in probes.items():
            if not isinstance(entry, dict):
                continue
            failure = probe_entry_failure(entry)
            if failure is not None:
                print(
                    f"vantage probe skipped: {path.name} {endpoint_id!r}: {failure}",
                    file=sys.stderr,
                )
                continue
            by_endpoint.setdefault(str(endpoint_id), []).append(
                VantageProbe(
                    vantage=str(entry.get("vantage") or vantage),
                    reachable=bool(entry.get("reachable")),
                    elapsed_ms=int(entry["elapsed_ms"]),
                    error=entry.get("error") if isinstance(entry.get("error"), str) else None,
                    capability=(
                        entry.get("capability")
                        if isinstance(entry.get("capability"), str)
                        else None
                    ),
                    smart=entry.get("smart") if isinstance(entry.get("smart"), str) else None,
                    # Read defensively: probe files written before this field existed simply
                    # do not carry it, and a vantage running an older revision is exactly the
                    # case this loader is built to tolerate.
                    status=(entry.get("status") if isinstance(entry.get("status"), int) else None),
                    # ``is True``, not truthiness. This field's whole job is to say that a
                    # request was actually made, so the only value that may assert it is a real
                    # JSON ``true``: a string, a number, or the field's absence all read as "this
                    # file does not say", which leaves the grade unpinned rather than pinning it
                    # on a claim nobody made. Same rule ``probe_entry_failure`` applies to
                    # ``reachable``, for the same reason.
                    smart_requested=entry.get("smart_requested") is True,
                    # A probe that reached the endpoint has no failure to classify, so it keeps
                    # ``None`` whatever the file says: a foreign writer that shipped both
                    # ``"reachable": true`` and a failure kind would otherwise put a reachable
                    # endpoint into a failure population. One that did not reach is normalised
                    # against the closed vocabulary -- an unknown label, a number, or nothing at
                    # all all read as ``unclassified``, which is what this run knows.
                    failure_kind=(
                        None
                        if entry.get("reachable") is True
                        else normalise_failure_kind(entry.get("failure_kind"))
                    ),
                )
            )
    return by_endpoint
