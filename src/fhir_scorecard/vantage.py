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
from dataclasses import asdict, dataclass
from pathlib import Path


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
    capability: str | None = None   # raw /metadata body, when this vantage retrieved it
    smart: str | None = None        # raw SMART discovery body, when retrieved

    @property
    def network(self) -> str:
        """The network this vantage sits on, by the ``<network>/<host>`` label convention.

        ``github-actions/ubuntu-latest`` and ``github-actions/macos-latest`` are two hosts on one
        network; ``davis-ca/residential`` is a different one. A label with no separator is its
        own network, which is the conservative reading: it never merges two things into one.
        """
        return self.vantage.split("/", 1)[0] or self.vantage


@dataclass(frozen=True)
class Consensus:
    reachable: bool
    elapsed_ms: int
    vantages: int          # distinct vantages, after duplicate labels are collapsed
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
            collapsed.append(VantageProbe(
                vantage=vantage, reachable=True,
                elapsed_ms=_median([p.elapsed_ms for p in reached]), error=None,
                capability=next((p.capability for p in reached if p.capability), None),
                smart=next((p.smart for p in reached if p.smart), None)))
        else:
            errors = sorted({p.error for p in group if p.error})
            collapsed.append(VantageProbe(vantage=vantage, reachable=False, elapsed_ms=0,
                                          error="; ".join(errors) or None))
    return collapsed


def reconcile(raw_probes: list[VantageProbe]) -> Consensus:
    """Combine per-vantage probes. Reaching an endpoint from anywhere settles that it is up."""
    if not raw_probes:
        return Consensus(reachable=False, elapsed_ms=0, vantages=0, agreeing=0,
                         networks=0, detail="no vantage reported")

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
        if len(networks) == 1:
            detail = (f"not reached from any of the {len(probes)} vantages tried, all on one "
                      f"network ({networks[0]}), so this run cannot separate an endpoint that is "
                      f"down from one that does not answer this network: {joined}")
        else:
            detail = (f"not reached from any of the {len(probes)} vantages tried, across "
                      f"{len(networks)} networks: {joined}")
        return Consensus(reachable=False, elapsed_ms=0, vantages=len(probes), agreeing=0,
                         networks=len(networks), detail=detail)

    # Median latency across the vantages that succeeded: one slow network path should not
    # define the number, and neither should one unusually fast one.
    median = _median([p.elapsed_ms for p in reached])

    if failed:
        names = ", ".join(sorted(p.vantage for p in failed))
        why = "; ".join(sorted({p.error or "unknown" for p in failed}))
        detail = (f"reachable from {len(reached)} of {len(probes)} vantages; "
                  f"failed from {names} ({why}), which is a property of that network "
                  "rather than of the endpoint")
    elif len(probes) > 1 and len(networks) == 1:
        detail = (f"reachable from all {len(probes)} vantages, which are {len(probes)} hosts on "
                  f"one network ({networks[0]}): one network's view sampled {len(probes)} times, "
                  f"not {len(probes)} independent networks")
    elif len(probes) > 1:
        detail = (f"reachable from all {len(probes)} vantages across "
                  f"{len(networks)} networks")
    else:
        detail = f"reachable from {probes[0].vantage}"

    borrowed = next((p for p in reached if p.capability), None)
    return Consensus(reachable=True, elapsed_ms=median, vantages=len(probes),
                     agreeing=len(reached), networks=len(networks), detail=detail,
                     capability=borrowed.capability if borrowed else None,
                     smart=borrowed.smart if borrowed else None)


def write_probes(path: Path, vantage: str, probes: dict[str, VantageProbe]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "vantage": vantage,
        "probes": {eid: asdict(p) for eid, p in sorted(probes.items())},
    }, indent=2), encoding="utf-8")


def load_probe_files(paths: list[Path]) -> dict[str, list[VantageProbe]]:
    """Load several vantages' probe files, keyed by endpoint id.

    A malformed or empty file is skipped rather than aborting the merge: losing one vantage
    should degrade the consensus, not the run.
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
            by_endpoint.setdefault(str(endpoint_id), []).append(VantageProbe(
                vantage=str(entry.get("vantage") or vantage),
                reachable=bool(entry.get("reachable")),
                elapsed_ms=int(entry.get("elapsed_ms") or 0),
                error=entry.get("error") if isinstance(entry.get("error"), str) else None,
                capability=(entry.get("capability")
                            if isinstance(entry.get("capability"), str) else None),
                smart=entry.get("smart") if isinstance(entry.get("smart"), str) else None,
            ))
    return by_endpoint
