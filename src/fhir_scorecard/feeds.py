"""Atom feeds over the observation record, so a change can be learned without visiting.

The record already holds a declaration timeline and an availability history; until now the
only way to find out that a payer changed what it declares was to open the page. This renders
the same events as Atom 1.0: ``/feed.xml`` site-wide, ``/endpoint/<id>/feed.xml`` per endpoint,
and ``/<cohort>/feed.xml`` per cohort. No accounts, no email, no service - a file rebuilt from
committed data, which is the only notification channel a static site can offer honestly.

Every rule the pages keep holds here, and four of them are load-bearing enough to state.

**An entry carries the event's recorded date, never the build's.** A feed regenerated daily
from an unchanged record must be the same feed; a build stamp in an entry would republish the
whole history to every subscriber every night. Nothing in this module reads a clock: the feed
is a pure function of the records and the origin, which is what makes
``test_two_builds_of_one_record_are_byte_identical`` a real assertion rather than a tautology.

**An entry id is derived from the tuple, not from the address.** ``(endpoint_id, kind, date,
digest)`` - and deliberately not from the origin the build was rendered for. An id that moved
when the site moved would look to every reader like a fresh set of events, which is the exact
failure a stable id exists to prevent. The digest covers the event's own content, so a *changed*
record does produce a new entry, which is correct: the fact changed.

**A feed with no entries is a file that says why.** A 404 and "nothing has happened" are
different facts, and the second is the one the record can support. :func:`feed_for_record`
writes the reason from what the record actually holds - no observation at all, or observations
with no recorded event - rather than from an assumption about which of those it is.

**The record holds dates, not instants.** Atom's ``updated`` is a date-time construct, so each
date is encoded as midnight UTC. That is an encoding, not an observation, and every feed says
so in its subtitle rather than letting a reader take ``T00:00:00Z`` for a time of day.

One thing the issue asked for that the record cannot support, stated here because the absence
is the finding rather than an omission: it asked for a transition into or out of "not observed"
**with the vantages named**. An observation in ``history.json`` is ``{"date", "up"}`` and has
been for every one of the entries the live record holds - the reconciled reachability and
nothing else. Naming the vantages that reported would mean inventing them, so an availability
entry says what the record says and states that the vantages are not retained.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from fhir_scorecard.archive import ARCHIVE_PATH, Record
from fhir_scorecard.cohort import Cohort
from fhir_scorecard.site import ATOM_MEDIA_TYPE

#: Filename every feed is written under, inside the directory of the page it belongs to.
FEED_FILENAME = "feed.xml"

#: Newest entries the site-wide feed carries. Per-endpoint and per-cohort feeds are unbounded:
#: they are the record of one subject and a reader who subscribed to one payer asked for all of
#: it. The site feed is every subject at once, so it is capped - and it states the cap and the
#: window it covers, because a truncated dataset published as a complete one is the defect this
#: project is organised against.
SITE_FEED_MAX_ENTRIES = 100

#: The tagging entity of every entry id, per RFC 4151. Fixed, and deliberately not read from the
#: build's ``--origin``: see the module docstring. The date is the year the authority was held
#: for this purpose, which is what a ``tag:`` URI's date component means.
ID_AUTHORITY = "fhir.chelseakr.com,2026"

#: Every event kind a feed can carry, with the sentence that says what it is. A kind outside
#: this map cannot be rendered - ``tests/test_feeds.py`` asserts the two agree - so a new kind
#: cannot ship without a documented name, the same rule ``audit.FINDING_CODES`` keeps.
EVENT_KINDS: dict[str, str] = {
    "entered-record": "an endpoint was first observed and entered the record",
    "declaration-change": "an endpoint changed what its CapabilityStatement declares",
    "declaration-return": (
        "an endpoint went back to a declaration already on record, counted rather than "
        "republished as a change"
    ),
    "availability-change": (
        "the record's reconciled observation moved between answered and did not answer"
    ),
}

#: What the feed says about the encoding of a date, on every feed, once.
_DATE_NOTE = (
    "The record holds dates, not times of day; Atom requires an instant, so each date is "
    "encoded as midnight UTC and no entry states an observed time."
)

#: What an availability entry can and cannot say. The record retains reconciled reachability
#: and never the vantages behind it, so this sentence is a statement about the record.
_VANTAGE_NOTE = (
    "The record retains the reconciled observation only, not the vantages that reported it, "
    "so no entry here names one."
)


def _xml(value: str) -> str:
    """``value`` as XML character data, with anything XML 1.0 cannot carry replaced.

    Two of the fields rendered here originate in a third party's document - ``software_name``
    and ``software_version`` reach an entry through ``drift.fingerprint_changes`` - so a
    published feed's well-formedness must not depend on what a payer put in its
    CapabilityStatement. ``repr`` already escapes the C0 range on the way in; this is the floor
    under that, and it covers the ranges ``repr`` does not (lone surrogates arriving through a
    JSON ``\\ud800`` escape, and the two permanently-unassigned noncharacters).
    """
    cleaned = "".join(
        character
        if character in "\t\n\r" or 0x20 <= ord(character) <= 0xD7FF or ord(character) >= 0xE000
        else "�"
        for character in value
        if not 0xFFFE <= ord(character) <= 0xFFFF
    )
    return html.escape(cleaned, quote=True)


def _instant(date: str) -> str:
    """A recorded date as the RFC 3339 instant Atom requires. See :data:`_DATE_NOTE`."""
    return f"{date}T00:00:00Z"


@dataclass(frozen=True)
class Event:
    """One thing the record says happened, on the date the record says it happened.

    ``digest`` is over the event's own content, so two events that differ only in their detail
    get different ids and a subscriber sees the corrected one. ``detail`` is what that digest
    is taken over and is never rendered directly; :attr:`summary` is the sentence a reader gets.
    """

    kind: str
    date: str
    endpoint_id: str
    endpoint_name: str
    title: str
    summary: str
    detail: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown feed event kind {self.kind!r}")

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {
                "kind": self.kind,
                "date": self.date,
                "endpoint_id": self.endpoint_id,
                "detail": list(self.detail),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def entry_id(self) -> str:
        """The tuple the issue specifies, spelled as an RFC 4151 ``tag:`` URI."""
        return f"tag:{ID_AUTHORITY}:{self.endpoint_id}/{self.kind}/{self.date}/{self.digest}"

    def sort_key(self) -> tuple[str, str, str, str]:
        """Newest first, then a total order so two builds cannot disagree about ties."""
        return (self.date, self.endpoint_id, self.kind, self.digest)


def _entered_record(record: Record) -> list[Event]:
    if record.first_seen is None:
        return []
    return [
        Event(
            kind="entered-record",
            date=record.first_seen,
            endpoint_id=record.endpoint_id,
            endpoint_name=record.name,
            title=f"{record.name} entered the observation record",
            summary=(
                f"First observed on {record.first_seen}. Nothing before that date is recorded "
                "for this endpoint, here or anywhere else in this project."
            ),
        )
    ]


def _declaration_changes(record: Record) -> list[Event]:
    return [
        Event(
            kind="declaration-change",
            date=change.date,
            endpoint_id=record.endpoint_id,
            endpoint_name=record.name,
            title=f"{record.name} changed what it declares",
            summary=(
                f"On {change.date} the declared capability facts moved: "
                + "; ".join(change.changes)
                + ". Recorded, not scored: a capability change is often a legitimate upgrade."
            ),
            detail=change.changes,
        )
        for change in record.changes
    ]


def _declaration_returns(record: Record) -> list[Event]:
    """One entry per recorded return, dated by the most recent one.

    A return is counted rather than repeated - ``drift._record_return`` merges every return to
    one declaration into a single record - so the entry moves to the newest date and its digest
    covers the count. A subscriber sees one updated item, not one item per bounce, which is the
    same rule the timeline page keeps.
    """
    events = []
    for item in record.returns:
        origin = (
            f"first seen {item.state_first_seen}"
            if item.state_first_seen is not None
            else "whose first sighting this record does not date"
        )
        detail = (item.window, str(item.times), origin, *item.changes)
        events.append(
            Event(
                kind="declaration-return",
                date=item.last_return,
                endpoint_id=record.endpoint_id,
                endpoint_name=record.name,
                title=f"{record.name} returned to a declaration already on record",
                summary=(
                    f"Over {item.window} this endpoint returned {item.times} "
                    f"{'time' if item.times == 1 else 'times'} to a declaration {origin}. "
                    "One address can sit in front of more than one backend, so this is counted "
                    "as a return and is not a change the publisher made."
                ),
                detail=detail,
            )
        )
    return events


def _availability_changes(record: Record) -> list[Event]:
    """One entry per transition between answered and did not answer, dated by the later day.

    The first observation is not a transition: there is no earlier state for it to have moved
    from, and dressing it as one would publish a change nobody observed. It is covered by the
    endpoint entering the record.
    """
    events = []
    ordered = sorted(record.observations, key=lambda observation: observation.date)
    for previous, current in pairwise(ordered):
        if previous.up == current.up:
            continue
        moved = "answered" if current.up else "did not answer"
        was = "answered" if previous.up else "did not answer"
        events.append(
            Event(
                kind="availability-change",
                date=current.date,
                endpoint_id=record.endpoint_id,
                endpoint_name=record.name,
                title=(
                    f"{record.name} answered again"
                    if current.up
                    else f"{record.name} stopped answering"
                ),
                summary=(
                    f"The observation recorded on {current.date} {moved}; the one before it, on "
                    f"{previous.date}, {was}. {_VANTAGE_NOTE}"
                ),
                detail=(previous.date, was, current.date, moved),
            )
        )
    return events


def events_for(record: Record) -> tuple[Event, ...]:
    """Every event one record holds, newest first.

    The four kinds are read from four different parts of the record and never merged: a return
    is not a change, and an availability transition is not a declaration. Their counts add up to
    what the record holds, which is what ``test_the_entry_count_equals_the_recorded_events``
    pins.
    """
    events = (
        _entered_record(record)
        + _declaration_changes(record)
        + _declaration_returns(record)
        + _availability_changes(record)
    )
    return tuple(sorted(events, key=Event.sort_key, reverse=True))


def record_as_of(records: list[Record]) -> str | None:
    """The newest date anywhere in the record, or ``None`` if it holds no date at all.

    This is what a feed with no entries is dated by, and it is the one date such a feed can
    honestly carry: not the build's clock, but when the record it is a view of was last written.
    ``None`` means the record has nothing dated in it, and :func:`build_feeds` writes no feeds
    at all rather than dating one from a clock - the same rule ``cli._coverage_page`` keeps for a
    coverage page with no frame behind it.
    """
    dates: set[str] = set()
    for record in records:
        dates.update(observation.date for observation in record.observations)
        dates.update(change.date for change in record.changes)
        dates.update(item.last_return for item in record.returns)
        dates.update(date for date in (record.first_seen, record.last_seen) if date)
    return max(dates) if dates else None


@dataclass(frozen=True)
class Feed:
    """One rendered feed, and where it belongs.

    ``page_path`` is the site-relative directory of the page this feed is the alternate of, and
    is also the directory the file is written into, so a feed and the page it describes cannot
    drift apart into different places.
    """

    page_path: str
    feed_id: str
    title: str
    #: The subtitle carries every statement this feed makes about itself: the window, the two
    #: counts, and the encoding notes. Assembled by :func:`_subtitle` rather than by a caller.
    subtitle: str
    updated: str
    entries: tuple[Event, ...]

    @property
    def file_path(self) -> str:
        """Site-relative path of the file, which is what the sitemap and the audit name."""
        return f"{self.page_path}/{FEED_FILENAME}" if self.page_path else FEED_FILENAME


def _window_sentence(entries: tuple[Event, ...], available: int) -> str:
    """The two numbers, always both, and the dates they span.

    ``N of M`` rather than ``N``: a feed that examined a sliver of the record reads exactly like
    one that carried all of it, and the cap on the site feed makes that a live possibility
    rather than a hypothetical.
    """
    if not entries:
        return "It carries 0 of 0 recorded events."
    oldest = min(entry.date for entry in entries)
    newest = max(entry.date for entry in entries)
    span = oldest if oldest == newest else f"{oldest} to {newest}"
    return f"It carries {len(entries)} of {available} recorded events, covering {span}."


def _subtitle(what: str, entries: tuple[Event, ...], available: int, reason: str) -> str:
    parts = [what, _window_sentence(entries, available)]
    if not entries:
        parts.append(reason)
    parts.append(_DATE_NOTE)
    return " ".join(parts)


def _feed(
    *,
    page_path: str,
    feed_key: str,
    title: str,
    what: str,
    reason: str,
    events: tuple[Event, ...],
    limit: int | None,
    as_of: str,
) -> Feed:
    entries = events[:limit] if limit is not None else events
    return Feed(
        page_path=page_path,
        feed_id=f"tag:{ID_AUTHORITY}:feed/{feed_key}",
        title=title,
        subtitle=_subtitle(what, entries, len(events), reason),
        updated=max(entry.date for entry in entries) if entries else as_of,
        entries=entries,
    )


def _no_entry_reason(record: Record) -> str:
    """Why this endpoint's feed is empty, read off the record rather than assumed."""
    if not record.observed:
        return (
            "No observation is recorded for this endpoint yet, so there is nothing to report: "
            "it entered the registry more recently than the record, or no run has reached it."
        )
    return (
        f"The record holds {record.observed} "
        f"{'observation' if record.observed == 1 else 'observations'} for this endpoint and no "
        "event: it has answered consistently and has not changed what it declares."
    )


