from __future__ import annotations

import json
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.report import render_html, to_json


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"endpoints": [
        {"id": "alpha", "name": "Alpha Reference", "kind": "reference",
         "base_url": "https://alpha.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-04"}},
        {"id": "beta-dark", "name": "Beta (no fixture)", "kind": "payer",
         "base_url": "https://beta.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-04"}},
    ]}))
    return path


def test_cli_offline_end_to_end(tmp_path: Path, capsys: object) -> None:
    fixtures = tmp_path / "fixtures" / "alpha"
    fixtures.mkdir(parents=True)
    (fixtures / "metadata.json").write_text(json.dumps(good_capability()))
    (fixtures / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"

    history = tmp_path / "history.json"
    args = ["grade", "--registry", str(_registry(tmp_path)), "--offline",
            "--fixtures", str(tmp_path / "fixtures"), "--out", str(out),
            "--history", str(history)]
    assert main(args) == 0

    payload = json.loads((out / "scorecards.json").read_text())
    grades = {s["endpoint_id"]: s["grade"] for s in payload["scorecards"]}
    assert grades["alpha"] == "A"
    assert grades["beta-dark"] == "F"  # missing fixture = unreachable = fail closed
    assert "disclaimer" in payload

    home = (out / "index.html").read_text()
    assert 'lang="en"' in home and "FHIR Scorecard" in home
    alpha_page = (out / "endpoint" / "alpha" / "index.html").read_text()
    assert "Alpha Reference" in alpha_page

    # Drift history persists across runs; a capability change surfaces in the next report.
    assert json.loads(history.read_text())["alpha"]["first_seen"]
    changed = good_capability()
    changed["software"] = {"name": "SyntheticServer", "version": "10.0.0"}
    (fixtures / "metadata.json").write_text(json.dumps(changed))
    assert main(args) == 0
    alpha = next(s for s in json.loads((out / "scorecards.json").read_text())["scorecards"]
                 if s["endpoint_id"] == "alpha")
    assert any("software_version" in e for e in alpha["drift_events"])
    assert "Declared capability changes" in (
        out / "endpoint" / "alpha" / "index.html").read_text()


def test_cli_offline_requires_fixtures(tmp_path: Path) -> None:
    assert main(["grade", "--registry", str(_registry(tmp_path)), "--offline"]) == 2


def test_cli_bad_registry_is_error(tmp_path: Path) -> None:
    bad = tmp_path / "registry.json"
    bad.write_text(json.dumps({"endpoints": [{"id": "x"}]}))
    assert main(["grade", "--registry", str(bad), "--offline",
                 "--fixtures", str(tmp_path)]) == 2


def test_report_escapes_html() -> None:
    card = build_scorecard(
        "evil", "<script>alert(1)</script>",
        FetchResult(url="https://x.test/metadata", ok=True, status=200, elapsed_ms=10,
                    body=b"", error=None),
        parse_capability(b""), parse_smart(b""))
    html_out = render_html([card], generated_at="2026-08-04")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_json_report_round_trips() -> None:
    card = build_scorecard(
        "x", "X",
        FetchResult(url="https://x.test/metadata", ok=False, status=None, elapsed_ms=0,
                    body=b"", error="URLError"),
        parse_capability(b""), parse_smart(b""))
    payload = json.loads(to_json([card], generated_at="2026-08-04"))
    assert payload["scorecards"][0]["grade"] == "F"
    assert payload["generator"] == "fhir-scorecard"


def test_report_groups_by_kind_and_never_ranks_across() -> None:
    """Grades are only comparable within a kind, so each kind gets its own table."""
    from fhir_scorecard.fetch import FetchResult as FR

    def card(eid: str, kind: str):
        return build_scorecard(
            eid, eid.title(),
            FR(url="https://x.test/metadata", ok=True, status=200, elapsed_ms=10,
               body=b"", error=None),
            parse_capability(json.dumps(good_capability()).encode()),
            parse_smart(json.dumps(good_smart()).encode()), kind=kind)

    html_out = render_html([card("aetna", "payer"), card("epic", "ehr")],
                           generated_at="2026-08-05")
    assert "Payer Patient Access APIs (1)" in html_out
    assert "EHR vendor sandboxes (1)" in html_out
    # Two separate tables, not one merged ranking.
    assert html_out.count("<table>") == 2


def test_vantage_recorded_in_outputs(tmp_path: Path) -> None:
    """Latency is single-vantage; the run must say where it measured from."""
    fixtures = tmp_path / "fixtures" / "alpha"
    fixtures.mkdir(parents=True)
    (fixtures / "metadata.json").write_text(json.dumps(good_capability()))
    (fixtures / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"
    assert main(["grade", "--registry", str(_registry(tmp_path)), "--offline",
                 "--fixtures", str(tmp_path / "fixtures"), "--out", str(out),
                 "--history", str(tmp_path / "h.json"),
                 "--vantage", "github-actions/ubuntu-latest"]) == 0
    payload = json.loads((out / "scorecards.json").read_text())
    assert payload["vantage"] == "github-actions/ubuntu-latest"
    alpha = next(s for s in payload["scorecards"] if s["endpoint_id"] == "alpha")
    r2 = next(f for d in alpha["dimensions"] for f in d["findings"] if f["code"] == "R2")
    assert "github-actions/ubuntu-latest" in r2["message"]
    assert "github-actions/ubuntu-latest" in (
        out / "endpoint" / "alpha" / "index.html").read_text()


def test_recheck_reports_without_touching_registry(tmp_path: Path, monkeypatch) -> None:
    """A candidate that starts answering is reported, never auto-promoted: verification means
    confirming the publisher, which a fetch cannot decide."""
    from fhir_scorecard import cli as cli_mod
    from fhir_scorecard.fetch import FetchResult as FR

    path = tmp_path / "rejected.json"
    path.write_text(json.dumps({"rejected": [
        {"id": "revived", "name": "Revived Plan", "base_url": "https://a.test/r4",
         "last_outcome": "HTTP 404"},
        {"id": "still-dead", "name": "Still Dead", "base_url": "https://b.test/r4",
         "last_outcome": "DNS"},
    ]}))

    def fake_fetch(url: str, **kwargs: object) -> FR:
        if url.startswith("https://a.test"):
            return FR(url=url, ok=True, status=200, elapsed_ms=5,
                      body=json.dumps(good_capability()).encode(), error=None)
        return FR(url=url, ok=False, status=None, elapsed_ms=0, body=b"", error="URLError")

    monkeypatch.setattr("fhir_scorecard.reprobe.fetch_json", fake_fetch)
    assert cli_mod.main(["recheck", "--candidates", str(path)]) == 0


def test_recheck_bad_candidates_file_is_error(tmp_path: Path) -> None:
    from fhir_scorecard import cli as cli_mod
    path = tmp_path / "rejected.json"
    path.write_text(json.dumps({"rejected": [{"id": "x"}]}))
    assert cli_mod.main(["recheck", "--candidates", str(path)]) == 2


def test_blocked_vantage_grades_on_borrowed_documents(tmp_path: Path) -> None:
    """End to end: the local vantage cannot reach the endpoint, a peer vantage can, and the
    result is a real grade rather than an F for documents we never received."""
    from fhir_scorecard.vantage import VantageProbe, write_probes

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"endpoints": [
        {"id": "blocked", "name": "Blocked Health", "kind": "payer",
         "base_url": "https://blocked.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-05"}}]}))

    peer = tmp_path / "peer.json"
    write_probes(peer, "peer-vantage", {"blocked": VantageProbe(
        "peer-vantage", True, 400,
        capability=json.dumps(good_capability()), smart=json.dumps(good_smart()))})

    out = tmp_path / "site"
    # No fixture directory for "blocked", so the local probe fails.
    assert main(["grade", "--registry", str(registry), "--offline",
                 "--fixtures", str(tmp_path / "none"), "--out", str(out),
                 "--history", str(tmp_path / "h.json"),
                 "--probes-in", str(peer), "--vantage", "local"]) == 0

    detail = json.loads((out / "api" / "endpoint" / "blocked.json").read_text())
    assert detail["endpoint"]["reachable"] == "true"
    assert detail["endpoint"]["grade"] != "F"
    assert int(detail["endpoint"]["transparency_score"]) > 0


