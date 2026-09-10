"""The feeds have to say what the record says, and say it the same way twice.

Every test here is written against one of the four properties the module docstring states,
because those are the properties a subscriber's client depends on and none of them is visible
by looking at a feed once:

* an entry carries the event's recorded date and no build stamp;
* an entry id is a function of the tuple and not of the address it was rendered for;
* a feed with no entries is a file that says why;
* the record holds dates, so no entry states a time of day.

The fixtures are :class:`fhir_scorecard.archive.Record` values rather than a built site
wherever the property is about the rendering, and a built site wherever it is about the
wiring, because the wiring is where a feed can be written correctly and advertised nowhere.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from xml.etree import ElementTree

import pytest

from fhir_scorecard.archive import Change, Observation, Record, Return
from fhir_scorecard.cli import main
from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.feeds import (
    EVENT_KINDS,
    ID_AUTHORITY,
    SITE_FEED_MAX_ENTRIES,
    Event,
    build_feeds,
    events_for,
    feed_for_record,
    record_as_of,
    render,
)
from fhir_scorecard.site import DEFAULT_ORIGIN

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ATOM = "http://www.w3.org/2005/Atom"


def _atom(name: str) -> str:
    return f"{{{ATOM}}}{name}"


def _record(
    endpoint_id: str = "alpha",
    *,
    name: str = "Alpha Patient Access API",
    observations: tuple[tuple[str, bool], ...] = (),
    first_seen: str | None = None,
    last_seen: str | None = None,
    changes: tuple[Change, ...] = (),
    returns: tuple[Return, ...] = (),
) -> Record:
    return Record(
        endpoint_id=endpoint_id,
        name=name,
        kind="payer",
        observations=tuple(Observation(date=d, up=up) for d, up in observations),
        first_seen=first_seen,
        last_seen=last_seen,
        changes=changes,
        returns=returns,
    )


def _busy_record() -> Record:
    """One record holding every kind of event, so a count can be checked against a known sum."""
    return _record(
        observations=(
            ("2026-08-01", True),
            ("2026-08-02", False),  # transition 1
            ("2026-08-03", False),
            ("2026-08-04", True),  # transition 2
        ),
        first_seen="2026-08-01",
        last_seen="2026-08-04",
        changes=(
            Change(date="2026-08-02", changes=("software_version: '1.0' -> '1.1'",)),
            Change(date="2026-08-03", changes=("resource_count: 20 -> 21",)),
        ),
        returns=(
            Return(
                first_return="2026-08-04",
                last_return="2026-08-04",
                times=1,
                state_first_seen="2026-08-01",
                changes=("software_version: '1.1' -> '1.0'",),
            ),
        ),
    )


# --- the vocabulary, and the floor under it ---


def test_every_kind_a_record_can_produce_is_a_documented_kind() -> None:
    """A kind outside ``EVENT_KINDS`` cannot ship, the same rule ``audit.FINDING_CODES`` keeps."""
    produced = {event.kind for event in events_for(_busy_record())}
    assert produced == set(EVENT_KINDS), produced.symmetric_difference(EVENT_KINDS)


def test_an_undocumented_kind_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown feed event kind"):
        Event(
            kind="something-new",
            date="2026-08-01",
            endpoint_id="alpha",
            endpoint_name="Alpha",
            title="t",
            summary="s",
        )


# --- what a feed carries ---


def test_the_entry_count_equals_the_recorded_events() -> None:
    """1 entering the record, 2 declaration changes, 1 return, 2 availability transitions."""
    events = events_for(_busy_record())
    by_kind = {kind: sum(1 for e in events if e.kind == kind) for kind in EVENT_KINDS}
    assert by_kind == {
        "entered-record": 1,
        "declaration-change": 2,
        "declaration-return": 1,
        "availability-change": 2,
    }
    assert len(events) == 6


def test_a_return_renders_as_a_return_and_not_as_a_change() -> None:
    feed = feed_for_record(_busy_record(), "2026-08-04")
    document = ElementTree.fromstring(render(feed, DEFAULT_ORIGIN))
    returns = [
        entry
        for entry in document.findall(_atom("entry"))
        if entry.find(_atom("category")).get("term") == "declaration-return"  # type: ignore[union-attr]
    ]
    assert len(returns) == 1
    title = returns[0].findtext(_atom("title"), "")
    assert "returned to a declaration already on record" in title
    summary = returns[0].findtext(_atom("summary"), "")
    assert "is not a change the publisher made" in summary


def test_the_first_observation_is_not_published_as_a_transition() -> None:
    """There is no earlier state for it to have moved from, so calling it one invents a change."""
    events = events_for(_record(observations=(("2026-08-01", False),), first_seen="2026-08-01"))
    assert [event.kind for event in events] == ["entered-record"]


def test_an_endpoint_with_no_observations_gets_a_feed_that_says_why() -> None:
    """Zero entries and a stated reason, not a missing file: a 404 and "nothing has happened"
    are different facts."""
    feed = feed_for_record(_record(), "2026-08-04")
    assert feed.entries == ()
    assert "No observation is recorded for this endpoint yet" in feed.subtitle
    document = ElementTree.fromstring(render(feed, DEFAULT_ORIGIN))
    assert document.findall(_atom("entry")) == []
    assert document.find(_atom("subtitle")) is not None
    assert document.findtext(_atom("updated")) == "2026-08-04T00:00:00Z"


def test_an_endpoint_that_has_only_ever_answered_says_that_rather_than_the_other_reason() -> None:
    """Two different empty cases, and the reason is read off the record rather than assumed.

    This one does hold entries - entering the record is an event - so the assertion is on the
    reason a caller would get if it did not, which is what ``_no_entry_reason`` decides.
    """
    quiet = _record(observations=(("2026-08-01", True), ("2026-08-02", True)))
    feed = feed_for_record(quiet, "2026-08-02")
    assert feed.entries == ()
    assert "holds 2 observations for this endpoint and no event" in feed.subtitle


# --- the two numbers, and the cap ---


def test_a_feed_states_how_many_events_it_carries_and_how_many_there_are() -> None:
    feed = feed_for_record(_busy_record(), "2026-08-04")
    assert "It carries 6 of 6 recorded events, covering 2026-08-01 to 2026-08-04." in feed.subtitle


def test_the_site_feed_states_its_cap_rather_than_truncating_silently() -> None:
    """A truncated dataset published as a complete one is the defect this project is against."""
    records = [
        _record(
            f"endpoint-{index:03d}",
            name=f"Endpoint {index}",
            observations=(("2026-08-01", True),),
            first_seen="2026-08-01",
            changes=(Change(date="2026-08-02", changes=(f"software_version: '{index}'",)),),
        )
        for index in range(SITE_FEED_MAX_ENTRIES)
    ]
    site = build_feeds(records)[0]
    available = 2 * SITE_FEED_MAX_ENTRIES
    assert len(site.entries) == SITE_FEED_MAX_ENTRIES
    assert f"It carries {SITE_FEED_MAX_ENTRIES} of {available} recorded events" in site.subtitle
    assert "covering 2026-08-02" in site.subtitle


def test_a_per_endpoint_feed_is_not_capped() -> None:
    changes = tuple(
        Change(date=f"2026-08-{day:02d}", changes=(f"software_version: '{day}'",))
        for day in range(1, 29)
    )
    feed = feed_for_record(_record(first_seen="2026-08-01", changes=changes), "2026-08-28")
    assert len(feed.entries) == len(changes) + 1 > SITE_FEED_MAX_ENTRIES // 4


# --- dates ---


def test_no_entry_states_a_time_of_day() -> None:
    """The record holds dates. ``T00:00:00Z`` is Atom's required encoding, not an observation."""
    document = ElementTree.fromstring(
        render(feed_for_record(_busy_record(), "2026-08-04"), DEFAULT_ORIGIN)
    )
    stamps = [element.text for element in document.iter() if element.tag.endswith("}updated")]
    stamps += [element.text for element in document.iter() if element.tag.endswith("}published")]
    assert stamps
    assert all(stamp is not None and stamp.endswith("T00:00:00Z") for stamp in stamps)