def feed_for_record(record: Record, as_of: str) -> Feed:
    """One endpoint's feed. Unbounded: a reader who subscribed to one payer asked for all of it."""
    return _feed(
        page_path=f"endpoint/{record.endpoint_id}",
        feed_key=f"endpoint/{record.endpoint_id}",
        title=f"{record.name}: recorded changes",
        what=(
            f"Every event the observation record holds for {record.name}, from the same "
            "history.json the pages are built from."
        ),
        reason=_no_entry_reason(record),
        events=events_for(record),
        limit=None,
        as_of=as_of,
    )


def build_feeds(records: list[Record], cohorts: tuple[Cohort, ...] = ()) -> tuple[Feed, ...]:
    """Every feed this build should write, or nothing at all if the record holds no date.

    Nothing rather than empty, for the reason :func:`record_as_of` gives: a feed has to carry an
    ``updated``, and the only honest source for one is the record. A build whose record holds no
    date has nothing to date a feed from, and dating it from the clock would put the build's own
    timestamp where a reader expects an observation.
    """
    as_of = record_as_of(records)
    if as_of is None:
        return ()
    per_record = {record.endpoint_id: events_for(record) for record in records}
    everything = tuple(
        sorted(
            (event for events in per_record.values() for event in events),
            key=Event.sort_key,
            reverse=True,
        )
    )
    feeds = [
        _feed(
            page_path="",
            feed_key="site",
            title="FHIR Scorecard: recorded changes",
            what=(
                "Every event the observation record holds, across every endpoint this project "
                "watches."
            ),
            reason=(
                "The record holds no event for any endpoint yet, which is a statement about the "
                "record and not about the endpoints."
            ),
            events=everything,
            limit=SITE_FEED_MAX_ENTRIES,
            as_of=as_of,
        )
    ]
    feeds.extend(feed_for_record(record, as_of) for record in records)
    for cohort in cohorts:
        # Deduplicated, and the reason is in the curation data rather than in defensiveness:
        # two member organizations can point at one published surface, which is what
        # `florida-marketplace` records for Cigna and Florida Blue and `michigan-marketplace`
        # for BCBS Michigan. The cohort page lists a row per member, correctly, because the
        # row is about the plan. A feed entry is about the event, and one declaration change
        # reported twice because two plans share a server would be counting members.
        endpoint_ids = list(
            dict.fromkeys(
                endpoint_id
                for member in cohort.included
                for endpoint_id in member.endpoint_ids
                if endpoint_id in per_record
            )
        )
        events = tuple(
            sorted(
                (event for endpoint_id in endpoint_ids for event in per_record[endpoint_id]),
                key=Event.sort_key,
                reverse=True,
            )
        )
        feeds.append(
            _feed(
                page_path=cohort.cohort_id,
                feed_key=f"cohort/{cohort.cohort_id}",
                title=f"{cohort.name}: recorded changes",
                what=(
                    f"Every event the observation record holds for the {len(endpoint_ids)} "
                    f"distinct listed endpoints of {cohort.name}."
                ),
                reason=(
                    "The record holds no event for any listed endpoint of this cohort. Members "
                    "reviewed and not listed have no endpoint to observe and cannot appear here."
                ),
                events=events,
                limit=None,
                as_of=as_of,
            )
        )
    return tuple(feeds)


