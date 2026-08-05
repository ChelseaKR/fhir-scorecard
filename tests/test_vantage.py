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
    assert "unreachable from all 2 vantage(s)" in c.detail
    assert "DNS did not resolve" in c.detail


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