def test_an_entry_carries_the_events_recorded_date_and_not_the_newest_one() -> None:
    document = ElementTree.fromstring(
        render(feed_for_record(_busy_record(), "2026-08-04"), DEFAULT_ORIGIN)
    )
    dated = {
        entry.findtext(_atom("title"), ""): entry.findtext(_atom("updated"), "")
        for entry in document.findall(_atom("entry"))
    }
    assert dated["Alpha Patient Access API entered the observation record"] == (
        "2026-08-01T00:00:00Z"
    )


def test_record_as_of_reads_every_dated_part_of_the_record() -> None:
    assert record_as_of([]) is None
    assert record_as_of([_record()]) is None
    assert record_as_of([_record(first_seen="2026-08-01")]) == "2026-08-01"
    assert record_as_of([_busy_record()]) == "2026-08-04"


def test_a_record_with_no_date_anywhere_produces_no_feed_at_all() -> None:
    """Absent rather than empty, the rule ``cli._coverage_page`` already keeps: a feed dated
    from the clock would put the build's own timestamp where a reader expects an observation."""
    assert build_feeds([_record()]) == ()
    assert build_feeds([]) == ()


# --- ids ---


def test_an_entry_id_is_the_tuple_the_issue_specifies() -> None:
    event = events_for(_record(first_seen="2026-08-01"))[0]
    assert event.entry_id == (f"tag:{ID_AUTHORITY}:alpha/entered-record/2026-08-01/{event.digest}")


