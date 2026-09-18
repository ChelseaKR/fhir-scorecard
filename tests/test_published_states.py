"""Every state the grader can publish, pinned through the path that actually builds the site.

``tests/test_failure_kinds.py::test_no_published_grade_moved`` pins five published cards, and it
is the only test in the suite that reads one. It builds with ``--offline --fixtures``, which
routes through ``cli._grade_endpoint``: one vantage, probing directly. **The published site is
built with ``--from-probes``**, which routes through ``cli._grade_from_probes`` and reconciles
three vantages' artifacts, and no published card was pinned through that function at all.

That is not a theoretical gap. Both of 2026-09-12's grade defects were in `_grade_from_probes`
and in nothing else:

* ``reconcile(probes)`` was passed ``None`` for an endpoint no vantage reported on, so an
  endpoint nobody asked about and an endpoint every vantage asked and none answered were the
  same object (#138).
* a SMART document every vantage asked for and none was served read as one nobody had asked
  for, which withheld 35 interop points, which left ``grading.letter`` unable to pin a band -
  and eighteen of eighty-one endpoints published no letter while all three vantages held their
  CapabilityStatements (#140).

Neither was reachable from a single-vantage fixture run, so neither could be pinned, so both
shipped. This file replays the committed captures as three vantages' probe artifacts and pins
what the publishing path publishes.

**What a fixture set has to be able to express.** Before #137 the offline captures were five
endpoints that all answered, and the unreachable path was not merely untested but
*unrepresentable*: there was no fixture shape that could encode it. ``refusal.json`` fixed that
for the direct path. The states below are the remaining ones, and three of the five could not be
expressed by any arrangement of that fixture set, because a single-vantage run has no consensus
object and therefore no vantage disagreement, no reporting count, and no way to say that nobody
looked.

The probe artifacts are written here rather than committed, from the *same* captured documents
``tests/fixtures/`` already holds. A committed copy of a CapabilityStatement inside a probe file
would be a second copy of a document that gets refreshed, and the two would drift the first time
anyone refreshed one of them - the "two documents each recording half a list" shape. What is
committed is the assignment: which vantage saw what, as a table in this file.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from fhir_scorecard.published import audit_published_grades
from fhir_scorecard.vantage import VantageProbe, write_probes

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REGISTRY = FIXTURES / "registry.json"

#: The three labels the publishing workflow's probe matrix produces. Spelled out because the
#: number of *networks* behind them is a published fact (`pages.yml` explains why three
#: GitHub-hosted runners are one network), and a test that invented labels would be exercising a
#: topology this project does not run.
VANTAGES = (
    "github-actions/macos-latest",
    "github-actions/ubuntu-latest",
    "github-actions/windows-latest",
)

#: Well under R2's first latency band, so every reachability score below is about whether the
#: endpoint answered rather than about how fast. A number near a band edge would make these pins
#: fail for a reason that has nothing to do with the state being pinned.
FAST_MS = 150


def _document(endpoint_id: str, name: str) -> str:
    """One committed capture, read from disk. Never a hand-written stand-in."""
    path = FIXTURES / endpoint_id / name
    assert path.is_file(), f"{path} is missing; this fixture set cannot express the state"
    return path.read_text(encoding="utf-8")


def _reached(vantage: str, endpoint_id: str, *, smart: bool) -> VantageProbe:
    return VantageProbe(
        vantage=vantage,
        reachable=True,
        elapsed_ms=FAST_MS,
        capability=_document(endpoint_id, "metadata.json"),
        smart=_document(endpoint_id, "smart.json") if smart else None,
        status=200,
        # True on every probe this repository writes: `cli._grade_endpoint` requests both
        # documents unconditionally, whatever /metadata did. It is what tells "asked and not
        # served" apart from "never asked", and getting it wrong is #140.
        smart_requested=True,
    )


def _refused(vantage: str, kind: str, error: str, status: int | None = None) -> VantageProbe:
    return VantageProbe(
        vantage=vantage,
        reachable=False,
        elapsed_ms=0,
        error=error,
        status=status,
        failure_kind=kind,
        smart_requested=True,
    )


def _probes() -> dict[str, dict[str, VantageProbe]]:
    """Who saw what, per vantage, for the five committed endpoints.

    One endpoint per state, and the states are the ones in this module's docstring. An endpoint
    that appears in no vantage's map is one **nobody asked about**, which is the only way to
    express that state and the reason `bcbs-arizona-patient-access` is absent below.
    """
    macos, ubuntu, windows = VANTAGES
    per: dict[str, dict[str, VantageProbe]] = {vantage: {} for vantage in VANTAGES}

    # Reached from every vantage, both documents retrieved.
    for vantage in VANTAGES:
        per[vantage]["cms-blue-button-2"] = _reached(vantage, "cms-blue-button-2", smart=True)

    # Reached from two vantages and refused at the third: the endpoint is up, and this project's
    # whole reconciliation rule is that one witness settles that.
    for vantage in (macos, ubuntu):
        per[vantage]["inferno-reference"] = _reached(vantage, "inferno-reference", smart=True)
    per[windows]["inferno-reference"] = _refused(windows, "forbidden", "HTTP 403", 403)

    # Reached from every vantage; every vantage asked for the SMART document and none was
    # served one. The live server answers 404 there, which is why no smart.json is committed
    # for it, and it is the state eighteen live endpoints were in on 2026-09-12.
    for vantage in VANTAGES:
        per[vantage]["oracle-health-open"] = _reached(vantage, "oracle-health-open", smart=False)

    # Asked from every vantage, answered by none, and the three disagree about why. Published as
    # the disagreement it is and never resolved to one condition.
    for vantage, (kind, sentence) in zip(
        VANTAGES,
        (
            ("tls", "TLS certificate verification failed (unable to get local issuer certificate)"),
            ("timeout", "the request timed out"),
            ("dns", "DNS did not resolve"),
        ),
        strict=True,
    ):
        per[vantage]["aspirus-patient-access"] = _refused(vantage, kind, sentence)

    return per


@pytest.fixture
def published(tmp_path: Path) -> Path:
    """A site built the way the publishing workflow builds it: from probe artifacts alone."""
    from fhir_scorecard.cli import main

    per_vantage = _probes()
    paths = []
    for vantage, probes in per_vantage.items():
        path = tmp_path / "probes" / f"{vantage.replace('/', '-')}.json"
        write_probes(path, vantage, probes)
        paths.append(str(path))

    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--from-probes",
                "--probes-in",
                *paths,
                "--registry",
                str(REGISTRY),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
                # An absent directory means no cohorts. The shipped cohorts name endpoints this
                # five-entry fixture registry does not carry.
                "--cohorts",
                str(tmp_path / "no-cohorts"),
            ]
        )
        == 0
    )
    return out


def _rows(site: Path) -> dict[str, dict[str, str]]:
    text = (site / "dataset.csv").read_text(encoding="utf-8")
    return {row["endpoint_id"]: row for row in csv.DictReader(io.StringIO(text))}


#: What the publishing path publishes for each state, as literals, read off a real run of the
#: assignment above. Every column that distinguishes one state from another is here: a pin over
#: the letter alone would not have noticed #138, which moved no letter and only ever moved the
#: reporting count and the condition.
#:
#: ``(grade, reachability, transparency, interop, reached, reporting, failure_kinds)``
PINNED_FROM_PROBES: dict[str, tuple[object, ...]] = {
    # Asked from three vantages, answered by none, and the three disagreed about why.
    "aspirus-patient-access": ("not observed", "", "", "", "0", "3", ["dns", "timeout", "tls"]),
    # Nobody asked. Not a failure population, and not a zero: no vantage reported at all.
    "bcbs-arizona-patient-access": ("not observed", "", "", "", "0", "0", []),
    # Reached from all three, both documents in hand.
    "cms-blue-button-2": ("B", "100", "100", "60", "3", "3", []),
    # Reached from two of three. One witness settles that the endpoint is up, so it grades on
    # the documents that were retrieved and is in no failure population.
    "inferno-reference": ("A", "100", "80", "100", "2", "3", []),
    # Reached from all three; SMART asked for by all three and served to none. This is the row
    # that published no letter at all before #140, and the C is what the fix restored.
    "oracle-health-open": ("C", "100", "80", "40", "3", "3", []),
}


def test_no_grade_the_publishing_path_publishes_moved(published: Path) -> None:
    """The pin. Same job as ``test_no_published_grade_moved``, one code path over."""
    rows = _rows(published)
    assert set(rows) == set(PINNED_FROM_PROBES), "the fixture registry moved under this pin"
    seen = {
        endpoint_id: (
            row["grade"],
            row["reachability_score"],
            row["transparency_score"],
            row["interop_score"],
            row["vantages_reached"],
            row["vantages_reporting"],
            sorted(row["failure_kinds"].split()),
        )
        for endpoint_id, row in rows.items()
    }
    assert seen == {k: tuple(v) for k, v in PINNED_FROM_PROBES.items()}


def test_the_per_endpoint_records_agree_with_the_pinned_csv(published: Path) -> None:
    """The pin is taken off ``dataset.csv``, which nothing read before. The per-endpoint JSON is
    what every downstream consumer builds from, so the two have to be the same numbers."""
    rows = _rows(published)
    for path in sorted((published / "api" / "endpoint").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))["endpoint"]
        row = rows[record["endpoint_id"]]
        assert record["grade"] == row["grade"]
        for column in ("reachability_score", "transparency_score", "interop_score"):
            assert str(record[column]) == row[column], (record["endpoint_id"], column)


def test_the_publishing_path_satisfies_the_published_grade_contract(published: Path) -> None:
    assert audit_published_grades(published) == []


# ----------------------------------------------------------------------------------
# The states themselves, each asserted to be present rather than assumed
# ----------------------------------------------------------------------------------


def test_an_endpoint_nobody_asked_about_is_not_in_a_failure_population(published: Path) -> None:
    """#138's state, and the one no arrangement of the direct-probe fixtures could express.

    Zero reporting vantages, no condition named, no score anywhere. An ``unclassified`` here
    would file an endpoint nobody looked at into a failure population, and a zero would publish a
    measurement nobody made.
    """
    row = _rows(published)["bcbs-arizona-patient-access"]
    assert row["vantages_reporting"] == "0"
    assert row["failure_kinds"] == ""
    assert row["reachable"] == "false"
    assert row["grade"] == "not observed"
    assert [row[c] for c in ("reachability_score", "transparency_score", "interop_score")] == [
        "",
        "",
        "",
    ]


def test_an_endpoint_every_vantage_asked_and_none_reached_names_every_condition(
    published: Path,
) -> None:
    """The other side of that pair. Three vantages asked, three saw different things, and all
    three are published rather than resolved to one."""
    row = _rows(published)["aspirus-patient-access"]
    assert row["vantages_reporting"] == "3"
    assert row["vantages_reached"] == "0"
    assert sorted(row["failure_kinds"].split()) == ["dns", "timeout", "tls"]


def test_a_vantage_disagreement_about_reachability_does_not_lower_a_grade(
    published: Path,
) -> None:
    """One vantage reaching settles that the endpoint is up. The blocked vantage is published as
    a count, never as a condition attached to a working endpoint."""
    row = _rows(published)["inferno-reference"]
    assert (row["vantages_reached"], row["vantages_reporting"]) == ("2", "3")
    assert row["reachable"] == "true"
    assert row["failure_kinds"] == ""
    assert row["grade"] == "A"


def test_a_smart_document_asked_for_everywhere_and_served_nowhere_still_pins_a_letter(
    published: Path,
) -> None:
    """#140. Read as an absence instead, the interop points are withheld, the weighted bounds
    straddle two bands and ``letter`` publishes nothing - which is what eighteen live endpoints
    did on 2026-09-12."""
    row = _rows(published)["oracle-health-open"]
    assert row["grade"] == "C"
    assert row["interop_score"] == "40"
    assert row["reachable"] == "true"


def _findings(published: Path, endpoint_id: str) -> dict[str, dict[str, object]]:
    payload = json.loads(
        (published / "api" / "endpoint" / f"{endpoint_id}.json").read_text(encoding="utf-8")
    )
    return {
        finding["code"]: finding
        for dimension in payload["dimensions"]
        for finding in dimension["findings"]
    }


def test_the_smart_state_is_published_as_a_finding_and_not_as_an_absence(
    published: Path,
) -> None:
    """#140 at the level the letter is computed from, not just at the letter.

    A document every vantage asked for and none was served is an **observation**: I2 is scored 0
    of 35 and withholds nothing. Read as an absence it would withhold 35 points, which is what
    left ``letter`` with nothing to pin.
    """
    smart_not_served = _findings(published, "oracle-health-open")["I2"]
    assert smart_not_served["observed"] is True
    assert smart_not_served["ok"] is False
    assert smart_not_served["withheld_points"] == 0
    assert smart_not_served["max_points"] == 35

    served = _findings(published, "cms-blue-button-2")["I2"]
    assert (served["observed"], served["ok"], served["withheld_points"]) == (True, True, 0)


def test_asked_and_unanswered_is_published_apart_from_nobody_asking(published: Path) -> None:
    """The two ungraded reasons, on the artifact. ``observed: false`` alone cannot separate
    them, and only the first of the two is information about the endpoint."""
    asked = _findings(published, "aspirus-patient-access")["R1"]
    assert (asked["observed"], asked["unanswered"]) == (False, True)

    nobody = _findings(published, "bcbs-arizona-patient-access")["R1"]
    assert (nobody["observed"], nobody["unanswered"]) == (False, False)
    assert "no vantage reported" in str(nobody["message"])


def _state(published: Path, endpoint_id: str) -> tuple[object, ...]:
    """One endpoint's published state, in the fields that tell the five apart.

    Deliberately not the scores: two endpoints can be in genuinely different states and score
    the same, and two can score differently for reasons that are not a state at all. The fields
    here are the ones each state is *defined* by.
    """
    row = _rows(published)[endpoint_id]
    findings = _findings(published, endpoint_id)
    reachability = findings["R1"]
    smart = findings.get("I2")
    return (
        row["reachable"],
        row["vantages_reached"],
        row["vantages_reporting"],
        bool(row["failure_kinds"]),
        (reachability["observed"], reachability["unanswered"]),
        None if smart is None else (smart["observed"], smart["ok"], smart["withheld_points"]),
    )


def test_the_fixture_set_can_express_every_state_this_pin_names(published: Path) -> None:
    """The gate's own coverage, asserted rather than left for a reader to count.

    Five states, five endpoints, and the check is that each one is *distinguishable* in what was
    published - not merely that five rows exist. Three of them (nobody asked, vantages
    disagreeing about reachability, vantages disagreeing about why) are unreachable from a
    single-vantage run and were unrepresentable by this project's fixture set until this file
    existed: there was no arrangement of ``metadata.json`` and ``refusal.json`` that could encode
    them, in the same way there was no way to encode an unreachable endpoint at all before #137.
    """
    rows = _rows(published)
    states = {endpoint_id: _state(published, endpoint_id) for endpoint_id in rows}
    assert len(set(states.values())) == len(rows), (
        f"two endpoints are in the same published state, so this file pins fewer states than "
        f"it names: {states}"
    )
    assert len(rows) == len(PINNED_FROM_PROBES) >= 5
