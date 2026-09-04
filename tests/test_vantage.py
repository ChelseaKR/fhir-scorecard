from __future__ import annotations

import json
from pathlib import Path

from fhir_scorecard.vantage import VantageProbe, load_probe_files, reconcile, write_probes


def _p(vantage: str, reachable: bool, ms: int = 100, error: str | None = None) -> VantageProbe:
    return VantageProbe(vantage=vantage, reachable=reachable, elapsed_ms=ms, error=error)


def test_one_reaching_vantage_settles_reachability() -> None:
    """The rule that matters: reaching an endpoint anywhere proves it is up; failing to reach
    it from one network proves only that this run did not get there from there."""
    c = reconcile(
        [
            _p("home", False, error="TLS certificate verification failed"),
            _p("ci", True, 200),
        ]
    )
    assert c.reachable
    assert not c.unanimous
    assert "not reached from home" in c.detail
    assert "TLS certificate" in c.detail


def test_a_failed_vantage_is_named_without_being_blamed() -> None:
    """Which vantage failed and why, and no claim about whose fault it was.

    The sentence used to close "which is a property of that network rather than of the
    endpoint". A 403, a 429 or a geo rule is the endpoint's policy toward that source, so the
    clause asserted the opposite of the truth in exactly the cases it was most confident. The
    module's own asymmetry forbids it: one vantage failing proves nothing, which includes
    proving who caused the failure.
    """
    c = reconcile([_p("home", False, error="HTTP 403"), _p("ci", True, 200)])
    assert "property of that network" not in c.detail
    assert "rather than of the endpoint" not in c.detail
    assert "not reached from home (HTTP 403)" in c.detail


def test_unanimous_failure_is_stated_as_such_with_causes() -> None:
    c = reconcile(
        [
            _p("home", False, error="DNS did not resolve"),
            _p("ci", False, error="DNS did not resolve"),
        ]
    )
    assert not c.reachable
    assert c.agreeing == 0
    assert "not reached from any of the 2 vantages tried" in c.detail
    assert "DNS did not resolve" in c.detail
    # Two labels with no network segment are two networks, so this is not the one-network case.
    assert c.networks == 2


def test_no_vantage_is_counted_twice() -> None:
    """CI merged the publishing run's own probe with the artifact written under the same label,
    and every card then said "reachable from all 4 vantage(s)" when three vantages reported."""
    ci_shaped = [
        _p("github-actions/ubuntu-latest", True, 300),  # the publishing run's own probe
        _p("github-actions/ubuntu-latest", True, 300),  # the ubuntu artifact, same vantage
        _p("github-actions/macos-latest", True, 700),
        _p("github-actions/windows-latest", True, 900),
    ]
    c = reconcile(ci_shaped)
    assert c.vantages == 3
    assert c.agreeing == 3
    assert "4" not in c.detail
    # The duplicate also gave one network path double weight in a median whose bands are 3s/8s.
    assert c.elapsed_ms == 700


def test_a_duplicated_vantage_failing_is_one_failure_not_two() -> None:
    """The asymmetry cuts the other way too: if the doubled vantage is the blocked one, it must
    not read as two independent networks failing."""
    c = reconcile(
        [
            _p("github-actions/ubuntu-latest", False, error="TLS certificate verification failed"),
            _p("github-actions/ubuntu-latest", False, error="TLS certificate verification failed"),
            _p("github-actions/macos-latest", True, 500),
        ]
    )
    assert c.vantages == 2 and c.agreeing == 1
    assert "reachable from 1 of 2 vantages" in c.detail


def test_a_duplicated_vantage_that_reached_once_reached() -> None:
    c = reconcile(
        [_p("ci", False, error="connection timed out"), _p("ci", True, 100), _p("ci", True, 300)]
    )
    assert c.vantages == 1 and c.reachable
    assert c.elapsed_ms == 200  # median of the samples that answered, not of the failure


def test_three_runner_images_are_one_network_and_say_so() -> None:
    """Three OS images on one provider share an address space, a reputation, and every rule a
    payer edge applies to it. Calling that three networks is the claim this project cannot make."""
    c = reconcile(
        [
            _p("github-actions/ubuntu-latest", True, 300),
            _p("github-actions/macos-latest", True, 700),
            _p("github-actions/windows-latest", True, 900),
        ]
    )
    assert c.vantages == 3
    assert c.networks == 1
    assert "one network (github-actions)" in c.detail
    assert "not 3 independent networks" in c.detail


