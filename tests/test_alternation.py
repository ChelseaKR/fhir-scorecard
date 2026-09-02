"""A return to a declaration already on record is not a new capability change.

The measurement this file is built from, taken off the published history on 2026-08-19:
``la-care-provider-directory`` carried 8 of the 27 recorded drift events, every one of them
``software_version`` moving between ``5.4.1.13_edfx`` and ``5.4.1.11_edfx``, on 08-08 (twice),
08-11, 08-12, 08-13, 08-14, 08-15 and 08-16. One hostname, two backends. ``medplum`` carried 13,
every one a version the server had never served before, 5.1.27 through 5.1.30.

Both real logs are reproduced here verbatim, because the rule has to tell them apart and the only
convincing evidence that it does is the data that motivated it.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.dataset import write_dataset
from fhir_scorecard.drift import UNDATED, fingerprint, observe, state_digest
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import Scorecard, build_scorecard
from fhir_scorecard.registry import Endpoint
from fhir_scorecard.report import render_html
from fhir_scorecard.site import endpoint_page

# The eight events recorded for la-care-provider-directory, copied from data published on the
# capability-history branch. Two values, alternating, for nine days.
LA_CARE_EVENTS: tuple[dict[str, Any], ...] = (
    {"date": "2026-08-08", "changes": ["software_version: '5.4.1.13_edfx' -> '5.4.1.11_edfx'"]},
    {"date": "2026-08-08", "changes": ["software_version: '5.4.1.11_edfx' -> '5.4.1.13_edfx'"]},
    {"date": "2026-08-11", "changes": ["software_version: '5.4.1.13_edfx' -> '5.4.1.11_edfx'"]},
    {"date": "2026-08-12", "changes": ["software_version: '5.4.1.11_edfx' -> '5.4.1.13_edfx'"]},
    {"date": "2026-08-13", "changes": ["software_version: '5.4.1.13_edfx' -> '5.4.1.11_edfx'"]},
    {"date": "2026-08-14", "changes": ["software_version: '5.4.1.11_edfx' -> '5.4.1.13_edfx'"]},
    {"date": "2026-08-15", "changes": ["software_version: '5.4.1.13_edfx' -> '5.4.1.11_edfx'"]},
    {"date": "2026-08-16", "changes": ["software_version: '5.4.1.11_edfx' -> '5.4.1.13_edfx'"]},
)

# The thirteen recorded for medplum, in order. Every value is new; nothing ever comes back.
MEDPLUM_VERSIONS: tuple[str, ...] = (
    "5.1.27-042f741",
    "5.1.28-9e40e9c",
    "5.1.28-cc10540",
    "5.1.28-3ac46ff",
    "5.1.28-6ad2fe2",
    "5.1.29-f5d2946",
    "5.1.29-c6ef164",
    "5.1.29-296dc48",
    "5.1.29-ca6e7b2",
    "5.1.29-ab373c5",
    "5.1.30-25d8de2",
    "5.1.30-d98cee7",
    "5.1.30-6daa238",
    "5.1.30-76fa156",
)


def _facts(name: str, version: str) -> Any:
    doc = good_capability()
    doc["software"] = {"name": name, "version": version}
    return parse_capability(json.dumps(doc).encode())


def _legacy_entry(events: tuple[dict[str, Any], ...], name: str, version: str) -> dict[str, Any]:
    """A history entry in the shape written before the alternation rule existed."""
    return {
        "first_seen": events[0]["date"],
        "last_seen": events[-1]["date"],
        "fingerprint": fingerprint(_facts(name, version)),
        "events": [dict(e) for e in events],
        "observations": [],
    }


def _events(history: dict[str, Any], endpoint_id: str) -> list[dict[str, Any]]:
    return list(history[endpoint_id]["events"])


def _returns(history: dict[str, Any], endpoint_id: str) -> int:
    return sum(int(a["returns"]) for a in history[endpoint_id].get("alternations", []))


def test_a_return_to_a_seen_declaration_is_counted_once_not_logged_each_cycle() -> None:
    history: dict[str, Any] = {}
    observe(history, "flapper", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-09-01")
    for day, version in enumerate(["5.4.1.11_edfx", "5.4.1.13_edfx"] * 6, start=2):
        observe(history, "flapper", _facts("HAPI FHIR Server", version), f"2026-09-{day:02d}")

    # Twelve alternations after the first move. One event: the day this address was first seen
    # serving something other than what it had been serving.
    assert len(_events(history, "flapper")) == 1
    assert _events(history, "flapper")[0]["date"] == "2026-09-02"
    assert _returns(history, "flapper") == 11


def test_the_run_that_lands_on_a_return_says_so_and_publishes_no_new_change() -> None:
    history: dict[str, Any] = {}
    observe(history, "flapper", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-09-01")
    forward = observe(history, "flapper", _facts("HAPI FHIR Server", "5.4.1.11_edfx"), "2026-09-02")
    back = observe(history, "flapper", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-09-03")

    assert not forward.returned
    assert forward.alternations == ()
    assert back.returned
    assert len(back.recorded_events) == 1, "the return must not have appended a second event"
    assert len(back.alternations) == 1
    assert "returned once" in back.alternations[0]
    assert "5.4.1.13_edfx" in back.alternations[0]
    # The stored fingerprint still follows what the server actually served, so the next diff is
    # taken against reality rather than against the last thing anyone called a change.
    assert history["flapper"]["fingerprint"]["software_version"] == "5.4.1.13_edfx"


def test_a_monotonic_release_sequence_keeps_every_event() -> None:
    """medplum's real version sequence. Thirteen forward releases, thirteen events, no returns."""
    history: dict[str, Any] = {}
    for day, version in enumerate(MEDPLUM_VERSIONS, start=1):
        observe(history, "medplum", _facts("medplum", version), f"2026-09-{day:02d}")

    assert len(_events(history, "medplum")) == len(MEDPLUM_VERSIONS) - 1 == 13
    assert "alternations" not in history["medplum"]