def test_from_probes_grades_without_probing_and_counts_each_vantage_once(
        tmp_path: Path, monkeypatch) -> None:
    """The publishing run has three vantages' documents in hand and nothing left to observe.

    It used to probe anyway, under a label one of the artifacts already carried, so every card
    reported one more vantage than had reported and the extra sample skewed the median latency.
    """
    from fhir_scorecard.vantage import VantageProbe, write_probes

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("--from-probes must not make a request of its own")

    monkeypatch.setattr("fhir_scorecard.cli.fetch_json", no_network)

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"endpoints": [
        {"id": "alpha", "name": "Alpha Health", "kind": "payer",
         "base_url": "https://alpha.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-06"}}]}))

    documents = {"capability": json.dumps(good_capability()), "smart": json.dumps(good_smart())}
    ubuntu = tmp_path / "probes-ubuntu.json"
    macos = tmp_path / "probes-macos.json"
    write_probes(ubuntu, "github-actions/ubuntu-latest", {"alpha": VantageProbe(
        "github-actions/ubuntu-latest", True, 300, **documents)})
    write_probes(macos, "github-actions/macos-latest", {"alpha": VantageProbe(
        "github-actions/macos-latest", True, 900, **documents)})

    out = tmp_path / "site"
    assert main(["grade", "--registry", str(registry), "--out", str(out),
                 "--history", str(tmp_path / "h.json"), "--from-probes",
                 "--probes-in", str(ubuntu), str(macos)]) == 0

    payload = json.loads((out / "scorecards.json").read_text())
    alpha = payload["scorecards"][0]
    assert alpha["grade"] == "A"  # graded on the documents the vantages retrieved
    r2 = next(f for d in alpha["dimensions"] for f in d["findings"] if f["code"] == "R2")
    assert "median across 2 reachable vantages on one network" in r2["message"]
    assert "3 reachable" not in r2["message"]
    assert alpha["vantage_note"].startswith("reachable from all 2 vantages")
    # The run reports the vantages that reported to it, never itself as a vantage.
    assert payload["vantage"] == ("reconciled from github-actions/macos-latest, "
                                  "github-actions/ubuntu-latest")


