"""The single-endpoint check and the composite Action that packages it.

Two properties are load-bearing here and neither is obvious from reading the YAML.

The gate must be able to fail. A published Action whose failure path has never run is a
decoration, so the tests below drive a deliberately bad input all the way through the same code
the Action calls and assert the non-zero exit, and then assert the shell keeps it: the result
renderer always runs, and the step still exits with the checker's code.

The gate must fail *honestly*. ``not observed`` and ``F`` are different values in this project
(:mod:`fhir_scorecard.grading`), and the whole point of splitting them was that an endpoint
nobody reached is not a finding about what that endpoint publishes. The Action's outputs, its
job summary, and its annotation are three new places that split could quietly be undone, so
each is asserted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from conftest import good_capability, good_smart

from fhir_scorecard import cli
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import NOT_OBSERVED

ROOT = Path(__file__).resolve().parent.parent
ACTION_YML = ROOT / "action.yml"
RENDER = ROOT / "action" / "render_result.py"

BASE = "https://endpoint.test/fhir"

# Retrieved, and poor: a CapabilityStatement with nothing in it. Every dimension is observed,
# so this is a genuine F rather than an absence wearing an F's clothes.
BARE_CAPABILITY: dict[str, object] = {
    "resourceType": "CapabilityStatement",
    "fhirVersion": "4.0.1",
    "rest": [],
}


def _ok(url: str, body: dict[str, object]) -> FetchResult:
    return FetchResult(
        url=url,
        ok=True,
        status=200,
        elapsed_ms=120,
        body=json.dumps(body).encode("utf-8"),
        error=None,
    )


def _dead(url: str, error: str) -> FetchResult:
    return FetchResult(url=url, ok=False, status=None, elapsed_ms=0, body=b"", error=error)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    capability: dict[str, object] | None,
    smart: dict[str, object] | None = None,
    *,
    error: str = "connection refused",
) -> None:
    """Answer this run's two requests without touching the network."""

    def fake(url: str, **_: Any) -> FetchResult:
        if url.endswith("/metadata"):
            return _ok(url, capability) if capability is not None else _dead(url, error)
        return _ok(url, smart) if smart is not None else _dead(url, "HTTP 404")

    monkeypatch.setattr("fhir_scorecard.cli.fetch_json", fake)


def _action_text() -> str:
    return ACTION_YML.read_text(encoding="utf-8")


def _block(name: str) -> str:
    """One top-level mapping of ``action.yml``, as text.

    Scanned rather than parsed, for the same reason ``test_workflow_shell_safety`` scans the
    workflows: a YAML parser would be a dependency added for the tests alone.
    """
    text = _action_text()
    start = text.index(f"\n{name}:\n") + len(name) + 2
    rest = text[start:]
    end = re.search(r"^\S", rest[1:], re.MULTILINE)
    return rest[: end.start() + 1] if end else rest


def _keys(block: str) -> set[str]:
    return set(re.findall(r"^  ([a-z][a-z-]*):\s*$", block, re.MULTILINE))


def _gate_step_run() -> str:
    """The Action's one shell block."""
    return _action_text().split("run: |", 1)[1]