def test_total_failure_on_one_network_states_the_limit_rather_than_the_verdict() -> None:
    c = reconcile(
        [
            _p("github-actions/ubuntu-latest", False, error="connection timed out"),
            _p("github-actions/macos-latest", False, error="connection timed out"),
            _p("github-actions/windows-latest", False, error="connection timed out"),
        ]
    )
    assert not c.reachable and c.networks == 1
    assert "all on one network (github-actions)" in c.detail
    assert "cannot separate an endpoint that is down" in c.detail


def test_distinct_networks_are_reported_as_distinct() -> None:
    c = reconcile(
        [_p("github-actions/ubuntu-latest", True, 300), _p("davis-ca/residential", True, 500)]
    )
    assert c.networks == 2
    assert "across 2 networks" in c.detail


def test_median_latency_ignores_one_slow_path() -> None:
    c = reconcile([_p("a", True, 100), _p("b", True, 200), _p("c", True, 9000)])
    assert c.elapsed_ms == 200
    assert c.unanimous


def test_even_number_of_vantages_averages_the_middle() -> None:
    assert reconcile([_p("a", True, 100), _p("b", True, 300)]).elapsed_ms == 200


def test_single_vantage_still_works() -> None:
    c = reconcile([_p("only", True, 50)])
    assert c.reachable and c.vantages == 1 and "reachable from only" in c.detail


def test_no_probes_fails_closed() -> None:
    c = reconcile([])
    assert not c.reachable and "no vantage reported" in c.detail


def test_probe_files_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "probes-ci.json"
    write_probes(path, "ci", {"alpha": _p("ci", True, 120)})
    loaded = load_probe_files([path])
    assert loaded["alpha"][0].vantage == "ci"
    assert loaded["alpha"][0].elapsed_ms == 120


def test_merge_across_vantages(tmp_path: Path) -> None:
    write_probes(tmp_path / "a.json", "home", {"alpha": _p("home", False, error="TLS")})
    write_probes(tmp_path / "b.json", "ci", {"alpha": _p("ci", True, 90)})
    merged = load_probe_files([tmp_path / "a.json", tmp_path / "b.json"])
    assert len(merged["alpha"]) == 2
    assert reconcile(merged["alpha"]).reachable


def test_broken_vantage_file_degrades_consensus_not_the_run(tmp_path: Path) -> None:
    """Losing one vantage should cost you that vantage, not the whole merge."""
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "empty.json").write_text(json.dumps({"vantage": "x"}))
    write_probes(tmp_path / "good.json", "ci", {"alpha": _p("ci", True, 90)})
    merged = load_probe_files(
        [
            tmp_path / "bad.json",
            tmp_path / "empty.json",
            tmp_path / "good.json",
            tmp_path / "missing.json",
        ]
    )
    assert list(merged) == ["alpha"]
    assert reconcile(merged["alpha"]).reachable


def test_consensus_borrows_documents_from_a_reaching_vantage() -> None:
    """Establishing an endpoint is up while scoring its content zero would report an F for
    material the probing vantage never received: the original mistake in a new costume."""
    blocked = VantageProbe("home", False, 0, "TLS certificate verification failed")
    reached = VantageProbe(
        "ci",
        True,
        640,
        capability='{"resourceType":"CapabilityStatement"}',
        smart='{"token_endpoint":"https://x.test/t"}',
    )
    c = reconcile([blocked, reached])
    assert c.reachable
    assert c.capability == '{"resourceType":"CapabilityStatement"}'
    assert c.smart == '{"token_endpoint":"https://x.test/t"}'


def test_no_documents_to_borrow_leaves_them_unset() -> None:
    c = reconcile([VantageProbe("a", True, 10), VantageProbe("b", False, 0, "boom")])
    assert c.reachable and c.capability is None


def test_an_empty_but_retrieved_document_is_not_treated_as_no_document() -> None:
    """A vantage that reached the endpoint and got back an empty body has retrieved a document,
    just an empty one - a different fact from a vantage that retrieved nothing. Gating on
    truthiness instead of ``is not None`` conflated the two and let a genuinely empty response
    fall back to "no document was borrowed", which downstream read as nothing having been
    retrieved at all."""
    c = reconcile(
        [
            VantageProbe("a", True, 10, capability="", smart=""),
            VantageProbe("b", False, 0, "boom"),
        ]
    )
    assert c.reachable
    assert c.capability == ""
    assert c.smart == ""