def test_from_probes_refuses_to_invent_a_vantage(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert main(["grade", "--registry", str(registry), "--out", str(tmp_path / "s"),
                 "--history", str(tmp_path / "h.json"), "--from-probes"]) == 2
    probe = tmp_path / "peer.json"
    probe.write_text(json.dumps({"vantage": "peer", "probes": {}}))
    assert main(["grade", "--registry", str(registry), "--out", str(tmp_path / "s"),
                 "--history", str(tmp_path / "h.json"), "--from-probes",
                 "--probes-in", str(probe), "--probes-out", str(tmp_path / "out.json")]) == 2


def test_from_probes_with_no_report_for_an_endpoint_is_not_a_measurement(
        tmp_path: Path, monkeypatch) -> None:
    """A vantage file that says nothing about an endpoint must not become a reachability fact."""
    from fhir_scorecard.vantage import VantageProbe, write_probes

    monkeypatch.setattr("fhir_scorecard.cli.fetch_json", lambda *a, **k: None)
    peer = tmp_path / "peer.json"
    write_probes(peer, "peer", {"alpha": VantageProbe(
        "peer", True, 100, capability=json.dumps(good_capability()),
        smart=json.dumps(good_smart()))})
    out = tmp_path / "site"
    assert main(["grade", "--registry", str(_registry(tmp_path)), "--out", str(out),
                 "--history", str(tmp_path / "h.json"), "--from-probes",
                 "--probes-in", str(peer)]) == 0
    cards = {s["endpoint_id"]: s for s in json.loads(
        (out / "scorecards.json").read_text())["scorecards"]}
    assert cards["alpha"]["reachable"] is True
    assert cards["beta-dark"]["reachable"] is False
    r1 = next(f for d in cards["beta-dark"]["dimensions"] for f in d["findings"]
              if f["code"] == "R1")
    assert "no vantage reported" in r1["message"]


def test_probes_out_records_this_runs_observations(tmp_path: Path) -> None:
    from fhir_scorecard.vantage import load_probe_files

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"endpoints": [
        {"id": "alpha", "name": "Alpha", "kind": "payer", "base_url": "https://a.test/r4",
         "verification": {"method": "fixture", "date": "2026-08-05"}}]}))
    d = tmp_path / "fixtures" / "alpha"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(good_capability()))
    probes = tmp_path / "probes.json"
    assert main(["grade", "--registry", str(registry), "--offline",
                 "--fixtures", str(tmp_path / "fixtures"), "--out", str(tmp_path / "site"),
                 "--history", str(tmp_path / "h.json"),
                 "--probes-out", str(probes), "--vantage", "runner-1"]) == 0
    loaded = load_probe_files([probes])
    assert loaded["alpha"][0].vantage == "runner-1"
    assert loaded["alpha"][0].reachable
    assert loaded["alpha"][0].capability  # documents carried for peers to borrow
