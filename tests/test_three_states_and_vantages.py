"""Three states, not two, and the per-vantage rows that make the third one mean something.

`observed=False` was carrying two opposite facts. "Every vantage asked on 2026-09-12 and none of
them was answered" is dated, sourced information about an endpoint. "Nobody asked" is the absence
of information. Publishing both as `not observed` is #135 with its harm turned down rather than
removed: it stopped overstating and started understating, and a reader could not tell which.

Measured on the live registry the day this was written: of 81 endpoints, **67 reached, 14 asked
everywhere and answered nowhere, 0 not asked**. So every endpoint publishing "not observed" for
reachability was in fact the state that is a finding.

And the reason there is no fourth option -- electing an authoritative vantage -- is measured too.
Across all 81 endpoints, three GitHub-hosted vantages against one residential vantage, three
disagreed and **not in one direction**: `ambetter-centene-provider-directory` answered
residentially and 403'd from all three runners, while `capital-bluecross` and
`chg-provider-directory` did the reverse, the latter pair being the 2026-08-05 TLS-interception
incident still live on that residential network. Promoting either side would have relocated the
misdiagnosis onto different named companies rather than removed it. So the rows are published and
the disagreement is the finding.

**What these tests are built to catch, specifically.** The previous pass on this module shipped
two tests that could not fail: one proved the merge *read* a new field while nothing proved
anything *wrote* it, and one proved a field survived a round trip while `collapse_by_vantage`
silently dropped it. Both passed a negative control that should have reddened them. So every
field added here is exercised through the writer that populates it in production -- the CLI, end
to end -- and the structural test below fails if any future field on either dataclass is added
without a stated fate in the collapse.
"""

from __future__ import annotations

import json
from pathlib import Path

from fhir_scorecard.capability import NO_CAPABILITY_RETRIEVED, NO_SMART_RETRIEVED
from fhir_scorecard.cli import main as cli_main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import NOT_OBSERVED, build_scorecard
from fhir_scorecard.vantage import VantageProbe, VantageReport, reconcile, write_probes

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
FIXTURE_REGISTRY = FIXTURES / "registry.json"

#: The endpoint deliberately left out of the probe files below, so the merge has nothing to read
#: for it. That is the only way state (b) can arise: a probing run always probes, so "nobody
#: asked" is a property of the *merge*, never of a probe.
UNREPORTED = "inferno-reference"


def _fixture_ids() -> list[str]:
    return [e["id"] for e in json.loads(FIXTURE_REGISTRY.read_text())["endpoints"]]


def _probe_files(tmp_path: Path) -> list[Path]:
    """Two vantages' probe files, built from the committed captures rather than committed.

    Derived on purpose. A probe file checked in beside the captures is a second copy of the same
    documents, and the two drift the first time one is refreshed -- the failure being silent,
    because both halves stay internally consistent. Building them here means the probe files are
    always exactly what a probing run over these captures would have written.

    ``UNREPORTED`` is omitted from both, which is what produces the third state.
    """
    written: list[Path] = []
    for vantage, elapsed in (("ci/alpha", 300), ("ci/beta", 900)):
        probes: dict[str, VantageProbe] = {}
        for endpoint_id in _fixture_ids():
            if endpoint_id == UNREPORTED:
                continue
            metadata = FIXTURES / endpoint_id / "metadata.json"
            smart = FIXTURES / endpoint_id / "smart.json"
            refusal = FIXTURES / endpoint_id / "refusal.json"
            if refusal.is_file():
                captured = json.loads(refusal.read_text())
                probes[endpoint_id] = VantageProbe(
                    vantage=vantage,
                    reachable=False,
                    elapsed_ms=0,
                    error=captured["error"],
                    status=captured.get("status"),
                    failure_kind=captured.get("failure_kind"),
                    smart_requested=True,
                )
            else:
                probes[endpoint_id] = VantageProbe(
                    vantage=vantage,
                    reachable=True,
                    elapsed_ms=elapsed,
                    capability=metadata.read_text(),
                    smart=smart.read_text() if smart.is_file() else None,
                    status=200,
                    smart_requested=True,
                )
        path = tmp_path / f"probes-{vantage.replace('/', '-')}.json"
        write_probes(path, vantage, probes)
        written.append(path)
    return written


def _publish(tmp_path: Path) -> dict[str, dict]:
    """Run the real publishing path over those files and hand back the published cards."""
    out = tmp_path / "site"
    assert (
        cli_main(
            [
                "grade",
                "--registry",
                str(FIXTURE_REGISTRY),
                "--out",
                str(out),
                "--origin",
                "https://example.test",
                "--history",
                str(tmp_path / "history.json"),
                "--from-probes",
                "--probes-in",
                *[str(p) for p in _probe_files(tmp_path)],
            ]
        )
        == 0
    )
    return {
        json.loads(p.read_text())["endpoint"]["endpoint_id"]: json.loads(p.read_text())
        for p in sorted((out / "api" / "endpoint").glob("*.json"))
    }