def test_duplicate_vantage_with_empty_body_keeps_it_rather_than_losing_it() -> None:
    """Two samples of the same vantage, both genuinely empty, must collapse to an empty
    document, not to no document."""
    c = reconcile(
        [
            VantageProbe("ci", True, 100, capability=""),
            VantageProbe("ci", True, 100, capability=""),
        ]
    )
    assert c.reachable
    assert c.capability == ""


def test_an_endpoint_that_answered_is_never_described_as_possibly_down() -> None:
    """The founding incident with its sign reversed, which is how it nearly recurred.

    ``reachable`` is 2xx-only, so an endpoint answering HTTP 415 from every vantage - the real
    Capital Blue Cross shape - fell into the all-failed branch and published "this run cannot
    separate an endpoint that is down from one that does not answer this network", quoting the
    status that disproves the sentence. A server that returns 415 completed DNS, TCP, TLS and
    HTTP. It is running and refusing this request.
    """
    c = reconcile(
        [
            VantageProbe("github-actions/ubuntu", False, 120, error="HTTP 415", status=415),
            VantageProbe("github-actions/macos", False, 130, error="HTTP 415", status=415),
        ]
    )
    assert not c.reachable, "no document was retrieved, so nothing content-bearing is claimed"
    assert c.answered == 2
    assert "answered HTTP 415" in c.detail
    assert "running and refusing this request" in c.detail
    assert "cannot separate an endpoint that is down" not in c.detail


def test_a_genuine_all_vantage_failure_still_says_it_cannot_separate_the_two() -> None:
    """The clause is right when nothing answered; it was only wrong when something did."""
    c = reconcile(
        [
            VantageProbe("github-actions/ubuntu", False, 0, error="DNS did not resolve"),
            VantageProbe("github-actions/macos", False, 0, error="DNS did not resolve"),
        ]
    )
    assert c.answered == 0
    assert "cannot separate an endpoint that is down" in c.detail


def test_a_re_rendered_document_is_not_a_disagreement() -> None:
    """Compared on what a document declares, not on its bytes.

    Byte comparison shipped for one publish and called 19 of 45 live endpoints disagreeing in a
    single run - including three-of-three unique documents from a reference server that plainly
    does not serve three different declarations. A generation timestamp, a request id, or a
    differently-ordered dict is the server re-rendering, which is exactly what `drift.py`
    fingerprints declared facts to ignore: "a server that merely re-renders its
    CapabilityStatement does not read as changed". The same has to hold across vantages.
    """
    import json as _json

    def doc(**over: object) -> str:
        d: dict[str, object] = {
            "resourceType": "CapabilityStatement",
            "fhirVersion": "4.0.1",
            "software": {"name": "S", "version": "1.0"},
            "rest": [
                {
                    "mode": "server",
                    "resource": [{"type": "Patient", "interaction": [{"code": "read"}]}],
                }
            ],
        }
        d.update(over)
        return _json.dumps(d)

    stamped_a, stamped_b = doc(date="2026-09-04T16:19:00Z"), doc(date="2026-09-04T16:19:31Z")
    reordered = _json.dumps(_json.loads(stamped_a), sort_keys=True)
    c = reconcile(
        [
            VantageProbe("gh/ubuntu", True, 100, capability=stamped_a),
            VantageProbe("gh/macos", True, 100, capability=stamped_b),
            VantageProbe("gh/windows", True, 100, capability=reordered),
        ]
    )
    assert c.declaration_disagreement is None
    assert "different declarations" not in c.detail

    # And a real difference is still caught: the same server one version apart.
    real = reconcile(
        [
            VantageProbe("gh/ubuntu", True, 100, capability=doc()),
            VantageProbe("gh/macos", True, 100, capability=doc()),
            VantageProbe(
                "gh/windows", True, 100, capability=doc(software={"name": "S", "version": "2.0"})
            ),
        ]
    )
    assert real.declaration_disagreement is not None
    assert "2 different declarations" in real.declaration_disagreement
    assert _json.loads(real.capability or "{}")["software"]["version"] == "1.0"


def test_when_no_two_vantages_agree_the_note_says_so() -> None:
    """ "the one 1 agreed on is graded" was the sentence this produced, which is not English
    and overstates besides: nothing was agreed."""
    one, two, three = '{"a":1}', '{"a":2}', '{"a":3}'
    c = reconcile(
        [
            VantageProbe("c/1", True, 100, capability=three),
            VantageProbe("a/1", True, 100, capability=one),
            VantageProbe("b/1", True, 100, capability=two),
        ]
    )
    assert c.declaration_disagreement is not None
    assert "no two agreed" in c.declaration_disagreement
    assert c.capability == one, "the first by vantage name, deterministically"