def test_two_builds_of_one_record_are_byte_identical() -> None:
    feed = feed_for_record(_busy_record(), "2026-08-04")
    again = feed_for_record(_busy_record(), "2026-08-04")
    assert render(feed, DEFAULT_ORIGIN) == render(again, DEFAULT_ORIGIN)


def test_an_id_does_not_move_when_the_site_moves() -> None:
    """An id derived from the origin would republish the whole history on a hosting change,
    which is the exact failure a stable id exists to prevent."""
    record = _busy_record()
    here = {event.entry_id for event in events_for(record)}
    rendered = render(feed_for_record(record, "2026-08-04"), "https://example.test/preview")
    assert all(entry_id in rendered for entry_id in here)


def test_an_id_does_not_move_when_the_registry_tidies_a_name() -> None:
    """The name is presentation. Rewriting one must not republish that endpoint's whole
    history to every subscriber."""
    original = {event.entry_id for event in events_for(_busy_record())}
    renamed = _busy_record()
    renamed = Record(
        endpoint_id=renamed.endpoint_id,
        name="Alpha Health",
        kind=renamed.kind,
        observations=renamed.observations,
        first_seen=renamed.first_seen,
        last_seen=renamed.last_seen,
        changes=renamed.changes,
        returns=renamed.returns,
    )
    assert {event.entry_id for event in events_for(renamed)} == original


def test_an_id_does_move_when_the_recorded_fact_changes() -> None:
    """The other direction, or the id would be stable by being blind."""
    one = events_for(
        _record(changes=(Change(date="2026-08-02", changes=("software_version: '1.0'",)),))
    )
    two = events_for(
        _record(changes=(Change(date="2026-08-02", changes=("software_version: '1.1'",)),))
    )
    assert one[0].entry_id != two[0].entry_id


# --- what a third party can put in our feed ---


def test_a_declaration_a_payer_wrote_cannot_break_the_feed() -> None:
    """``software_name`` and ``software_version`` come from a third party's document. A feed
    that stopped parsing because of what a payer typed would be an outage in the notification
    channel, caused by the thing it exists to report."""
    hostile = Change(
        date="2026-08-02",
        changes=("software_name: 'A\x0bB\ud800C' -> 'D<E&F￾G'",),
    )
    feed = feed_for_record(_record(changes=(hostile,)), "2026-08-02")
    rendered = render(feed, DEFAULT_ORIGIN)
    document = ElementTree.fromstring(rendered)
    summary = document.findall(_atom("entry"))[0].findtext(_atom("summary"), "")
    assert "D<E&F" in summary
    assert "\x0b" not in rendered
    assert "\ud800" not in rendered
    assert "￾" not in rendered


# --- cohorts ---


def _cohort(*member_endpoints: tuple[str, tuple[str, ...]]) -> Cohort:
    return Cohort(
        cohort_id="fixture-cohort",
        name="Fixture Cohort",
        description="A fixture.",
        notes=(),
        sources=(),
        members=tuple(
            CohortMember(member_id=mid, name=mid, programs=(), endpoint_ids=eids)
            for mid, eids in member_endpoints
        ),
    )


def test_two_members_sharing_one_endpoint_yield_one_set_of_entries() -> None:
    """Measured in the shipped curation: ``florida-marketplace`` records Cigna and Florida Blue
    under two member organizations each, both pointing at one published surface, and
    ``michigan-marketplace`` does the same for BCBS Michigan's provider directory. A row per
    member is right on the cohort page, because the row is about the plan. An entry is about
    the event, and reporting one declaration change twice would be counting members."""
    record = _busy_record()
    cohort = _cohort(("plan-a", ("alpha",)), ("plan-b", ("alpha",)))
    feeds = {feed.page_path: feed for feed in build_feeds([record], (cohort,))}
    entries = feeds["fixture-cohort"].entries
    assert len(entries) == len(events_for(record))
    assert len({entry.entry_id for entry in entries}) == len(entries)
    assert "for the 1 distinct listed endpoints" in feeds["fixture-cohort"].subtitle


def test_a_cohort_feed_carries_only_its_own_members() -> None:
    inside = _busy_record()
    outside = _record("beta", name="Beta", first_seen="2026-08-01")
    cohort = _cohort(("plan-a", ("alpha",)))
    feeds = {feed.page_path: feed for feed in build_feeds([inside, outside], (cohort,))}
    assert {entry.endpoint_id for entry in feeds["fixture-cohort"].entries} == {"alpha"}
    assert {entry.endpoint_id for entry in feeds[""].entries} == {"alpha", "beta"}