# ----------------------------------------------------------------------------------
# The three states, on the published artifact
# ----------------------------------------------------------------------------------


def test_the_fixture_set_produces_all_three_states(tmp_path: Path) -> None:
    """The gate's own coverage. If any state stops being represented, this fails.

    #137 added the second state to a fixture set that only had the first, and that is what let a
    published `reachability_score: 0` survive. The same argument applies to the third: a gate
    that never sees "nobody asked" cannot notice the day it starts rendering as "asked and
    refused", which would be a fabricated observation about a named company.
    """
    cards = _publish(tmp_path)

    def reachability(card: dict) -> dict:
        return next(d for d in card["dimensions"] if d["key"] == "reachability")

    reached = [e for e, c in cards.items() if c["endpoint"]["reachable"] == "true"]
    unanswered = [
        e
        for e, c in cards.items()
        if all(f["unanswered"] for f in reachability(c)["findings"])
        and c["endpoint"]["reachable"] != "true"
    ]
    unreported = [
        e
        for e, c in cards.items()
        if not any(f["unanswered"] for f in reachability(c)["findings"])
        and c["endpoint"]["reachable"] != "true"
    ]

    assert reached, "state 1 (reached and graded) is not represented"
    assert unanswered, "state 2 (asked everywhere, answered nowhere) is not represented"
    assert unreported == [UNREPORTED], (
        f"state 3 (no vantage reported) must be exactly {UNREPORTED!r}, got {unreported}"
    )
    # The three are disjoint and cover the set: no card is in two states or none.
    assert len(reached) + len(unanswered) + len(unreported) == len(cards)


def test_the_two_unreached_states_publish_different_sentences(tmp_path: Path) -> None:
    """Same absent score, different published claim. That is the whole point."""
    cards = _publish(tmp_path)
    asked = next(
        c for e, c in cards.items() if e != UNREPORTED and c["endpoint"]["reachable"] != "true"
    )
    never_asked = cards[UNREPORTED]

    for card in (asked, never_asked):
        # Neither publishes a number. The score was the false part in #135 and it stays absent.
        for dimension in card["dimensions"]:
            assert dimension["score"] is None, card["endpoint"]["endpoint_id"]
        assert card["endpoint"]["reachability_score"] == ""
        assert card["endpoint"]["grade"] == NOT_OBSERVED

    asked_r1 = next(
        f
        for d in asked["dimensions"]
        if d["key"] == "reachability"
        for f in d["findings"]
        if f["code"] == "R1"
    )
    never_r1 = next(
        f
        for d in never_asked["dimensions"]
        if d["key"] == "reachability"
        for f in d["findings"]
        if f["code"] == "R1"
    )
    assert asked_r1["unanswered"] is True
    assert never_r1["unanswered"] is False
    # The one that was asked says how many vantages asked, and when.
    assert "vantages tried" in asked_r1["message"]
    assert "observed 2" in asked_r1["message"], asked_r1["message"]
    # The one nobody reported on claims nothing about any vantage.
    assert "no vantage reported" in never_r1["message"]
    assert never_asked["vantages"] == []


# ----------------------------------------------------------------------------------
# Per-vantage rows: written, not merely readable
# ----------------------------------------------------------------------------------


def test_the_publishing_run_writes_a_row_for_every_reporting_vantage(tmp_path: Path) -> None:
    """Driven through the CLI, because the last pass proved a field readable and never written.

    Every endpoint two vantages reported on must publish two rows, whether they agreed or not.
    """
    cards = _publish(tmp_path)
    for endpoint_id, card in cards.items():
        expected = 0 if endpoint_id == UNREPORTED else 2
        assert len(card["vantages"]) == expected, endpoint_id
        assert {r["vantage"] for r in card["vantages"]} == (
            set() if expected == 0 else {"ci/alpha", "ci/beta"}
        ), endpoint_id
        # Two numbers on the row, always, and they agree with the rows themselves.
        record = card["endpoint"]
        assert int(record["vantages_reporting"] or 0) == expected, endpoint_id
        assert int(record["vantages_reached"] or 0) == sum(
            1 for r in card["vantages"] if r["reachable"]
        ), endpoint_id


def test_a_failed_vantage_publishes_no_latency_and_a_reached_one_publishes_no_condition(
    tmp_path: Path,
) -> None:
    """The two coercions this project has been bitten by, on the new surface.

    `elapsed_ms` of 0 for a probe that never completed is the fastest possible reading of a
    measurement nobody took, and it is what `probe_entry_failure` exists to refuse one layer
    down. A `failure_kind` beside a reachable vantage files a working endpoint into a failure
    population.
    """
    cards = _publish(tmp_path)
    rows = [r for c in cards.values() for r in c["vantages"]]
    assert rows
    for row in rows:
        if row["reachable"]:
            assert row["failure_kind"] is None, row
            assert row["error"] is None, row
            assert isinstance(row["elapsed_ms"], int), row
        else:
            assert row["elapsed_ms"] is None, row
            assert row["failure_kind"], row