class TestTheGateCanFail:
    def test_a_measured_grade_below_the_threshold_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The deliberately bad input: a document that was retrieved and is empty."""
        _serve(monkeypatch, BARE_CAPABILITY)
        out = tmp_path / "result.json"
        code = cli.main(["check", BASE, "--min-grade", "B", "--json-out", str(out)])
        assert code == 1
        assert json.loads(out.read_text())["scorecards"][0]["grade"] == "F"

    def test_the_same_input_passes_when_the_caller_set_no_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A finding is data. Without a threshold the caller asked for, it is not a failure."""
        _serve(monkeypatch, BARE_CAPABILITY)
        assert cli.main(["check", BASE]) == 0

    def test_a_grade_at_the_threshold_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, good_capability(), good_smart())
        assert cli.main(["check", BASE, "--min-grade", "A", "--kind", "payer"]) == 0

    def test_the_result_is_written_before_the_threshold_is_applied(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A failing gate is exactly when the evidence is wanted."""
        _serve(monkeypatch, BARE_CAPABILITY)
        out = tmp_path / "nested" / "result.json"
        assert cli.main(["check", BASE, "--min-grade", "A", "--json-out", str(out)]) == 1
        payload = json.loads(out.read_text())
        assert payload["disclaimer"].startswith("Observational snapshot")
        assert payload["scorecards"][0]["dimensions"]

    def test_a_non_https_base_url_is_an_input_error_not_a_gate_failure(self) -> None:
        """Exit 2 keeps its existing meaning: the caller gave the tool something it cannot use."""
        assert cli.main(["check", "http://endpoint.test/fhir", "--min-grade", "A"]) == 2

    def test_an_unwritable_result_path_is_an_input_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _serve(monkeypatch, good_capability(), good_smart())
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        assert cli.main(["check", BASE, "--json-out", str(blocker / "result.json")]) == 2


class TestTheGateFailsHonestly:
    def test_an_endpoint_nobody_reached_is_not_reported_as_f(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _serve(monkeypatch, None, error="DNS did not resolve (nodename nor servname provided)")
        out = tmp_path / "result.json"
        code = cli.main(["check", BASE, "--min-grade", "B", "--json-out", str(out)])
        captured = capsys.readouterr()

        assert code == 1
        assert json.loads(out.read_text())["scorecards"][0]["grade"] == NOT_OBSERVED
        assert "could not be evaluated" in captured.err
        assert "DNS did not resolve" in captured.err
        assert "grade F" not in captured.err and "below" not in captured.err

    def test_an_endpoint_nobody_reached_passes_when_no_threshold_was_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing was asked of the run, so nothing about it can have failed."""
        _serve(monkeypatch, None)
        assert cli.main(["check", BASE]) == 0

    def test_the_check_names_the_host_rather_than_an_organization(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A check has no verification record, so it puts no name behind an address."""
        _serve(monkeypatch, good_capability(), good_smart())
        out = tmp_path / "result.json"
        cli.main(["check", BASE, "--json-out", str(out)])
        card = json.loads(out.read_text())["scorecards"][0]
        assert card["name"] == "endpoint.test"
        assert card["endpoint_id"] == "endpoint-test"

    def test_the_check_claims_no_availability_from_one_observation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _serve(monkeypatch, good_capability(), good_smart())
        out = tmp_path / "result.json"
        cli.main(["check", BASE, "--json-out", str(out)])
        card = json.loads(out.read_text())["scorecards"][0]
        assert card["availability"] == ""
        assert card["observed_since"] is None
        assert card["drift_events"] == []

    def test_the_check_writes_nothing_but_the_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No registry, no history, no site. ``conftest`` already put us in an empty cwd."""
        _serve(monkeypatch, good_capability(), good_smart())
        out = tmp_path / "result.json"
        assert cli.main(["check", BASE, "--json-out", str(out)]) == 0
        assert sorted(p.name for p in Path.cwd().iterdir()) == ["result.json"]


class TestTheActionPreservesTheExitCode:
    def test_the_shell_block_runs_the_renderer_and_then_exits_with_the_checkers_code(self) -> None:
        run = _gate_step_run()
        assert "set -euo pipefail" in run
        assert "set +e" in run and "gate_rc=$?" in run and "set -e" in run
        # Order matters: the artifact is written by the check, the renderer reads it whatever
        # happened, and only then does the step adopt the checker's status.
        assert (
            run.index("gate_rc=$?") < run.index("render_result.py") < run.index('exit "$gate_rc"')
        )

    def test_the_action_declares_stable_outputs_and_inputs(self) -> None:
        assert _keys(_block("outputs")) == {
            "grade",
            "observed",
            "reachable",
            "passed",
            "result-json",
        }
        inputs = _block("inputs")
        assert _keys(inputs) == {
            "base-url",
            "min-grade",
            "name",
            "kind",
            "expects",
            "json",
            "summary",
            "python-version",
        }
        # Only the URL is required, and no threshold is set by default: an Action that gated by
        # default would be deciding for the caller what counts as a failure about someone's
        # endpoint.
        assert _action_text().count("required: true") == 1
        assert 'using: "composite"' in _action_text()

    def test_the_action_pins_every_step_it_uses_to_a_commit(self) -> None:
        refs = re.findall(r"uses: (\S+)", _action_text())
        assert refs, "the Action uses no steps; this scan would pass over nothing"
        for ref in refs:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref), ref

    def test_the_action_runs_the_bundled_checker_and_not_a_downloaded_one(self) -> None:
        run = _gate_step_run()
        assert '"${GITHUB_ACTION_PATH}"' in run
        assert "git+https://" not in run
        assert "pip install" in run and "fhir-scorecard==" not in run

    def test_the_action_source_archive_is_runtime_bounded(self) -> None:
        """A consumer downloads this tree on every ``uses:``; it is not the published dataset."""
        git = shutil.which("git")
        assert git is not None
        archive = subprocess.run(  # noqa: S603 - resolved git binary over this checkout
            [git, "archive", "--worktree-attributes", "--format=tar", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
            names = set(bundle.getnames())

        assert {"action.yml", "action/render_result.py", "pyproject.toml"} <= names
        assert "src/fhir_scorecard/cli.py" in names
        assert not any(name.startswith(("data/", "docs/", "tests/", ".github/")) for name in names)
        assert len(names) < 60

    def test_the_docs_do_not_advertise_a_release_tag_that_does_not_exist(self) -> None:
        """The project is pre-release and publishes no tags, so ``@v1`` would be an invention.

        The version in ``pyproject.toml`` is a development version; until it is not, the only
        honest ref to document is a commit SHA.
        """
        version = str(
            tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ]
        )
        docs = (ROOT / "docs" / "ci-action.md").read_text(encoding="utf-8")
        if ".dev" in version:
            assert not re.search(r"fhir-scorecard@v\d", docs), (
                "docs point at a release tag, but the version is still a development version"
            )
            assert "no release tag" in docs

    def test_the_documented_exit_codes_are_the_ones_the_check_returns(self) -> None:
        """The first exit-code table this repository publishes; it has to be true."""
        docs = (ROOT / "docs" / "ci-action.md").read_text(encoding="utf-8")
        for row in ("| `0` |", "| `1` |", "| `2` |"):
            assert row in docs, f"docs/ci-action.md does not document exit {row}"


def _render(
    tmp_path: Path, artifact: dict[str, Any] | None, gate_rc: int, min_grade: str = "B"
) -> tuple[str, str, str]:
    result = tmp_path / "result.json"
    if artifact is not None:
        result.write_text(json.dumps(artifact), encoding="utf-8")
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.md"
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, repository-owned script
        [
            sys.executable,
            str(RENDER),
            "--json",
            str(result),
            "--gate-rc",
            str(gate_rc),
            "--min-grade",
            min_grade,
            "--write-summary",
            "true",
        ],
        env=os.environ | {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        output.read_text() if output.exists() else "",
        summary.read_text() if summary.exists() else "",
        completed.stdout,
    )


def _artifact(grade: str, *, reachable: bool) -> dict[str, Any]:
    return {
        "generator": "fhir-scorecard",
        "generated_at": "2026-08-16 09:00 UTC",
        "vantage": "github-actions/Linux",
        "disclaimer": "Observational snapshot of public, unauthenticated FHIR discovery surfaces.",
        "scorecards": [
            {
                "endpoint_id": "endpoint-test",
                "name": "endpoint.test",
                "grade": grade,
                "reachable": reachable,
                "dimensions": [
                    {"key": "reachability", "title": "Reachability", "score": None, "findings": []},
                ],
            }
        ],
    }


class TestTheRenderedResult:
    def test_it_writes_outputs_a_summary_and_an_annotation(self, tmp_path: Path) -> None:
        outputs, summary, stdout = _render(tmp_path, _artifact("F", reachable=True), 1)
        assert "grade=F" in outputs
        assert "observed=true" in outputs
        assert "passed=false" in outputs
        assert "endpoint.test: grade F" in summary
        assert "Observational snapshot" in summary
        assert "::error title=FHIR endpoint check::" in stdout

    def test_a_passing_gate_writes_no_annotation(self, tmp_path: Path) -> None:
        outputs, _summary, stdout = _render(tmp_path, _artifact("A", reachable=True), 0)
        assert "passed=true" in outputs
        assert "::error" not in stdout

    def test_an_unobserved_run_is_never_rendered_as_a_letter(self, tmp_path: Path) -> None:
        outputs, summary, stdout = _render(tmp_path, _artifact(NOT_OBSERVED, reachable=False), 1)
        assert f"grade={NOT_OBSERVED}" in outputs
        assert "observed=false" in outputs
        assert "not observed on this run" in summary
        assert "grade F" not in summary and "grade F" not in stdout
        assert "could not be evaluated" in stdout
        assert "not a finding about what the endpoint publishes" in stdout

    def test_the_summary_says_the_measurement_came_from_one_vantage(self, tmp_path: Path) -> None:
        """Every vantage here is one host on one network, and the wording must not inflate it."""
        _outputs, summary, _stdout = _render(tmp_path, _artifact("B", reachable=True), 0)
        assert "from one vantage: github-actions/Linux" in summary
        for overclaim in ("several vantages", "independent networks", "multiple networks"):
            assert overclaim not in summary.lower()

    def test_a_missing_result_file_still_annotates_rather_than_crashing(
        self, tmp_path: Path
    ) -> None:
        outputs, summary, stdout = _render(tmp_path, None, 2)
        assert "passed=false" in outputs
        assert summary == ""
        assert "could not be checked" in stdout


def test_the_composite_action_survives_a_real_end_to_end_failing_run(tmp_path: Path) -> None:
    """Drive the Action's own shell contract, minus the runner, against a host that cannot exist.

    ``.invalid`` is reserved by RFC 2606 and is guaranteed never to resolve, so this is a real
    retrieval attempt that reaches nobody: the deliberately bad input, end to end, including the
    renderer and the ``exit "$gate_rc"`` the Action relies on.
    """
    bash = shutil.which("bash")
    assert bash is not None
    result = tmp_path / "result.json"
    script = (
        "set -euo pipefail\n"
        "set +e\n"
        f'"{sys.executable}" -m fhir_scorecard.cli check https://nothing.invalid/fhir '
        f'--min-grade B --json-out "{result}"\n'
        "gate_rc=$?\n"
        "set -e\n"
        f'"{sys.executable}" "{RENDER}" --json "{result}" --gate-rc "$gate_rc" '
        "--min-grade B --write-summary true\n"
        'exit "$gate_rc"\n'
    )
    completed = subprocess.run(  # noqa: S603 - resolved bash over a script this test builds
        [bash, "-c", script],
        cwd=tmp_path,
        env=os.environ
        | {
            "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            "PYTHONPATH": str(ROOT / "src"),
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1, completed.stderr
    assert json.loads(result.read_text())["scorecards"][0]["grade"] == NOT_OBSERVED
    outputs = (tmp_path / "output.txt").read_text()
    assert "passed=false" in outputs
    assert f"grade={NOT_OBSERVED}" in outputs
    assert "::error title=FHIR endpoint check::" in completed.stdout
    assert "grade F" not in completed.stdout
