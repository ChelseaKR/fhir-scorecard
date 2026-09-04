"""A multi-line workflow step must not be able to pass on a command that failed.

GitHub Actions runs a ``run:`` block with ``bash -e`` by default. ``-e`` alone does not set
``pipefail``, so in ``cmd | tee out.txt`` the pipeline's exit status is tee's and a crashed
``cmd`` is invisible. That is how ``recheck.yml`` came to report "no rejected candidate has
started answering" for any failure of the re-probe, including a candidate file it could not
load, while the workflow went green.

The fix is one line per block. This test is what keeps the next block from omitting it: every
``run: |`` in every workflow must declare ``shell: bash`` and open with ``set -euo pipefail``.
Workflow YAML is scanned as text rather than parsed, so no dependency is added for it; the
scan asserts its own preconditions loudly enough that a change in file shape fails here rather
than being skipped past.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
# The composite action's shell block runs on a consumer's runner rather than on ours, which is
# the one place where a missing `pipefail` would fail quietly in somebody else's build.
ACTION = ROOT / "action.yml"


def _scanned() -> list[Path]:
    return [*sorted(WORKFLOWS.glob("*.yml")), *([ACTION] if ACTION.is_file() else [])]


_RUN_BLOCK = re.compile(r"^(?P<indent>\s*)run: \|\s*$")

REQUIRED_FIRST_STATEMENT = "set -euo pipefail"


def _blocks(text: str) -> list[tuple[int, list[str]]]:
    """Every ``run: |`` block, as (line number of the ``run:``, block lines)."""
    lines = text.splitlines()
    found: list[tuple[int, list[str]]] = []
    for i, line in enumerate(lines):
        match = _RUN_BLOCK.match(line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        body: list[str] = []
        for candidate in lines[i + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                break
            body.append(candidate)
        found.append((i + 1, body))
    return found


def _step_declares_bash(text: str, run_line: int) -> bool:
    """Whether the step containing the ``run:`` at ``run_line`` sets ``shell: bash``.

    Scans upward from the ``run:`` to the start of the step (a line beginning ``- ``).
    """
    lines = text.splitlines()
    for line in reversed(lines[: run_line - 1]):
        if line.strip().startswith("- "):
            return False
        if line.strip() == "shell: bash":
            return True
    return False


def test_there_are_workflows_to_check() -> None:
    """A scan over nothing passes trivially, which is the failure mode being guarded."""
    files = _scanned()
    assert len(files) >= 5, (
        f"expected the workflow set and action.yml, found {[f.name for f in files]}"
    )
    assert ACTION in files, "action.yml ships a shell block to consumers and must be scanned"
    assert sum(len(_blocks(f.read_text(encoding="utf-8"))) for f in files) >= 6


def test_every_multiline_run_block_fails_on_the_first_failed_command() -> None:
    for path in _scanned():
        text = path.read_text(encoding="utf-8")
        for run_line, body in _blocks(text):
            where = f"{path.name}:{run_line}"
            statements = [
                line.strip() for line in body if line.strip() and not line.strip().startswith("#")
            ]
            assert statements, f"{where}: empty run block"
            assert _step_declares_bash(text, run_line), (
                f"{where}: multi-line run block does not declare `shell: bash`, so the "
                "shell it gets is the runner default and `set -o pipefail` is not implied"
            )
            assert statements[0] == REQUIRED_FIRST_STATEMENT, (
                f"{where}: run block opens with {statements[0]!r}, not "
                f"{REQUIRED_FIRST_STATEMENT!r}. Without pipefail a failing command on the "
                "left of a pipe is reported as success."
            )


#: Interpolations that are safe to expand into a shell, because GitHub controls their value and
#: no third party can influence it. Everything else must reach a script through `env:`, where it
#: arrives as a variable rather than as text pasted into the program before bash parses it.
_ALLOWED_INTERPOLATIONS = frozenset({"github.token", "matrix.os"})

_INTERPOLATION = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def _jobs(text: str) -> list[tuple[str, str]]:
    """(name, body) for each job, found by indentation under the top-level ``jobs:`` key.

    Written by hand because this package has no runtime dependencies and the test suite does not
    get a YAML parser for one assertion. Only the shape these files actually use is supported:
    two-space job names under ``jobs:``, which is what every workflow here is written in.
    """
    if "\njobs:\n" not in text:
        return []
    block = text.split("\njobs:\n", 1)[1]
    found: list[tuple[str, str]] = []
    name: str | None = None
    body: list[str] = []
    for line in block.split("\n"):
        if line and not line.startswith(" ") and not line.startswith("#"):
            break  # a new top-level key ends the jobs block
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            if name is not None:
                found.append((name, "\n".join(body)))
            name, body = match.group(1), []
        elif name is not None:
            body.append(line)
    if name is not None:
        found.append((name, "\n".join(body)))
    return found


def test_no_run_block_pastes_an_untrusted_expression_into_a_shell() -> None:
    """`${{ }}` inside `run:` is textual substitution performed before bash sees the script.

    This suite already required `shell: bash` and `set -euo pipefail`, which is about a script
    failing honestly, not about what the script is. Nothing checked the actual injection vector.
    These files are clean today by discipline alone, and `action.yml` ships to consumers'
    runners, so the discipline is worth a gate: a value reaching a shell must come through
    `env:`, where bash receives a variable rather than a program someone else helped write.
    """
    offenders: list[str] = []
    for path in _scanned():
        text = path.read_text(encoding="utf-8")
        for run_line, body in _blocks(text):
            for line in body:
                for expression in _INTERPOLATION.findall(line):
                    if expression not in _ALLOWED_INTERPOLATIONS:
                        offenders.append(f"{path.name}:{run_line}: ${{{{ {expression} }}}}")
    assert not offenders, (
        "these run blocks interpolate a template expression directly into the shell; pass the "
        "value through `env:` instead: " + "; ".join(offenders)
    )


def test_every_job_bounds_its_own_runtime() -> None:
    """A hung job holds a runner for the six-hour default. `pages.yml` probes 45 third-party
    servers from three OS images, so the default is six hours per image."""
    missing: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job, section in _jobs(path.read_text(encoding="utf-8")):
            if re.search(r"(?m)^    uses:", section):
                # A job that calls a reusable workflow has no `runs-on` of its own, and GitHub
                # rejects `timeout-minutes` on it. The called workflow bounds its own jobs.
                continue
            if "timeout-minutes:" not in section:
                missing.append(f"{path.name}:{job}")
    assert not missing, f"jobs with no timeout-minutes: {missing}"


def test_the_job_scan_finds_the_jobs_it_is_meant_to_bound() -> None:
    """The scan is indentation-based, so a vacuous pass is the failure mode to guard."""
    found = {
        f"{path.name}:{job}"
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job, _ in _jobs(path.read_text(encoding="utf-8"))
    }
    assert {"pages.yml:probe", "pages.yml:grade", "pages.yml:deploy"} <= found
    assert {"verify.yml:verify", "security.yml:codeql"} <= found
    # `on:` keys sit at the same indentation as job names and are not jobs.
    assert not any(name.endswith((":schedule", ":workflow_dispatch", ":push")) for name in found)
