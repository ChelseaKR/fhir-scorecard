"""Capability drift: remember what each endpoint declared, and surface changes between runs.

Drift is recorded and displayed, not scored (v0.1): a capability change is often a legitimate
upgrade, so the honest treatment is "here is what changed and when," not a penalty. The
fingerprint covers declared capability facts only, never volatile fields like generation
timestamps, so a server that merely re-renders its CapabilityStatement does not read as changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import CapabilityFacts

_MAX_EVENTS = 20
# Availability is kept as a bounded rolling window of dated observations. 120 entries is roughly
# four months of daily runs, which is enough to say something about reliability without letting
# the history file grow without limit.
_MAX_OBSERVATIONS = 120
# Reporting availability off two data points would be noise dressed as a metric, so it stays
# informational until a run has this many observations behind it.
MIN_OBSERVATIONS_TO_REPORT = 14

_FINGERPRINT_FIELDS = (
    "fhir_version",
    "software_name",
    "software_version",
    "resource_count",
    "resources_with_interactions",
    "declares_oauth_security",
)


@dataclass(frozen=True)
class Availability:
    """Rolling reachability over the recorded observation window."""

    observations: int
    reachable: int

    @property
    def reportable(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS_TO_REPORT

    def summary(self) -> str:
        if not self.observations:
            return "no availability observations recorded yet"
        if not self.reportable:
            return (f"answered {self.reachable} of {self.observations} checks so far; "
                    f"availability is reported once {MIN_OBSERVATIONS_TO_REPORT} "
                    "observations exist")
        pct = round(100 * self.reachable / self.observations)
        return f"answered {self.reachable} of the last {self.observations} daily checks ({pct}%)"


@dataclass(frozen=True)
class DriftResult:
    first_seen: str | None
    changes: tuple[str, ...]
    recorded_events: tuple[str, ...]
    availability: Availability = Availability(0, 0)


def fingerprint(facts: CapabilityFacts) -> dict[str, object]:
    """Declared capability, reduced to comparable facts.

    Profiles are stored as a count plus a digest rather than the full list: Firely alone declares
    215 of them, and keeping every URL for every endpoint every day would grow the history file
    without bound while adding nothing drift detection needs.
    """
    fp: dict[str, object] = {name: getattr(facts, name) for name in _FINGERPRINT_FIELDS}
    profiles = sorted(set(facts.supported_profiles))
    fp["profile_count"] = len(profiles)
    fp["profile_digest"] = hashlib.sha256(
        "\n".join(profiles).encode("utf-8")).hexdigest()[:16]
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
    for key in _FINGERPRINT_FIELDS:
        before, after = previous.get(key), current.get(key)
        if before != after:
            messages.append(f"{key}: {before!r} -> {after!r}")

    before_count, after_count = previous.get("profile_count"), current.get("profile_count")
    if before_count != after_count:
        messages.append(f"declared profiles: {before_count} -> {after_count}")
    elif previous.get("profile_digest") != current.get("profile_digest"):
        # Same number of profiles, different set: a swap, which a count alone would hide.
        messages.append(f"declared profiles changed (still {after_count})")

    # Legacy rows stored the full profile list; migrate them silently rather than reporting a
    # spurious change on the first run after the format changed.
    if "supported_profiles" in previous and "profile_count" not in previous:
        legacy = previous.get("supported_profiles")
        legacy_count = len(legacy) if isinstance(legacy, list) else 0
        messages = [m for m in messages if not m.startswith("declared profiles")]
        if legacy_count != after_count:
            messages.append(f"declared profiles: {legacy_count} -> {after_count}")
    return messages


def _record_observation(entry: dict[str, Any], today: str, reachable: bool) -> Availability:
    """Append today's reachability, replacing any earlier entry for the same date so a re-run
    does not double-count a day."""
    raw = entry.get("observations")
    observations: list[dict[str, Any]] = raw if isinstance(raw, list) else []
    observations = [o for o in observations
                    if isinstance(o, dict) and o.get("date") != today]
    observations.append({"date": today, "up": reachable})
    observations = observations[-_MAX_OBSERVATIONS:]
    entry["observations"] = observations
    return Availability(observations=len(observations),
                        reachable=sum(1 for o in observations if o.get("up")))


def observe(history: dict[str, Any], endpoint_id: str, facts: CapabilityFacts,
            today: str, *, reachable: bool | None = None) -> DriftResult:
    """Record today's observation, mutating ``history``; returns what changed since last run.

    Only parsed CapabilityStatements are observed: an outage must not read as the server's
    declared capability having changed.
    """
    entry_raw = history.get(endpoint_id)
    entry: dict[str, Any] = entry_raw if isinstance(entry_raw, dict) else {}
    events_raw = entry.get("events")
    events: list[dict[str, Any]] = events_raw if isinstance(events_raw, list) else []

    # Reachability is recorded even when the CapabilityStatement is unusable: an endpoint that
    # answers with garbage is a different fact from one that does not answer, and availability
    # should reflect what actually happened rather than only the days parsing succeeded.
    is_up = facts.parsed and facts.resource_type_ok if reachable is None else reachable
    availability = _record_observation(entry, today, is_up)
    history[endpoint_id] = entry

    if not facts.parsed or not facts.resource_type_ok:
        first = entry.get("first_seen")
        entry.setdefault("first_seen", today)
        return DriftResult(
            first_seen=first if isinstance(first, str) else None,
            changes=(),
            recorded_events=_event_lines(events),
            availability=availability,
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
    entry.update({
        "first_seen": first_seen,
        "last_seen": today,
        "fingerprint": current,
        "events": events,
    })
    history[endpoint_id] = entry
    return DriftResult(first_seen=first_seen, changes=tuple(changes),
                       recorded_events=_event_lines(events), availability=availability)


def _event_lines(events: list[dict[str, Any]]) -> tuple[str, ...]:
    lines: list[str] = []
    for event in events:
        date = event.get("date")
        changes = event.get("changes")
        if isinstance(date, str) and isinstance(changes, list):
            lines.append(f"{date}: " + "; ".join(str(c) for c in changes))
    return tuple(lines)
