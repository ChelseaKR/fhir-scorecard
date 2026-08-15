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

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

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
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert len(files) >= 4, f"expected the workflow set, found {[f.name for f in files]}"
    assert sum(len(_blocks(f.read_text(encoding="utf-8"))) for f in files) >= 5


def test_every_multiline_run_block_fails_on_the_first_failed_command() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
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
