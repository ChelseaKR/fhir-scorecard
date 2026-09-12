"""A document every vantage asked for and none was served is an observation, not an absence.

Measured on the live site on 2026-09-12. Eighteen of the eighty-one graded endpoints published
``not observed`` as their letter -- ``hapi-fhir-r4``, ``oracle-health-open``,
``kaiser-permanente-health-plan``, ``florida-blue-patient-access`` among them -- while all three
vantages had retrieved their CapabilityStatements on that same run. Their SMART discovery
documents answered 404. ``hapi.fhir.org/baseR4/metadata`` returned 200 and
``hapi.fhir.org/baseR4/.well-known/smart-configuration`` returned 404, by hand, the same day.

The two graders disagreed on that evidence. :func:`fhir_scorecard.cli._grade_endpoint`, which
probes directly, says in its own comment that "this run reached the host, so it did ask for the
SMART document: a failed SMART fetch is an observation that it is absent or unusable", and grades
I2 at 0 of 35. :func:`fhir_scorecard.cli._grade_from_probes`, which is the path the daily publish
actually runs, had no field to read that from: a probe file records ``smart: null`` both for a
vantage that asked and was refused and for one that never asked, so it took the second reading,
withheld the 35 points, and :func:`fhir_scorecard.grading.letter` could no longer pin a band.

That is this repository's own defect class with its sign reversed. The usual form publishes an
absence as a measurement; this published a measurement as an absence, and told a reader
"no vantage retrieved its public documents" on a page that then listed 146 resource types from
the document it said nobody had.

The property these tests hold is parity, not a letter: the two graders must reach the same
conclusion from the same evidence. A test written to today's grade for a named endpoint would go
stale the first time that endpoint changes what it publishes.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import good_capability

from fhir_scorecard.capability import NO_SMART_RETRIEVED, SMART_NOT_SERVED
from fhir_scorecard.cli import _grade_endpoint, _grade_from_probes
from fhir_scorecard.grading import NOT_OBSERVED
from fhir_scorecard.registry import Endpoint
from fhir_scorecard.vantage import VantageProbe, load_probe_files, write_probes

_ENDPOINT = Endpoint(
    endpoint_id="alpha",
    name="Alpha Health",
    kind="payer",
    base_url="https://alpha.test/r4",
    verified_method="fixture",
    verified_date="2026-09-12",
    expects="r4",
)


def _probe(vantage: str, *, smart: str | None, smart_requested: bool = True) -> VantageProbe:
    """One vantage that retrieved the CapabilityStatement, with or without a SMART document."""
    return VantageProbe(
        vantage=vantage,
        reachable=True,
        elapsed_ms=300,
        capability=json.dumps(good_capability()),
        smart=smart,
        status=200,
        smart_requested=smart_requested,
    )


def _from_probes(probes: list[VantageProbe]) -> object:
    return _grade_from_probes(
        _ENDPOINT,
        history={},
        today="2026-09-12",
        other_probes={_ENDPOINT.endpoint_id: probes},
        declared={},
        smart_declared={},
    )


def _direct(smart_body: bytes | None) -> object:
    """Grade the same endpoint by probing it, with the network answered from memory.

    ``smart_body`` of ``None`` is the 404 every one of the eighteen endpoints returned.
    """
    from fhir_scorecard import cli
    from fhir_scorecard.fetch import FetchResult

    capability = json.dumps(good_capability()).encode()

    def answer(url: str, **kwargs: object) -> FetchResult:
        if url.endswith("/metadata"):
            return FetchResult(url, True, 200, 300, capability, None)
        if smart_body is None:
            return FetchResult(url, False, 404, 300, b"", "HTTP 404", failure_kind="not_found")
        return FetchResult(url, True, 200, 300, smart_body, None)

    original = cli.fetch_json
    cli.fetch_json = answer  # type: ignore[assignment]
    try:
        return _grade_endpoint(
            _ENDPOINT,
            offline=False,
            fixtures=None,
            history={},
            today="2026-09-12",
            vantage="test/one",
            other_probes={},
            probes_seen={},
            declared={},
            smart_declared={},
        )
    finally:
        cli.fetch_json = original  # type: ignore[assignment]


def test_every_vantage_asked_and_none_was_served_grades_as_an_observation() -> None:
    """The reconciling path must reach the same grade the probing path reaches.

    Both are looking at the same fact: the endpoint answered ``/metadata``, and its SMART
    discovery document was requested and not served.
    """
    reconciled = _from_probes(
        [
            _probe("github-actions/ubuntu-latest", smart=None),
            _probe("github-actions/macos-latest", smart=None),
            _probe("github-actions/windows-latest", smart=None),
        ]
    )
    probed = _direct(None)

    assert probed.grade != NOT_OBSERVED, "the probing path has always graded this evidence"
    assert reconciled.grade == probed.grade, (
        f"the publishing path graded {reconciled.grade!r} where a direct probe of the same "
        f"evidence grades {probed.grade!r}"
    )

    interop = next(d for d in reconciled.dimensions if d.key == "interop")
    assert interop.score is not None, "a document requested and not served is a measured absence"
    assert interop.withheld_points == 0, (
        "nothing was withheld: every vantage asked for the SMART document and was answered 404"
    )
    smart_finding = next(f for f in interop.findings if f.code == "I2")
    assert smart_finding.observed is True
    assert smart_finding.max_points == 35


def test_a_probe_that_never_asked_is_still_an_absence() -> None:
    """The conservative reading survives, and it is the one a silent probe file gets.

    A vantage this project does not operate (#100) may post a file written before
    ``smart_requested`` existed, or by a writer that only fetches ``/metadata``. Nothing in such a
    file says the SMART document was ever requested, so nothing may be concluded about it.
    """
    reconciled = _from_probes(
        [
            _probe("foreign/one", smart=None, smart_requested=False),
            _probe("foreign/two", smart=None, smart_requested=False),
        ]
    )
    assert reconciled.grade == NOT_OBSERVED
    interop = next(d for d in reconciled.dimensions if d.key == "interop")
    assert interop.score is None
    assert interop.withheld_points == 35
    smart_finding = next(f for f in interop.findings if f.code == "I2")
    assert smart_finding.observed is False


def test_one_vantage_that_asked_settles_it_for_the_others() -> None:
    """The module's asymmetry, one level down: one vantage asking is enough to have asked.

    A peer that cannot report whether it asked does not unsettle a vantage that did.
    """
    reconciled = _from_probes(
        [
            _probe("github-actions/ubuntu-latest", smart=None, smart_requested=True),
            _probe("foreign/silent", smart=None, smart_requested=False),
        ]
    )
    assert reconciled.grade != NOT_OBSERVED


def test_a_retrieved_smart_document_is_unaffected() -> None:
    """The control: nothing about the case that already worked may move."""
    from conftest import good_smart

    body = json.dumps(good_smart())
    reconciled = _from_probes([_probe("github-actions/ubuntu-latest", smart=body)])
    probed = _direct(body.encode())
    assert reconciled.grade == probed.grade
    interop = next(d for d in reconciled.dimensions if d.key == "interop")
    assert interop.withheld_points == 0


def test_smart_requested_survives_the_probe_file_round_trip(tmp_path: Path) -> None:
    """``write_probes`` serialises a dataclass and ``load_probe_files`` reads field by field.

    A field the loader does not name reads back as its default, which for this one is the
    conservative ``False`` -- so the round trip is where a correct writer would silently lose the
    distinction the grader depends on.
    """
    path = tmp_path / "probes.json"
    write_probes(path, "test/one", {"alpha": _probe("test/one", smart=None)})
    assert json.loads(path.read_text())["probes"]["alpha"]["smart_requested"] is True
    loaded = load_probe_files([path])
    assert loaded["alpha"][0].smart_requested is True


def test_the_probing_run_records_that_it_asked(tmp_path: Path) -> None:
    """The writer, not a hand-built probe.

    Every other test here constructs ``VantageProbe`` itself, so sabotaging
    ``_grade_endpoint``'s ``smart_requested=True`` left all six of them green: they proved the
    merge reads the field and never that anything writes it. This one drives the real probing
    path and reads what it put in ``probes_seen``, which is what ``write_probes`` serialises.
    """
    probes_seen: dict[str, VantageProbe] = {}
    from fhir_scorecard import cli
    from fhir_scorecard.fetch import FetchResult

    capability = json.dumps(good_capability()).encode()

    def answer(url: str, **kwargs: object) -> FetchResult:
        if url.endswith("/metadata"):
            return FetchResult(url, True, 200, 300, capability, None)
        return FetchResult(url, False, 404, 300, b"", "HTTP 404", failure_kind="not_found")

    original = cli.fetch_json
    cli.fetch_json = answer  # type: ignore[assignment]
    try:
        cli._grade_endpoint(
            _ENDPOINT,
            offline=False,
            fixtures=None,
            history={},
            today="2026-09-12",
            vantage="test/one",
            other_probes={},
            probes_seen=probes_seen,
            declared={},
            smart_declared={},
        )
    finally:
        cli.fetch_json = original  # type: ignore[assignment]

    assert probes_seen["alpha"].smart_requested is True, (
        "a probing run asks for both documents unconditionally and must say so in its artifact"
    )


def test_probe_then_publish_reaches_the_grade_the_probe_saw(tmp_path: Path) -> None:
    """The CI topology end to end: a probe job writes a file, a publishing job grades from it.

    This is the shape the defect lived in. Each half was tested and neither test crossed the
    artifact, so a distinction the probing run held and the file did not carry was invisible to
    both.
    """
    from fhir_scorecard import cli
    from fhir_scorecard.fetch import FetchResult

    capability = json.dumps(good_capability()).encode()

    def answer(url: str, **kwargs: object) -> FetchResult:
        if url.endswith("/metadata"):
            return FetchResult(url, True, 200, 300, capability, None)
        return FetchResult(url, False, 404, 300, b"", "HTTP 404", failure_kind="not_found")

    probes_seen: dict[str, VantageProbe] = {}
    original = cli.fetch_json
    cli.fetch_json = answer  # type: ignore[assignment]
    try:
        probed = cli._grade_endpoint(
            _ENDPOINT,
            offline=False,
            fixtures=None,
            history={},
            today="2026-09-12",
            vantage="github-actions/ubuntu-latest",
            other_probes={},
            probes_seen=probes_seen,
            declared={},
            smart_declared={},
        )
    finally:
        cli.fetch_json = original  # type: ignore[assignment]

    path = tmp_path / "probes-ubuntu-latest.json"
    write_probes(path, "github-actions/ubuntu-latest", probes_seen)
    published = _from_probes(load_probe_files([path])["alpha"])

    assert probed.grade != NOT_OBSERVED
    assert published.grade == probed.grade, (
        f"the publish graded {published.grade!r} from the artifact of a probe that graded "
        f"{probed.grade!r}"
    )


def test_collapsing_two_samples_of_one_vantage_keeps_that_it_asked() -> None:
    """``collapse_by_vantage`` rebuilds the dataclass, so it drops any field it does not name.

    Deleting the ``smart_requested=`` line from its reached branch left all 1,148 tests green
    when this was written. The field would then have read back as ``False`` -- the value that
    suppresses a letter -- for every vantage that reported twice, which is exactly the case the
    collapse exists for.
    """
    from fhir_scorecard.vantage import collapse_by_vantage

    collapsed = collapse_by_vantage(
        [
            _probe("github-actions/ubuntu-latest", smart=None),
            _probe("github-actions/ubuntu-latest", smart=None),
        ]
    )
    assert len(collapsed) == 1
    assert collapsed[0].smart_requested is True

    silent = collapse_by_vantage(
        [
            _probe("foreign/one", smart=None, smart_requested=False),
            _probe("foreign/one", smart=None, smart_requested=False),
        ]
    )
    assert silent[0].smart_requested is False, "the collapse must not invent a request either"


def test_every_probe_field_has_a_stated_fate_in_the_collapse() -> None:
    """A field added to ``VantageProbe`` must be given a decision here, not a default.

    The collapse carries some fields, derives some, and deliberately drops others -- ``error``
    and ``failure_kind`` have nothing to say about a vantage that reached the endpoint. What it
    must never do is acquire a new field silently, because the failure is invisible: the
    dataclass default is a plausible value and no assertion anywhere reads it.
    """
    from dataclasses import fields

    from fhir_scorecard.vantage import collapse_by_vantage

    carried = {"vantage", "reachable", "elapsed_ms", "capability", "smart", "status"}
    dropped_when_reached = {"error", "failure_kind"}
    decided = carried | dropped_when_reached | {"smart_requested"}
    actual = {f.name for f in fields(VantageProbe)}
    assert actual == decided, (
        f"VantageProbe fields without a stated fate in collapse_by_vantage: "
        f"{sorted(actual - decided)}; fields named here but gone from the dataclass: "
        f"{sorted(decided - actual)}"
    )

    # And the carried ones really do survive, rather than being listed above and forgotten.
    rich = VantageProbe(
        vantage="test/one",
        reachable=True,
        elapsed_ms=400,
        capability="{}",
        smart="{}",
        status=200,
        smart_requested=True,
    )
    collapsed = collapse_by_vantage([rich, rich])[0]
    for name in carried - {"elapsed_ms"}:
        assert getattr(collapsed, name) == getattr(rich, name), f"{name} was lost in the collapse"


def test_the_two_sentinels_stay_distinguishable() -> None:
    """Both are ungraded SMART facts and they mean opposite things about this run."""
    assert SMART_NOT_SERVED.observed is True
    assert NO_SMART_RETRIEVED.observed is False
    assert SMART_NOT_SERVED.not_served is True
    assert SMART_NOT_SERVED.parsed is NO_SMART_RETRIEVED.parsed is False


def test_a_page_does_not_say_nothing_was_retrieved_above_what_was_retrieved() -> None:
    """`letter` returns "not observed" for two different states; the page said one thing.

    The second state is an endpoint that answered, whose documents were read, and where one
    check could not be made -- so the weighted score's bounds straddle a band. The published
    sentence for it claimed "no vantage retrieved its public documents", directly above the
    table of resource types drawn from the CapabilityStatement that had been retrieved.
    """
    from fhir_scorecard.capability import parse_capability
    from fhir_scorecard.fetch import FetchResult
    from fhir_scorecard.grading import build_scorecard
    from fhir_scorecard.site import endpoint_page

    # Retrieved, and readable. The SMART document's state is genuinely unknown -- nobody's probe
    # file says whether it was asked for -- so I2 is withheld and the letter cannot be pinned.
    metadata = FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=300,
        body=json.dumps(good_capability()).encode(),
        error=None,
    )
    from fhir_scorecard.capability import NO_SMART_RETRIEVED

    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        metadata,
        parse_capability(metadata.body),
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    assert card.grade == NOT_OBSERVED and card.reachable is True

    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live fetch (recorded 2026-09-12)",
        origin="https://example.test",
    )
    assert "no vantage retrieved its public documents" not in page.body
    assert "cannot pin a single letter" in page.body
    # And the page really does go on to describe the document, which is what made the old
    # sentence false rather than merely vague.
    assert "resource types declared" in page.body