def _entry_xml(event: Event, origin: str) -> str:
    page = f"{origin}/{ARCHIVE_PATH}/{event.endpoint_id}/"
    record = f"{origin}/api/{ARCHIVE_PATH}/{event.endpoint_id}.json"
    instant = _instant(event.date)
    return f"""  <entry>
    <id>{_xml(event.entry_id)}</id>
    <title type="text">{_xml(event.title)}</title>
    <updated>{instant}</updated>
    <published>{instant}</published>
    <category scheme="{_xml(f"tag:{ID_AUTHORITY}:kind")}" term="{_xml(event.kind)}"/>
    <link rel="alternate" type="text/html" href="{_xml(page)}"/>
    <link rel="via" type="application/json" href="{_xml(record)}"/>
    <summary type="text">{_xml(event.summary)}</summary>
  </entry>
"""


def render(feed: Feed, origin: str) -> str:
    """One feed as an Atom 1.0 document.

    A function of ``feed`` and ``origin`` and of nothing else - no clock, no filesystem - which
    is what makes byte-identity across two builds of one record a property rather than a hope.
    """
    origin = origin.rstrip("/")
    page = f"{origin}/{feed.page_path}/" if feed.page_path else f"{origin}/"
    entries = "".join(_entry_xml(event, origin) for event in feed.entries)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>{_xml(feed.feed_id)}</id>
  <title type="text">{_xml(feed.title)}</title>
  <subtitle type="text">{_xml(feed.subtitle)}</subtitle>
  <updated>{_instant(feed.updated)}</updated>
  <author><name>FHIR Scorecard</name><uri>{_xml(origin + "/")}</uri></author>
  <link rel="self" type="{ATOM_MEDIA_TYPE}" href="{_xml(f"{origin}/{feed.file_path}")}"/>
  <link rel="alternate" type="text/html" href="{_xml(page)}"/>
  <generator uri="https://github.com/ChelseaKR/fhir-scorecard">FHIR Scorecard</generator>
{entries}</feed>
"""


def write_feeds(out: Path, feeds: tuple[Feed, ...], origin: str) -> tuple[str, ...]:
    """Write every feed under ``out``; returns the site-relative paths actually written.

    The return value is what tells the sitemap, the pages' alternate links and
    ``api/index.json`` which feeds exist. None of them may name a feed from an assumption that
    one was written: a link to a feed the build did not write is the same defect as a sitemap
    entry no file answers, and this project already has a finding code for that one.
    """
    written = []
    for feed in feeds:
        target = out / feed.page_path if feed.page_path else out
        target.mkdir(parents=True, exist_ok=True)
        (target / FEED_FILENAME).write_text(render(feed, origin), encoding="utf-8")
        written.append(feed.file_path)
    return tuple(written)