def test_the_graded_declaration_is_the_one_most_vantages_returned() -> None:
    """Selection used to be first-in-list-order, and list order is ``probes/*.json`` glob order.

    A vantage whose network answers with a 200 interstitial sorts before a vantage holding the
    real CapabilityStatement purely on filename, and then defines the grade, every finding, and
    the drift fingerprint. Majority instead, and the disagreement is recorded rather than
    silently discarded - across vantages in one run it is the same fact the alternation rule
    already tells over time.
    """
    real = '{"resourceType":"CapabilityStatement"}'
    interstitial = "<html>blocked by proxy</html>"
    c = reconcile(
        [
            VantageProbe("aaa-proxy/1", True, 100, capability=interstitial),
            VantageProbe("mmm-good/1", True, 100, capability=real),
            VantageProbe("zzz-good/1", True, 100, capability=real),
        ]
    )
    assert c.capability == real
    assert c.declaration_disagreement is not None
    assert "3 different" not in c.declaration_disagreement
    assert "2 different declarations" in c.declaration_disagreement
    assert "aaa-proxy/1" in c.declaration_disagreement


def test_agreeing_vantages_record_no_disagreement() -> None:
    real = '{"resourceType":"CapabilityStatement"}'
    c = reconcile(
        [
            VantageProbe("a/1", True, 100, capability=real),
            VantageProbe("b/1", True, 100, capability=real),
        ]
    )
    assert c.capability == real
    assert c.declaration_disagreement is None


def test_a_tie_between_declarations_is_broken_deterministically() -> None:
    """Two backends, one from each vantage: the result must not depend on argument order."""
    one, two = '{"a":1}', '{"a":2}'
    first = reconcile(
        [
            VantageProbe("a/1", True, 100, capability=one),
            VantageProbe("b/1", True, 100, capability=two),
        ]
    )
    second = reconcile(
        [
            VantageProbe("b/1", True, 100, capability=two),
            VantageProbe("a/1", True, 100, capability=one),
        ]
    )
    assert first.capability == second.capability == one
    assert first.declaration_disagreement == second.declaration_disagreement


def test_the_smart_document_is_borrowed_independently_of_the_capability() -> None:
    """It used to come from whichever probe supplied the CapabilityStatement.

    So a vantage-local block on /.well-known discarded a peer's complete SMART document and
    published "absent or incomplete" about a named payer - up to 35 of 100 interop points,
    wider than a letter band, decided by which probe happened to be first in the list.
    ``collapse_by_vantage`` already did this correctly six lines away.
    """
    c = reconcile(
        [
            VantageProbe("has-capability/1", True, 100, capability='{"a":1}', smart=None),
            VantageProbe("has-smart/1", True, 100, capability=None, smart='{"token_endpoint":"x"}'),
        ]
    )
    assert c.capability == '{"a":1}'
    assert c.smart == '{"token_endpoint":"x"}'


def test_networks_are_counted_over_the_vantages_that_reached_the_endpoint() -> None:
    """A median over one vantage was published as "across 3 networks", which describes a breadth
    of agreement the number does not have."""
    c = reconcile(
        [
            VantageProbe("alpha/1", True, 500, capability="{}"),
            VantageProbe("beta/1", False, 0, error="HTTP 403"),
            VantageProbe("gamma/1", False, 0, error="HTTP 403"),
        ]
    )
    assert c.agreeing == 1
    assert c.networks == 1, "one reachable vantage sits on one network, whatever the others saw"


def test_a_probe_file_written_before_status_existed_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    path.write_text(
        json.dumps(
            {
                "vantage": "old/1",
                "probes": {"x": {"vantage": "old/1", "reachable": True, "elapsed_ms": 10}},
            }
        )
    )
    loaded = load_probe_files([path])
    assert loaded["x"][0].status is None
    assert loaded["x"][0].reachable


def test_a_disagreement_between_vantages_is_published_not_merely_recorded() -> None:
    """A reader looking at a grade derived from one of two declarations is entitled to know
    the other existed."""
    c = reconcile(
        [
            VantageProbe("a/1", True, 100, capability='{"v":1}'),
            VantageProbe("b/1", True, 100, capability='{"v":2}'),
        ]
    )
    assert c.declaration_disagreement is not None
    assert c.declaration_disagreement in c.detail
    assert "different declarations" in c.detail
