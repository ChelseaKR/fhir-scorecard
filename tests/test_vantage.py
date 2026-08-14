from __future__ import annotations

import json
from pathlib import Path

from fhir_scorecard.vantage import VantageProbe, load_probe_files, reconcile, write_probes


def _p(vantage: str, reachable: bool, ms: int = 100, error: str | None = None) -> VantageProbe:
    return VantageProbe(vantage=vantage, reachable=reachable, elapsed_ms=ms, error=error)


def test_one_reaching_vantage_settles_reachability() -> None:
    """The rule that matters: reaching an endpoint anywhere proves it is up; failing to reach
    it from one network proves only that the network could not get there."""
    c = reconcile([
        _p("home", False, error="TLS certificate verification failed"),
        _p("ci", True, 200),
    ])
    assert c.reachable
    assert not c.unanimous
    assert "failed from home" in c.detail
    assert "property of that network" in c.detail
    assert "TLS certificate" in c.detail


def test_unanimous_failure_is_stated_as_such_with_causes() -> None:
    c = reconcile([_p("home", False, error="DNS did not resolve"),
                   _p("ci", False, error="DNS did not resolve")])
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
        _p("github-actions/ubuntu-latest", True, 300),   # the publishing run's own probe
        _p("github-actions/ubuntu-latest", True, 300),   # the ubuntu artifact, same vantage
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
    c = reconcile([
        _p("github-actions/ubuntu-latest", False, error="TLS certificate verification failed"),
        _p("github-actions/ubuntu-latest", False, error="TLS certificate verification failed"),
        _p("github-actions/macos-latest", True, 500),
    ])
    assert c.vantages == 2 and c.agreeing == 1
    assert "reachable from 1 of 2 vantages" in c.detail


def test_a_duplicated_vantage_that_reached_once_reached() -> None:
    c = reconcile([_p("ci", False, error="connection timed out"), _p("ci", True, 100),
                   _p("ci", True, 300)])
    assert c.vantages == 1 and c.reachable
    assert c.elapsed_ms == 200  # median of the samples that answered, not of the failure


def test_three_runner_images_are_one_network_and_say_so() -> None:
    """Three OS images on one provider share an address space, a reputation, and every rule a
    payer edge applies to it. Calling that three networks is the claim this project cannot make."""
    c = reconcile([_p("github-actions/ubuntu-latest", True, 300),
                   _p("github-actions/macos-latest", True, 700),
                   _p("github-actions/windows-latest", True, 900)])
    assert c.vantages == 3
    assert c.networks == 1
    assert "one network (github-actions)" in c.detail
    assert "not 3 independent networks" in c.detail


def test_total_failure_on_one_network_states_the_limit_rather_than_the_verdict() -> None:
    c = reconcile([_p("github-actions/ubuntu-latest", False, error="connection timed out"),
                   _p("github-actions/macos-latest", False, error="connection timed out"),
                   _p("github-actions/windows-latest", False, error="connection timed out")])
    assert not c.reachable and c.networks == 1
    assert "all on one network (github-actions)" in c.detail
    assert "cannot separate an endpoint that is down" in c.detail


def test_distinct_networks_are_reported_as_distinct() -> None:
    c = reconcile([_p("github-actions/ubuntu-latest", True, 300),
                   _p("davis-ca/residential", True, 500)])
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
    merged = load_probe_files([tmp_path / "bad.json", tmp_path / "empty.json",
                               tmp_path / "good.json", tmp_path / "missing.json"])
    assert list(merged) == ["alpha"]
    assert reconcile(merged["alpha"]).reachable


def test_consensus_borrows_documents_from_a_reaching_vantage() -> None:
    """Establishing an endpoint is up while scoring its content zero would report an F for
    material the probing vantage never received: the original mistake in a new costume."""
    blocked = VantageProbe("home", False, 0, "TLS certificate verification failed")
    reached = VantageProbe("ci", True, 640, capability='{"resourceType":"CapabilityStatement"}',
                           smart='{"token_endpoint":"https://x.test/t"}')
    c = reconcile([blocked, reached])
    assert c.reachable
    assert c.capability == '{"resourceType":"CapabilityStatement"}'
    assert c.smart == '{"token_endpoint":"https://x.test/t"}'


def test_no_documents_to_borrow_leaves_them_unset() -> None:
    c = reconcile([VantageProbe("a", True, 10), VantageProbe("b", False, 0, "boom")])
    assert c.reachable and c.capability is None