# --- the wiring: a feed written and advertised nowhere is a feed nobody finds ---


def _build(out: Path, tmp_path: Path, *, cohorts: Path | None = None) -> Path:
    argv = [
        "grade",
        "--offline",
        "--fixtures",
        str(FIXTURES),
        "--registry",
        str(FIXTURES / "registry.json"),
        "--out",
        str(out),
        "--history",
        str(tmp_path / f"{out.name}-history.json"),
    ]
    if cohorts is not None:
        argv += ["--cohorts", str(cohorts)]
    assert main(argv) == 0
    return out


@pytest.fixture
def built(tmp_path: Path) -> Path:
    return _build(tmp_path / "site", tmp_path)


def test_the_build_writes_a_site_feed_and_one_per_endpoint(built: Path) -> None:
    registry = json.loads((FIXTURES / "registry.json").read_text(encoding="utf-8"))
    expected = {"feed.xml"} | {
        f"endpoint/{entry['id']}/feed.xml" for entry in registry["endpoints"]
    }
    written = {path.relative_to(built).as_posix() for path in built.rglob("feed.xml")}
    assert written == expected


def test_the_sitemap_lists_every_feed_the_build_wrote(built: Path) -> None:
    sitemap = (built / "sitemap.xml").read_text(encoding="utf-8")
    for path in sorted(built.rglob("feed.xml")):
        loc = f"{DEFAULT_ORIGIN}/{path.relative_to(built).as_posix()}"
        assert f"<loc>{loc}</loc>" in sitemap, loc


def test_the_pages_point_feed_autodiscovery_at_a_file_that_exists(built: Path) -> None:
    home = (built / "index.html").read_text(encoding="utf-8")
    assert '<link rel="alternate" type="application/atom+xml"' in home
    assert 'href="/feed.xml"' in home
    endpoint = (built / "endpoint" / "cms-blue-button-2" / "index.html").read_text(encoding="utf-8")
    assert 'href="/endpoint/cms-blue-button-2/feed.xml"' in endpoint
    # The record page is where every entry in that feed links, so it advertises the same feed.
    record = (built / "history" / "cms-blue-button-2" / "index.html").read_text(encoding="utf-8")
    assert 'href="/endpoint/cms-blue-button-2/feed.xml"' in record


def test_api_index_names_the_feeds_and_only_the_feeds_that_exist(built: Path) -> None:
    index = json.loads((built / "api" / "index.json").read_text(encoding="utf-8"))
    assert index["feed"] == f"{DEFAULT_ORIGIN}/feed.xml"
    for entry in index["endpoints"]:
        relative = entry["feed"].removeprefix(f"{DEFAULT_ORIGIN}/")
        assert (built / relative).is_file(), entry["feed"]


def test_a_build_with_cohorts_writes_a_feed_per_cohort(tmp_path: Path) -> None:
    cohorts = tmp_path / "cohorts"
    cohorts.mkdir()
    (cohorts / "fixture-cohort.json").write_text(
        json.dumps(
            {
                "cohort": {
                    "id": "fixture-cohort",
                    "name": "Fixture Cohort",
                    "description": "Endpoints of the offline fixture registry.",
                },
                "members": [
                    {
                        "id": "fixture-plan",
                        "name": "Fixture Plan",
                        "programs": ["covered-ca"],
                        "endpoints": ["cms-blue-button-2"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    built = _build(tmp_path / "site", tmp_path, cohorts=cohorts)
    feed = built / "fixture-cohort" / "feed.xml"
    assert feed.is_file()
    page = (built / "fixture-cohort" / "index.html").read_text(encoding="utf-8")
    assert 'href="/fixture-cohort/feed.xml"' in page


def test_the_feeds_carry_no_build_stamp_although_the_pages_do(tmp_path: Path) -> None:
    """Two builds of one record. The pages may legitimately differ - they state when they were
    generated - and every feed byte must not, because a build stamp inside a feed republishes
    the whole history to every subscriber on every rebuild."""
    history = tmp_path / "history.json"
    first = _build(tmp_path / "one", tmp_path)
    shutil.copy(tmp_path / "one-history.json", history)
    shutil.copy(history, tmp_path / "two-history.json")
    second = _build(tmp_path / "two", tmp_path)

    feeds = sorted(path.relative_to(first).as_posix() for path in first.rglob("feed.xml"))
    assert feeds
    for relative in feeds:
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), relative

    home = (first / "index.html").read_text(encoding="utf-8")
    stamp = home.split("Generated ", 1)[1].split(".", 1)[0]
    assert "UTC" in stamp
    for relative in feeds:
        assert stamp not in (first / relative).read_text(encoding="utf-8"), relative
