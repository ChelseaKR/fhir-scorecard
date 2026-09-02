"""Capability drift: remember what each endpoint declared, and surface changes between runs.

Drift is recorded and displayed, not scored (v0.1): a capability change is often a legitimate
upgrade, so the honest treatment is "here is what changed and when," not a penalty. The
fingerprint covers declared capability facts only, never volatile fields like generation
timestamps, so a server that merely re-renders its CapabilityStatement does not read as changed.

**A return is not a change.** One hostname can sit in front of more than one backend, and then a
daily probe lands on whichever answers. Measured on ``la-care-provider-directory``: eight events
between 2026-08-08 and 2026-08-16, every one of them ``software_version`` moving between
``5.4.1.13_edfx`` and ``5.4.1.11_edfx`` and back. The first of those said something real — this
address serves two different declarations. The seven after it said the same thing again, and each
one published "L.A. Care changed its declared capability" on a page people read. At eight events
out of a twenty-event window, roughly twelve more would have evicted every genuine capability
change this endpoint ever makes.

So the log distinguishes two things a naive diff cannot. *Advancing* to a declaration never seen
before is an event, which is what keeps ``medplum``'s thirteen real forward releases (5.1.27 →
5.1.30) reading exactly as they did. *Returning* to one already on record is an alternation: it is
counted, dated, and reported once, and it never appends another event. See
:func:`_apply_alternation_rule` for what happens to a log written before this rule existed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard.capability import CapabilityFacts

_MAX_EVENTS = 20
# Declarations remembered per endpoint, for deciding whether the one in hand is a return. Bounded
# for the same reason the event log is: this file is committed on every run. An endpoint that
# rotates through more distinct declarations than this will eventually see the oldest fall out and
# be recorded as an advance when it comes back, which is the honest failure mode - the record no
# longer holds the evidence that it is a return.
_MAX_REMEMBERED_STATES = 24
# Availability is kept as a bounded rolling window of dated observations. 120 entries is roughly
# four months of daily runs, which is enough to say something about reliability without letting
# the history file grow without limit.
_MAX_OBSERVATIONS = 120
# Reporting availability off two data points would be noise dressed as a metric, so it stays
# informational until a run has this many observations behind it.
MIN_OBSERVATIONS_TO_REPORT = 14

#: Written into an alternation record in place of a date the history could not supply.
#:
#: :func:`_apply_alternation_rule` rebuilds an old log under the current rule, and an event whose
#: date the record never held cannot be given one. The sentinel exists so the file says which
#: dates are absent instead of leaving a key out, but it is *not* a date and must never be
#: rendered as one: "unknown to unknown: returned 3 times" reads as a measurement, and it is the
#: absence of one. :func:`undated` is the only way anything should read these fields, and
#: ``archive`` imports it so the writer and the readers cannot disagree about the spelling.
UNDATED = "unknown"


def undated(value: Any) -> str | None:
    """The date in ``value``, or ``None`` where the record does not hold one.

    ``None``, a non-string, and the :data:`UNDATED` sentinel are the same fact - this record
    cannot say when - and they have to read the same, because a caller that checked only
    ``isinstance(value, str)`` accepted the sentinel and published it as a date.
    """
    return value if isinstance(value, str) and value != UNDATED else None


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
            return (
                f"answered {self.reachable} of {self.observations} checks so far; "
                f"availability is reported once {MIN_OBSERVATIONS_TO_REPORT} "
                "observations exist"
            )
        pct = round(100 * self.reachable / self.observations)
        return f"answered {self.reachable} of the last {self.observations} daily checks ({pct}%)"


@dataclass(frozen=True)
class DriftResult:
    first_seen: str | None
    changes: tuple[str, ...]
    recorded_events: tuple[str, ...]
    availability: Availability = Availability(0, 0)
    #: Declarations this endpoint has returned to, one line each, with how many times and when.
    #: Empty for the ordinary endpoint that only ever moves forward.
    alternations: tuple[str, ...] = ()
    #: True when *this run's* difference was a return to a declaration already on record. The
    #: run still updates the stored fingerprint, so the next diff compares against what was
    #: actually served; it just does not call the return a change.
    returned: bool = False


def fingerprint(facts: CapabilityFacts) -> dict[str, object]:
    """Declared capability, reduced to comparable facts.

    Profiles are stored as a count plus a digest rather than the full list: Firely alone declares
    215 of them, and keeping every URL for every endpoint every day would grow the history file
    without bound while adding nothing drift detection needs.
    """
    fp: dict[str, object] = {name: getattr(facts, name) for name in _FINGERPRINT_FIELDS}
    profiles = sorted(set(facts.supported_profiles))
    fp["profile_count"] = len(profiles)
    fp["profile_digest"] = hashlib.sha256("\n".join(profiles).encode("utf-8")).hexdigest()[:16]
    return fp


#: Where the history file records which kind of run wrote it. Endpoint ids can never collide with
#: it: the registry's id pattern requires a leading letter or digit.
META_KEY = "_meta"


def ensure_mode(history: dict[str, Any], *, offline: bool) -> None:
    """Refuse to mix fixture observations and live ones in one history file.

    Running an offline build against the real ``data/history.json`` writes a ``false`` observation
    for every endpoint in the registry, on a date that had none, and availability then reports a
    day nobody measured. The file records which kind of run wrote it, and a run of the other kind
    raises rather than appending. The fix on the reader's side is a scratch path, which
    ``--offline`` now chooses by default; there is deliberately no flag to override this, because
    an override is the thing that gets passed in a hurry.
    """
    mode = "offline" if offline else "live"
    meta = history.get(META_KEY)
    recorded = meta.get("mode") if isinstance(meta, dict) else None
    if isinstance(recorded, str) and recorded != mode:
        raise ValueError(
            f"this history file was written by a {recorded} run and this is a {mode} run; "
            f"point --history at a scratch path rather than mixing {recorded} and {mode} "
            "observations in one availability record"
        )
    history[META_KEY] = {"mode": mode}


def endpoint_count(history: dict[str, Any]) -> int:
    """How many endpoints a history file describes, ignoring bookkeeping keys."""
    return sum(1 for key in history if not key.startswith("_"))


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


def state_digest(fp: dict[str, Any]) -> str:
    """A stable short digest of one declared-capability fingerprint.

    Identity, not similarity: two runs that produce byte-identical fingerprints produce the same
    digest, and anything else produces a different one. That is the whole test for "have we seen
    this declaration before".
    """
    canonical = json.dumps(fp, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _states(entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw = entry.get("states")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _remember(states: list[dict[str, Any]], digest: str, when: str) -> list[dict[str, Any]]:
    """Record that ``digest`` was in force from ``when``, keeping the newest bounded set."""
    kept = [s for s in states if s.get("digest") != digest]
    kept.append({"digest": digest, "first_seen": when})
    return kept[-_MAX_REMEMBERED_STATES:]


def _first_seen_of(states: list[dict[str, Any]], digest: str) -> str | None:
    for state in states:
        if state.get("digest") == digest:
            value = state.get("first_seen")
            return value if isinstance(value, str) else None
    return None


def _record_return(
    alternations: list[dict[str, Any]],
    digest: str,
    state_first_seen: str,
    today: str,
    changes: list[str],
) -> list[dict[str, Any]]:
    """Count one return to ``digest``, rather than appending another event for it."""
    for record in alternations:
        if record.get("digest") == digest:
            record["returns"] = int(record.get("returns", 0)) + 1
            record["last_return"] = today
            record["changes"] = changes
            return alternations
    alternations.append(
        {
            "digest": digest,
            "state_first_seen": state_first_seen,
            "first_return": today,
            "last_return": today,
            "returns": 1,
            "changes": changes,
        }
    )
    return alternations[-_MAX_REMEMBERED_STATES:]


_CHANGE_RE = re.compile(r"^(?P<key>[a-z_]+): (?P<before>.+) -> (?P<after>.+)$")


def _invert(state: dict[str, Any], changes: Any) -> dict[str, Any] | None:
    """The fingerprint that was in force *before* an event, or None if it cannot be re-derived.

    Every event line for a fingerprint field carries both sides of the transition, so the earlier
    state is recoverable exactly rather than guessed. Two lines are deliberately not recoverable:
    ``declared profiles: N -> M`` and ``declared profiles changed (still N)`` record a count or a
    fact of change, never the digest, so any event carrying one stops the walk. None means "stop",
    never "assume".
    """
    if not isinstance(changes, list) or not changes:
        return None
    older = dict(state)
    for line in changes:
        match = _CHANGE_RE.match(line) if isinstance(line, str) else None
        if match is None or match["key"] not in _FINGERPRINT_FIELDS:
            return None
        try:
            before = ast.literal_eval(match["before"])
            after = ast.literal_eval(match["after"])
        except (ValueError, SyntaxError):
            return None
        # The event must actually describe the transition into the state in hand. If it does not,
        # the log and the fingerprint disagree and re-deriving anything from them would be fiction.
        if older.get(match["key"]) != after:
            return None
        older[match["key"]] = before
    return older


def _replayable(entry: dict[str, Any], current: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """Walk the recorded events backwards from ``current``, re-deriving each earlier fingerprint.

    Returns the index of the oldest replayable event and the fingerprints in force from that point
    on, oldest first, so ``states[k]`` is the declaration ``events[cut + k]`` moved *away* from.
    """
    raw = entry.get("events")
    events = raw if isinstance(raw, list) else []
    backwards = [current]
    cut = len(events)
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        older = _invert(backwards[-1], event.get("changes")) if isinstance(event, dict) else None
        if older is None:
            break
        backwards.append(older)
        cut = i
    return cut, list(reversed(backwards))


def _apply_alternation_rule(entry: dict[str, Any], current: dict[str, Any] | None) -> None:
    """Bring a history entry written before the alternation rule under it, once.

    The presence of ``states`` is the marker, so this runs on an entry's first observation after
    the upgrade and never again. Nothing is invented: every event line carries both sides of its
    transition, so the earlier fingerprints are re-derived from the record and the log is rebuilt
    under the rule that is in force now. An event whose earlier state cannot be re-derived stops
    the walk, and it and everything older than it are left exactly as they were recorded.

    Measured on the shipped history at 2026-08-19: ``la-care-provider-directory`` 8 events -> 1
    event plus 7 counted returns; ``medplum`` 13 events -> 13 events, 0 returns, because every one
    of its versions is a declaration nothing had served before.
    """
    if "states" in entry:
        return
    if not isinstance(current, dict):
        # Nothing has been fingerprinted yet, so there is nothing to remember and nothing to
        # rebuild. The first observation seeds this.
        entry["states"] = []
        return

    raw = entry.get("events")
    events = raw if isinstance(raw, list) else []
    cut, derived = _replayable(entry, current)
    oldest_first_seen = entry.get("first_seen")
    if cut > 0 or not isinstance(oldest_first_seen, str):
        # The walk stopped early, so the oldest re-derived state came from the event just before
        # the cut rather than from the endpoint's first observation.
        previous_event = events[cut - 1] if cut > 0 else None
        candidate = previous_event.get("date") if isinstance(previous_event, dict) else None
        oldest_first_seen = candidate if isinstance(candidate, str) else UNDATED

    states = _remember([], state_digest(derived[0]), oldest_first_seen)
    rebuilt = list(events[:cut])
    alternations: list[dict[str, Any]] = []
    for offset, event in enumerate(events[cut:]):
        after = derived[offset + 1]
        digest = state_digest(after)
        date = event.get("date") if isinstance(event, dict) else None
        date = date if isinstance(date, str) else UNDATED
        known = _first_seen_of(states, digest)
        if known is not None:
            changes = event.get("changes") if isinstance(event, dict) else None
            alternations = _record_return(
                alternations, digest, known, date, changes if isinstance(changes, list) else []
            )
        else:
            rebuilt.append(event)
            states = _remember(states, digest, date)

    entry["states"] = states
    if alternations:
        entry["alternations"] = alternations
        # The rebuild is itself on the record: a reader comparing this file to an older commit of
        # it should be able to see that events were collapsed, and by how many.
        entry["alternation_rebuild"] = {"events_before": len(events), "events_after": len(rebuilt)}
    entry["events"] = rebuilt[-_MAX_EVENTS:]


def _alternation_lines(entry: dict[str, Any]) -> tuple[str, ...]:
    """One published sentence per alternation record, and none of them invents a date.

    These lines are rendered verbatim on the endpoint page (``site.py``) and in the report, so a
    missing date reached readers twice over: ``record.get('first_return')`` formats a missing key
    as the literal ``None``, and :data:`UNDATED` formats as the word ``unknown``. Both read as
    dates. A record whose window this history does not hold is **dropped**, which is what
    :func:`archive._returns` does with the same record and what an undated event has always got:
    a return is a thing that happened on some days, and a row that cannot say which is not the
    shorter version of that sentence. ``state_first_seen`` is a detail rather than the window, so
    its absence is said in words and the return is still published.
    """
    raw = entry.get("alternations")
    if not isinstance(raw, list):
        return ()
    lines: list[str] = []
    for record in raw:
        if not isinstance(record, dict):
            continue
        first, last = undated(record.get("first_return")), undated(record.get("last_return"))
        if first is None or last is None:
            continue
        returns = int(record.get("returns", 0) or 0)
        changes = record.get("changes")
        detail = "; ".join(str(c) for c in changes) if isinstance(changes, list) and changes else ""
        window = first if first == last else f"{first} to {last}"
        times = "once" if returns == 1 else f"{returns} times"
        state_first_seen = undated(record.get("state_first_seen"))
        origin = (
            f"first observed {state_first_seen}"
            if state_first_seen is not None
            else "whose first sighting this record does not date"
        )
        lines.append(
            f"{window}: returned {times} to a declaration {origin}"
            + (f" ({detail})" if detail else "")
            + " - counted, not recorded as a new change each time"
        )
    return tuple(lines)


def _record_observation(entry: dict[str, Any], today: str, reachable: bool) -> Availability:
    """Append today's reachability, replacing any earlier entry for the same date so a re-run
    does not double-count a day."""
    raw = entry.get("observations")
    observations: list[dict[str, Any]] = raw if isinstance(raw, list) else []
    observations = [o for o in observations if isinstance(o, dict) and o.get("date") != today]
    observations.append({"date": today, "up": reachable})
    observations = observations[-_MAX_OBSERVATIONS:]
    entry["observations"] = observations
    return Availability(
        observations=len(observations), reachable=sum(1 for o in observations if o.get("up"))
    )


def observe(
    history: dict[str, Any],
    endpoint_id: str,
    facts: CapabilityFacts,
    today: str,
    *,
    reachable: bool | None = None,
) -> DriftResult:
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
            alternations=_alternation_lines(entry),
        )

    current = fingerprint(facts)
    previous_raw = entry.get("fingerprint")
    previous: dict[str, Any] | None = previous_raw if isinstance(previous_raw, dict) else None

    # Before this run's difference is judged, the entry has to know which declarations are already
    # on record. A log written before the rule existed is rebuilt under it here, once.
    _apply_alternation_rule(entry, previous)
    events_raw = entry.get("events")
    events = events_raw if isinstance(events_raw, list) else []
    states = _states(entry)

    changes: list[str] = [] if previous is None else _diff(previous, current)
    returned = False
    if previous is None:
        states = _remember(states, state_digest(current), today)
    elif changes:
        digest = state_digest(current)
        state_first_seen = _first_seen_of(states, digest)
        if state_first_seen is not None:
            # Already served, already reported. Counting the return keeps the fact that this
            # address serves more than one declaration, without republishing it as news.
            returned = True
            alternations_raw = entry.get("alternations")
            entry["alternations"] = _record_return(
                alternations_raw if isinstance(alternations_raw, list) else [],
                digest,
                state_first_seen,
                today,
                changes,
            )
        else:
            events.append({"date": today, "changes": changes})
            events = events[-_MAX_EVENTS:]
            states = _remember(states, digest, today)
    entry["states"] = states

    first_seen_raw = entry.get("first_seen")
    first_seen = first_seen_raw if isinstance(first_seen_raw, str) else today
    entry.update(
        {
            "first_seen": first_seen,
            "last_seen": today,
            "fingerprint": current,
            "events": events,
        }
    )
    history[endpoint_id] = entry
    return DriftResult(
        first_seen=first_seen,
        changes=tuple(changes),
        recorded_events=_event_lines(events),
        availability=availability,
        alternations=_alternation_lines(entry),
        returned=returned,
    )


def _event_lines(events: list[dict[str, Any]]) -> tuple[str, ...]:
    lines: list[str] = []
    for event in events:
        date = event.get("date")
        changes = event.get("changes")
        if isinstance(date, str) and isinstance(changes, list):
            lines.append(f"{date}: " + "; ".join(str(c) for c in changes))
    return tuple(lines)
