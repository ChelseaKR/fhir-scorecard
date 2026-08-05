"""Capability drift: remember what each endpoint declared, and surface changes between runs.

Drift is recorded and displayed, not scored (v0.1): a capability change is often a legitimate
upgrade, so the honest treatment is "here is what changed and when," not a penalty. The
fingerprint covers declared capability facts only, never volatile fields like generation
timestamps, so a server that merely re-renders its CapabilityStatement does not read as changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import CapabilityFacts

_MAX_EVENTS = 20

_FINGERPRINT_FIELDS = (
    "fhir_version",
    "software_name",
    "software_version",
    "resource_count",
    "resources_with_interactions",
    "declares_oauth_security",
)


@dataclass(frozen=True)
class DriftResult:
    first_seen: str | None
    changes: tuple[str, ...]
    recorded_events: tuple[str, ...]


def fingerprint(facts: CapabilityFacts) -> dict[str, object]:
    fp: dict[str, object] = {name: getattr(facts, name) for name in _FINGERPRINT_FIELDS}
    fp["supported_profiles"] = sorted(set(facts.supported_profiles))
    return fp


def load_history(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_history(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _diff(previous: dict[str, Any], current: dict[str, object]) -> list[str]:
    messages: list[str] = []
    for key in (*_FINGERPRINT_FIELDS, "supported_profiles"):
        before, after = previous.get(key), current.get(key)
        if before == after:
            continue
        if key == "supported_profiles":
            b = set(before) if isinstance(before, list) else set()
            a = set(after) if isinstance(after, list) else set()
            if a - b:
                messages.append(f"profiles added: {len(a - b)}")
            if b - a:
                messages.append(f"profiles removed: {len(b - a)}")
        else:
            messages.append(f"{key}: {before!r} -> {after!r}")
    return messages


def observe(history: dict[str, Any], endpoint_id: str, facts: CapabilityFacts,
            today: str) -> DriftResult:
    """Record today's observation, mutating ``history``; returns what changed since last run.

    Only parsed CapabilityStatements are observed: an outage must not read as the server's
    declared capability having changed.
    """
    entry_raw = history.get(endpoint_id)
    entry: dict[str, Any] = entry_raw if isinstance(entry_raw, dict) else {}
    events_raw = entry.get("events")
    events: list[dict[str, Any]] = events_raw if isinstance(events_raw, list) else []

    if not facts.parsed or not facts.resource_type_ok:
        first = entry.get("first_seen")
        return DriftResult(
            first_seen=first if isinstance(first, str) else None,
            changes=(),
            recorded_events=_event_lines(events),
        )

    current = fingerprint(facts)
    previous_raw = entry.get("fingerprint")
    previous: dict[str, Any] | None = previous_raw if isinstance(previous_raw, dict) else None

    changes: list[str] = [] if previous is None else _diff(previous, current)
    if changes:
        events.append({"date": today, "changes": changes})
        events = events[-_MAX_EVENTS:]

    first_seen_raw = entry.get("first_seen")
    first_seen = first_seen_raw if isinstance(first_seen_raw, str) else today
    history[endpoint_id] = {
        "first_seen": first_seen,
        "last_seen": today,
        "fingerprint": current,
        "events": events,
    }
    return DriftResult(first_seen=first_seen, changes=tuple(changes),
                       recorded_events=_event_lines(events))


def _event_lines(events: list[dict[str, Any]]) -> tuple[str, ...]:
    lines: list[str] = []
    for event in events:
        date = event.get("date")
        changes = event.get("changes")
        if isinstance(date, str) and isinstance(changes, list):
            lines.append(f"{date}: " + "; ".join(str(c) for c in changes))
    return tuple(lines)
