"""Re-probe previously rejected candidates: a 404 today may be a live endpoint next quarter.

Rejections are data, not dead ends. Payers stand up endpoints, migrate hosts, and lift gateway
rules; a candidate log that is never revisited quietly becomes wrong. This module re-checks
recorded rejections and reports which ones now answer, without touching the registry: promotion
into the registry stays a human decision, because verification means confirming the publisher is
who the entry claims, and that is a judgement a fetch cannot make.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fhir_scorecard.capability import parse_capability
from fhir_scorecard.fetch import fetch_json


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str
    base_url: str
    last_outcome: str


@dataclass(frozen=True)
class ReprobeResult:
    candidate: Candidate
    now_answers: bool
    detail: str


def load_candidates(path: Path) -> list[Candidate]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("rejected"), list):
        raise ValueError("candidates file must be an object with a 'rejected' list")
    out: list[Candidate] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["rejected"]):
        if not isinstance(item, dict):
            raise ValueError(f"rejected[{i}] is not an object")
        cid = str(item.get("id") or "").strip()
        base = str(item.get("base_url") or "").strip()
        if not cid or not base:
            raise ValueError(f"rejected[{i}] needs id and base_url")
        if cid in seen:
            raise ValueError(f"duplicate candidate id {cid!r}")
        seen.add(cid)
        out.append(
            Candidate(
                candidate_id=cid,
                name=str(item.get("name") or cid),
                base_url=base.rstrip("/"),
                last_outcome=str(item.get("last_outcome") or "unknown"),
            )
        )
    return out


def reprobe(candidate: Candidate) -> ReprobeResult:
    """Fetch one candidate's CapabilityStatement. Never mutates the registry."""
    result = fetch_json(f"{candidate.base_url}/metadata", timeout=20)
    if not result.ok:
        detail = result.error or f"HTTP {result.status}"
        return ReprobeResult(candidate=candidate, now_answers=False, detail=detail)
    facts = parse_capability(result.body)
    if not facts.parsed or not facts.resource_type_ok:
        return ReprobeResult(
            candidate=candidate,
            now_answers=False,
            detail=f"answered, but not a CapabilityStatement: {facts.parse_error}",
        )
    return ReprobeResult(
        candidate=candidate,
        now_answers=True,
        detail=(
            f"CapabilityStatement present: fhirVersion {facts.fhir_version}, "
            f"{facts.resource_count} resource types, "
            f"software {facts.software_name or 'unstated'}"
        ),
    )


def format_report(results: list[ReprobeResult]) -> str:
    """Human-readable summary. Newly-answering candidates need publisher confirmation before
    they can enter the registry, and the report says so rather than implying promotion."""
    answering = [r for r in results if r.now_answers]
    lines = [
        f"re-probed {len(results)} previously rejected candidates; {len(answering)} now answer"
    ]
    for r in results:
        mark = "NOW ANSWERS" if r.now_answers else "still rejected"
        lines.append(f"  [{mark}] {r.candidate.candidate_id}: {r.detail}")
    if answering:
        lines.append("")
        lines.append(
            "Confirm the publisher matches the claimed organization before adding any "
            "of these to the registry; answering is not the same as verified."
        )
    return "\n".join(lines)
