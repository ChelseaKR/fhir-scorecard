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
            f"CapabilityStatement present: fhirVersion {_safe(facts.fhir_version)}, "
            f"{facts.resource_count} resource types, "
            f"software {_safe(facts.software_name) or 'unstated'}"
        ),
    )


#: Longest remote-supplied string this report will quote.
_MAX_QUOTED = 120


def _safe(value: str | None) -> str:
    """A remote server's string, bounded and stripped of anything that could restructure a report.

    ``format_report``'s output is not only read by people. ``recheck.yml`` greps it for a marker
    and pastes it inside a fenced block in a GitHub issue, so a server controls text that reaches
    both a branch decision and rendered Markdown. A ``software.name`` containing the marker forces
    a false revival issue; one containing a newline and a fence escapes the block and injects
    arbitrary Markdown into this repository's issues. Neither is exotic - it is a free-text field
    on a third-party server this project deliberately does not trust.

    Control characters go, backticks go, and the value is capped. The workflow no longer branches
    on this prose either (it reads a structured field), so this bounds what gets *quoted* rather
    than being the only thing standing between a payer's string and a decision.
    """
    if value is None:
        return ""
    cleaned = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in value)
    cleaned = cleaned.replace("`", "'").strip()
    if len(cleaned) > _MAX_QUOTED:
        cleaned = cleaned[:_MAX_QUOTED] + "..."
    return cleaned


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