def test_disagreement_is_published_rather_than_resolved() -> None:
    """One vantage reaching still settles that the endpoint is up -- and the dissent survives.

    This is the asymmetry the module rests on, and it is a property of the *claims*, not a
    preference for a vantage: "it is reachable" needs one witness, "it is unreachable" is a
    universal statement needing every vantage this run had. Publishing the rows is what keeps
    the first from hiding the second.
    """
    consensus = reconcile(
        [
            VantageProbe("ci/alpha", True, 300, status=200, smart_requested=True),
            VantageProbe(
                "ci/beta",
                False,
                0,
                "HTTP 403",
                status=403,
                failure_kind="forbidden",
                smart_requested=True,
            ),
        ]
    )
    assert consensus.reachable is True, "one witness settles that it is up"
    assert consensus.agreeing == 1 and consensus.vantages == 2
    assert len(consensus.reports) == 2, "the vantage that disagreed must not be dropped"
    assert {r.reachable for r in consensus.reports} == {True, False}

    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        FetchResult("https://payer.test/r4/metadata", True, 200, 300, b"{}", None),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
    )
    assert len(card.vantage_reports) == 2


# ----------------------------------------------------------------------------------
# Structural: a new field must be given a fate
# ----------------------------------------------------------------------------------


def test_every_probe_and_report_field_has_a_stated_fate() -> None:
    """Adding a field to either dataclass without deciding what happens to it fails here.

    `collapse_by_vantage` rebuilds `VantageProbe` field by field, so a field it does not name
    reads back as its default -- and this project's defaults are deliberately the conservative
    values that *withhold* a claim, which means the failure is silent and looks like caution.
    Deleting one such line left all 1,148 tests green when this was last measured.

    `_report_for` has the same shape one layer out. Both are listed here, and a field on either
    that is not named fails with a message saying which.
    """
    from dataclasses import fields

    probe_carried = {"vantage", "reachable", "elapsed_ms", "capability", "smart", "status"}
    probe_dropped_when_reached = {"error", "failure_kind"}
    probe_decided = probe_carried | probe_dropped_when_reached | {"smart_requested"}
    probe_actual = {f.name for f in fields(VantageProbe)}
    assert probe_actual == probe_decided, (
        "VantageProbe fields with no stated fate in collapse_by_vantage: "
        f"{sorted(probe_actual - probe_decided)}; named here but gone from the dataclass: "
        f"{sorted(probe_decided - probe_actual)}"
    )

    report_actual = {f.name for f in fields(VantageReport)}
    report_decided = {
        "vantage",
        "network",
        "reachable",
        "status",
        "failure_kind",
        "elapsed_ms",
        "error",
    }
    assert report_actual == report_decided, (
        "VantageReport fields with no stated fate in _report_for or in the published JSON: "
        f"{sorted(report_actual - report_decided)}; named here but gone from the dataclass: "
        f"{sorted(report_decided - report_actual)}"
    )

    # And every field of a published row really is published, rather than listed above and
    # forgotten. This is the half that the round-trip test missed last time.
    from fhir_scorecard.vantage import _report_for

    rich = VantageProbe(
        "ci/alpha",
        False,
        0,
        "HTTP 403",
        status=403,
        failure_kind="forbidden",
        smart_requested=True,
    )
    report = _report_for(rich)
    assert report.vantage == "ci/alpha"
    assert report.network == "ci"
    assert report.status == 403
    assert report.failure_kind == "forbidden"
    assert report.error == "HTTP 403"
    assert report.elapsed_ms is None


def test_collapsing_two_samples_of_one_vantage_keeps_every_carried_field() -> None:
    """The collapse is where a carried field silently becomes its default."""
    from fhir_scorecard.vantage import collapse_by_vantage

    rich = VantageProbe(
        vantage="ci/alpha",
        reachable=True,
        elapsed_ms=400,
        capability="{}",
        smart="{}",
        status=200,
        smart_requested=True,
    )
    collapsed = collapse_by_vantage([rich, rich])
    assert len(collapsed) == 1
    for name in ("vantage", "reachable", "capability", "smart", "status", "smart_requested"):
        assert getattr(collapsed[0], name) == getattr(rich, name), f"{name} lost in the collapse"


# ----------------------------------------------------------------------------------
# #139: when did it last answer
# ----------------------------------------------------------------------------------


