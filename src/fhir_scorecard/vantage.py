"""Reconcile probe results from several network vantage points into one honest reachability fact.

A single probing network is an unreliable narrator. On 2026-08-05 a live payer endpoint was
recorded as dead because a middlebox on the probing network intercepted TLS; the endpoint was
fine and the network was not. Probing from more than one place and reconciling the results is
the fix, and it is the difference between "this endpoint is down" and "I could not get there
from here".

The reconciliation rule is deliberately asymmetric. **One vantage reaching an endpoint proves it
is reachable; one vantage failing proves nothing.** Unreachability is a claim about the world and
needs agreement across vantages; reachability is a demonstrated fact and needs only one witness.
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


@dataclass(frozen=True)
class Consensus:
    reachable: bool
    elapsed_ms: int
    vantages: int
    agreeing: int
    detail: str
    # Documents from whichever vantage retrieved them, so content can be graded even when the
    # local vantage was blocked.
    capability: str | None = None
    smart: str | None = None

    @property
    def unanimous(self) -> bool:
        return self.vantages > 0 and self.agreeing == self.vantages


def reconcile(probes: list[VantageProbe]) -> Consensus:
    """Combine per-vantage probes. Reaching an endpoint from anywhere settles that it is up."""
    if not probes:
        return Consensus(reachable=False, elapsed_ms=0, vantages=0, agreeing=0,
                         detail="no vantage reported")

    reached = [p for p in probes if p.reachable]
    failed = [p for p in probes if not p.reachable]

    if not reached:
        # Every vantage failed. Only now is "unreachable" a claim about the endpoint, and even
        # then the failure modes are worth showing: identical errors everywhere read very
        # differently from a scattered mix.
        joined = "; ".join(sorted({p.error or "unknown" for p in failed}))
        return Consensus(reachable=False, elapsed_ms=0, vantages=len(probes), agreeing=0,
                         detail=f"unreachable from all {len(probes)} vantage(s): {joined}")

    # Median latency across the vantages that succeeded: one slow network path should not
    # define the number, and neither should one unusually fast one.
    times = sorted(p.elapsed_ms for p in reached)
    median = times[len(times) // 2] if len(times) % 2 else (
        (times[len(times) // 2 - 1] + times[len(times) // 2]) // 2)

    if failed:
        names = ", ".join(sorted(p.vantage for p in failed))
        why = "; ".join(sorted({p.error or "unknown" for p in failed}))
        detail = (f"reachable from {len(reached)} of {len(probes)} vantages; "
                  f"failed from {names} ({why}), which is a property of that network "
                  "rather than of the endpoint")
    else:
        detail = (f"reachable from all {len(probes)} vantage(s)" if len(probes) > 1
                  else f"reachable from {probes[0].vantage}")

    borrowed = next((p for p in reached if p.capability), None)
    return Consensus(reachable=True, elapsed_ms=median, vantages=len(probes),
                     agreeing=len(reached), detail=detail,
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