def test_a_log_written_before_the_rule_is_rebuilt_from_its_own_record() -> None:
    """la-care's eight recorded events collapse to one event and seven counted returns.

    Nothing is invented to do it: every event line carries both sides of its transition, so the
    earlier fingerprints are re-derived from the log rather than assumed.
    """
    history: dict[str, Any] = {
        "la-care": _legacy_entry(LA_CARE_EVENTS, "HAPI FHIR Server", "5.4.1.13_edfx")
    }
    result = observe(history, "la-care", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-08-19")

    assert len(_events(history, "la-care")) == 1
    assert _events(history, "la-care")[0]["date"] == "2026-08-08"
    assert _returns(history, "la-care") == 7
    assert history["la-care"]["alternation_rebuild"] == {"events_before": 8, "events_after": 1}
    assert result.changes == (), "the rebuild is not itself a capability change"
    assert len(result.alternations) == 2
    assert all("counted, not recorded as a new change each time" in a for a in result.alternations)


def test_the_rebuild_leaves_a_forward_only_log_exactly_as_it_found_it() -> None:
    events = tuple(
        {
            "date": f"2026-08-{day:02d}",
            "changes": [f"software_version: {before!r} -> {after!r}"],
        }
        for day, (before, after) in enumerate(pairwise(MEDPLUM_VERSIONS), start=5)
    )
    history: dict[str, Any] = {"medplum": _legacy_entry(events, "medplum", MEDPLUM_VERSIONS[-1])}
    before_rebuild = _events(history, "medplum")
    observe(history, "medplum", _facts("medplum", MEDPLUM_VERSIONS[-1]), "2026-08-19")

    assert _events(history, "medplum") == before_rebuild
    assert "alternations" not in history["medplum"]
    assert "alternation_rebuild" not in history["medplum"]


def test_the_rebuild_stops_at_an_event_it_cannot_re_derive_rather_than_guessing() -> None:
    """A profile event records a count, never the digest, so the state before it is unrecoverable.

    Everything older than that event is left exactly as recorded. Rebuilding across it would mean
    inventing a fingerprint and then deciding, from the invention, whether something was a return.
    """
    events = (
        {"date": "2026-08-01", "changes": ["software_version: 'a' -> 'b'"]},
        {"date": "2026-08-02", "changes": ["declared profiles: 4 -> 6"]},
        *LA_CARE_EVENTS,
    )
    history: dict[str, Any] = {"mixed": _legacy_entry(events, "HAPI FHIR Server", "5.4.1.13_edfx")}
    observe(history, "mixed", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-08-19")

    kept = _events(history, "mixed")
    # The two unrecoverable events survive untouched; the eight replayable ones collapse to one.
    assert [e["date"] for e in kept] == ["2026-08-01", "2026-08-02", "2026-08-08"]
    assert _returns(history, "mixed") == 7


def test_alternations_no_longer_evict_the_real_changes_from_the_window() -> None:
    """The failure this rule exists to stop: a flapping value pushing real releases out.

    Under the old rule a return appended an event, so twenty returns emptied the twenty-event
    window of everything else the endpoint had ever done.
    """
    history: dict[str, Any] = {}
    observe(history, "mixed", _facts("HAPI", "1.0.0"), "2026-09-01")
    observe(history, "mixed", _facts("HAPI", "2.0.0"), "2026-09-02")  # a real release
    for day in range(3, 33):
        version = "2.0.0" if day % 2 else "1.0.0"
        observe(history, "mixed", _facts("HAPI", version), f"2026-09-{day % 30 + 1:02d}")

    dates = [e["date"] for e in _events(history, "mixed")]
    assert dates == ["2026-09-02"], "the one genuine release is still the only event on record"
    assert _returns(history, "mixed") == 29


def test_state_digest_is_identity_not_similarity() -> None:
    a = fingerprint(_facts("HAPI", "1.0.0"))
    b = fingerprint(_facts("HAPI", "1.0.0"))
    c = fingerprint(_facts("HAPI", "1.0.1"))
    assert state_digest(a) == state_digest(b)
    assert state_digest(a) != state_digest(c)


def test_remembered_states_are_bounded() -> None:
    history: dict[str, Any] = {}
    for i in range(60):
        observe(history, "x", _facts("HAPI", f"1.0.{i}"), f"2026-09-{i % 28 + 1:02d}")
    assert len(history["x"]["states"]) <= 24


def test_a_malformed_legacy_event_stops_the_rebuild_instead_of_being_interpreted() -> None:
    """Four ways a recorded event can fail to describe a transition, and none of them may guess.

    A history file is written by earlier versions of this code and edited by nobody, but it is
    still data read from disk. The rebuild either re-derives an earlier fingerprint exactly or
    declines to, and declining leaves the log as recorded.
    """
    malformed: list[Any] = [
        {"date": "2026-08-02", "changes": "software_version: 'a' -> 'b'"},  # not a list
        {"date": "2026-08-02", "changes": []},  # empty
        {"date": "2026-08-02", "changes": ["software_version: abc -> def"]},  # not literals
        {"date": "2026-08-02", "changes": ["software_version: 'a' -> 'nowhere-near'"]},  # no match
        {"date": "2026-08-02", "changes": [{"software_version": "a"}]},  # not even a line
    ]
    for bad in malformed:
        events = ({"date": "2026-08-01", "changes": ["software_version: 'a' -> 'b'"]}, bad)
        history: dict[str, Any] = {
            "x": _legacy_entry(tuple(events), "HAPI FHIR Server", "5.4.1.13_edfx")
        }
        observe(history, "x", _facts("HAPI FHIR Server", "5.4.1.13_edfx"), "2026-08-19")
        assert len(_events(history, "x")) == 2, f"rebuilt across {bad!r}"
        assert "alternations" not in history["x"]


def test_a_malformed_alternation_record_is_skipped_not_rendered() -> None:
    history: dict[str, Any] = {"x": {"alternations": ["not a record"], "observations": []}}
    result = observe(history, "x", _facts("HAPI", "1.0.0"), "2026-09-01")
    assert result.alternations == ()


DATED_RETURN: dict[str, Any] = {
    "digest": "b4924cf854242db6",
    "first_return": "2026-08-08",
    "last_return": "2026-08-26",
    "returns": 9,
    "state_first_seen": "2026-08-08",
    "changes": [],
}


def _published(record: dict[str, Any]) -> tuple[str, ...]:
    history: dict[str, Any] = {"x": {"alternations": [record], "observations": []}}
    return observe(history, "x", _facts("HAPI", "1.0.0"), "2026-09-01").alternations


def test_the_published_return_sentence_never_invents_a_date() -> None:
    """These lines are rendered verbatim on the endpoint page and in the report, and both ways a
    date could go missing formatted as one.

    ``_apply_alternation_rule`` writes ``UNDATED`` when it cannot re-derive a date, which printed
    as "unknown to unknown: returned 9 times"; and a key missing altogether printed through
    ``dict.get`` as the literal "None". Both read as a measurement. A return whose window is not
    on the record is dropped, exactly as an undated event is.
    """
    control = _published(DATED_RETURN)
    assert control == (
        "2026-08-08 to 2026-08-26: returned 9 times to a declaration first observed 2026-08-08"
        " - counted, not recorded as a new change each time",
    )

    for missing in ("first_return", "last_return"):
        assert _published(dict(DATED_RETURN, **{missing: UNDATED})) == ()
        assert _published({k: v for k, v in DATED_RETURN.items() if k != missing}) == ()


def test_a_return_whose_first_sighting_is_undated_is_still_published_and_says_so() -> None:
    """The window is the return; ``state_first_seen`` is a detail hanging off it. Dropping the
    return over a missing detail would lose a window that really was recorded, so the sentence
    says what it does not know instead."""
    for value in ({"state_first_seen": UNDATED}, {}):
        record = {k: v for k, v in DATED_RETURN.items() if k != "state_first_seen"} | value
        (line,) = _published(record)
        assert (
            "returned 9 times to a declaration whose first sighting this record does not date"
            in line
        )
        assert "2026-08-08 to 2026-08-26" in line
        assert UNDATED not in line and "None" not in line


def test_an_outage_does_not_lose_the_alternation_record() -> None:
    history: dict[str, Any] = {}
    observe(history, "flapper", _facts("HAPI", "a"), "2026-09-01")
    observe(history, "flapper", _facts("HAPI", "b"), "2026-09-02")
    observe(history, "flapper", _facts("HAPI", "a"), "2026-09-03")
    outage = observe(history, "flapper", parse_capability(b"<html>502</html>"), "2026-09-04")
    assert len(outage.alternations) == 1
    assert _returns(history, "flapper") == 1


def _flapping_card(alternations: tuple[str, ...]) -> Scorecard:
    return build_scorecard(
        "la-care-provider-directory",
        "L.A. Care Provider Directory API",
        FetchResult(
            url="https://x.test/metadata", ok=True, status=200, elapsed_ms=10, body=b"", error=None
        ),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind="payer_provider_directory",
        observed_since="2026-08-08",
        drift_events=("2026-08-08: software_version: '5.4.1.13_edfx' -> '5.4.1.11_edfx'",),
        drift_alternations=alternations,
    )


def test_the_published_page_separates_a_return_from_a_change() -> None:
    line = "2026-08-08 to 2026-08-16: returned 4 times to a declaration first observed 2026-08-08"
    card = _flapping_card((line,))
    body = endpoint_page(card, base_url="https://x.test", verified="fixture", origin="https://o")
    assert "Declared capability changes" in body.body
    assert "Declarations this endpoint returns to" in body.body
    assert line in body.body
    assert "one hostname in front of more than one backend" in body.body

    # And the single-file report says it too, so the two renderers cannot disagree.
    assert line in render_html([card], generated_at="2026-08-19 00:00 UTC")


def test_the_dataset_publishes_returns_in_their_own_field(tmp_path: Path) -> None:
    """A consumer counting capability changes must not be handed returns among them."""
    card = _flapping_card(("2026-08-16: returned 4 times",))
    endpoint = Endpoint(
        endpoint_id=card.endpoint_id,
        name=card.name,
        kind=card.kind,
        base_url="https://x.test",
        verified_method="fixture",
        verified_date="2026-08-19",
    )
    write_dataset(
        tmp_path,
        [card],
        [endpoint],
        origin="https://o",
        generated_at="2026-08-19 00:00 UTC",
        vantage="test",
    )
    payload = json.loads(
        (tmp_path / "api" / "endpoint" / f"{card.endpoint_id}.json").read_text(encoding="utf-8")
    )
    assert payload["drift_events"] == list(card.drift_events)
    assert payload["drift_alternations"] == ["2026-08-16: returned 4 times"]