def test_the_run_writes_the_date_an_endpoint_last_answered(tmp_path: Path) -> None:
    """Derived from the observation record by the run, not constructed in the test.

    `availability` gives a rate and a rate is not a date: at 94% a reader cannot tell whether the
    last success was yesterday or three weeks ago (#139).
    """
    cards = _publish(tmp_path)
    for endpoint_id, card in cards.items():
        record = card["endpoint"]
        if record["reachable"] == "true":
            # A fresh history holds exactly this run, so the date it last answered is this run's.
            assert record["last_answered"], endpoint_id
        else:
            # Nothing in the window is a success. Empty, never a placeholder date.
            assert record["last_answered"] == "", endpoint_id


def test_last_answered_is_the_latest_success_not_the_last_row() -> None:
    """Recorded out of order, or repaired by hand, the newest row is not the newest date."""
    from fhir_scorecard.drift import _record_observation

    entry = {
        "observations": [
            {"date": "2026-09-01", "up": False},
            {"date": "2026-09-09", "up": True},
            {"date": "2026-09-03", "up": True},
        ]
    }
    availability = _record_observation(entry, "2026-09-12", reachable=False)
    assert availability.last_answered == "2026-09-09"
    assert availability.reachable == 2
    assert availability.observations == 4


def test_no_successful_observation_is_empty_and_never_a_date() -> None:
    from fhir_scorecard.drift import _record_observation

    entry = {"observations": [{"date": "2026-09-01", "up": False}]}
    assert _record_observation(entry, "2026-09-12", reachable=False).last_answered is None


def test_the_page_says_the_window_is_bounded_rather_than_saying_never() -> None:
    """ "never answered" would be a claim about all time; the record is a rolling window."""
    from fhir_scorecard.site import endpoint_page

    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        FetchResult("https://payer.test/r4/metadata", False, None, 0, b"", "connection refused"),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        last_answered=None,
    )
    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live fetch",
        origin="https://example.test",
    )
    assert "not in the recorded window" in page.body
    assert "never answered" not in page.body


def test_a_reached_vantage_carrying_a_failure_kind_still_publishes_none() -> None:
    """Tested directly, because no fixture can reach this branch.

    A control that sabotaged `_report_for`'s `failure_kind` guard stayed green: every reachable
    probe in every fixture already carries `failure_kind=None`, so removing the guard changed
    nothing observable and the sabotage read as a pass. That is a control you can predict will
    lie, and the answer is a case the guard is actually load-bearing for.

    `load_probe_files` normalises this one layer up, which is exactly why the shape exists: a
    foreign vantage (#100) that ships both `"reachable": true` and a failure kind must not put a
    working endpoint into a failure population, and defence in depth is only defence if something
    checks the inner layer.
    """
    from fhir_scorecard.vantage import _report_for

    contradictory = VantageProbe(
        "foreign/one", True, 300, "HTTP 403", status=200, failure_kind="forbidden"
    )
    report = _report_for(contradictory)
    assert report.reachable is True
    assert report.failure_kind is None, "a vantage that reached has no failure to classify"
    assert report.error is None


def test_the_card_renders_the_per_vantage_table(tmp_path: Path) -> None:
    """On the page, not only in the JSON.

    A control that stopped `_vantage_rows` returning anything left all 1,163 tests green: every
    assertion about the rows read `api/endpoint/<id>.json`, and none read the HTML a person
    actually sees. The requirement was that the disagreement be visible on the card.
    """
    from fhir_scorecard.site import endpoint_page

    consensus = reconcile(
        [
            VantageProbe("ci/alpha", True, 300, status=200, smart_requested=True),
            VantageProbe(
                "ci/beta",
                False,
                0,
                "connection timed out",
                failure_kind="timeout",
                smart_requested=True,
            ),
        ]
    )
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        FetchResult("https://payer.test/r4/metadata", True, 200, 300, b"{}", None),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
    )
    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live fetch",
        origin="https://example.test",
    )
    assert "What each vantage saw" in page.body
    # Both numbers, in the caption a screen reader reaches through the table.
    assert "reached from 1 of 2 reporting vantages" in page.body
    # Both vantages named, including the one that disagreed -- which is the whole point.
    assert "ci/alpha" in page.body and "ci/beta" in page.body
    assert "connection timed out" in page.body
    assert "timeout" in page.body
    # And the caveat that makes the count readable: hosts on one network are not networks.
    assert "one network" in page.body


def test_a_card_with_no_reporting_vantages_renders_no_table() -> None:
    """Nothing to show is shown as nothing, not as an empty table claiming zero of zero."""
    from fhir_scorecard.site import endpoint_page

    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        FetchResult("https://payer.test/r4/metadata", False, None, 0, b"", "no vantage reported"),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    assert card.vantage_reports == ()
    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live fetch",
        origin="https://example.test",
    )
    assert "What each vantage saw" not in page.body
