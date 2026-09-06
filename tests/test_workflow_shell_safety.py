"""A multi-line workflow step must not be able to pass on a command that failed.

GitHub Actions runs a ``run:`` block with ``bash -e`` by default. ``-e`` alone does not set
``pipefail``, so in ``cmd | tee out.txt`` the pipeline's exit status is tee's and a crashed
``cmd`` is invisible. That is how ``recheck.yml`` came to report "no rejected candidate has
started answering" for any failure of the re-probe, including a candidate file it could not
load, while the workflow went green.

The fix is one line per block. This test is what keeps the next block from omitting it: every
``run: |`` in every workflow must declare ``shell: bash`` and open with ``set -euo pipefail``.
Those assertions are about the *text* of a shell script, so they are made against the text.

The assertions about workflow *structure* are not, and this file used to say they were: it
claimed workflow YAML was "scanned as text rather than parsed, so no dependency is added for
it". That stance shipped a release-blocking bug. A hardening pass added ``timeout-minutes: 10``
to a ``release.yml`` job that already declared ``timeout-minutes: 20``; the guard against an
unbounded job asked whether the substring ``"timeout-minutes:"`` appeared in the job at all, and
a key that appears twice satisfies that as readily as a key that appears once. GitHub then
refused to parse the workflow, so no release could be dispatched, and the suite stayed green
across the whole outage.

Whether GitHub can parse a workflow is not a question string matching can answer, so the
structural checks now parse, with a loader that rejects duplicate mapping keys the way GitHub
does. See ``pyproject.toml`` for why the dependency was worth reversing that stance for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

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


class DuplicateKeyError(Exception):
    """A mapping declared the same key twice."""


class _StrictLoader(yaml.SafeLoader):
    """``yaml.SafeLoader``, except that a repeated mapping key is an error rather than a
    silent last-one-wins overwrite.

    PyYAML resolves duplicates the way a dict literal does. GitHub Actions does not: it
    rejects the workflow outright, which is what made a duplicated key a release blocker
    rather than a style nit. This loader is the strict half of that behaviour, so the test
    below fails on the file GitHub would refuse.
    """


def _reject_duplicate_keys(loader: _StrictLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: dict[Any, int] = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise DuplicateKeyError(
                f"duplicate key {key!r}: first declared on line {seen[key]}, "
                f"declared again on line {key_node.start_mark.line + 1}"
            )
        seen[key] = key_node.start_mark.line + 1
    return dict(yaml.SafeLoader.construct_mapping(loader, node, deep=True))


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _reject_duplicate_keys
)


def _load_strict(text: str) -> Any:
    """Parse ``text`` with the duplicate-rejecting loader.

    The single entry point for the strict parse, so the one suppression below is the only one.
    ``S506`` fires on any ``yaml.load`` whose loader it does not recognise as safe, and it
    recognises the name ``SafeLoader`` rather than the class hierarchy. ``_StrictLoader``
    derives from ``SafeLoader`` and adds a constructor that raises; it resolves no tags
    ``SafeLoader`` would not, so it cannot instantiate an arbitrary object. The assertion is
    what keeps that argument true - re-parent the loader and this fails rather than silently
    becoming the unsafe load the rule is warning about.
    """
    assert issubclass(_StrictLoader, yaml.SafeLoader), (
        "_StrictLoader must stay a SafeLoader subclass; the S506 suppression below is only "
        "correct because it is one"
    )
    return yaml.load(text, Loader=_StrictLoader)  # noqa: S506


def _parsed(path: Path) -> dict[Any, Any]:
    """The workflow at ``path``, parsed strictly. Raises on a duplicate key."""
    document = _load_strict(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path.name}: workflow is not a mapping"
    return document


def _jobs(path: Path) -> list[tuple[str, dict[Any, Any]]]:
    """(name, body) for each job in the workflow at ``path``."""
    jobs = _parsed(path).get("jobs") or {}
    assert isinstance(jobs, dict), f"{path.name}: `jobs:` is not a mapping"
    return sorted(jobs.items())


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


def test_every_workflow_parses_the_way_github_will_parse_it() -> None:
    """GitHub rejects a workflow whose YAML is malformed or declares a key twice, and a
    rejected workflow cannot be dispatched at all.

    This is the assertion that was missing when a duplicated ``timeout-minutes:`` reached
    ``release.yml`` and blocked releases: the file was still valid *text*, and every check
    over it was a check over text. ``action.yml`` is included because it is parsed on a
    consumer's runner, where a parse error is someone else's broken build.
    """
    broken: list[str] = []
    for path in _scanned():
        try:
            _parsed(path)
        except DuplicateKeyError as exc:
            broken.append(f"{path.name}: {exc}")
        except yaml.YAMLError as exc:
            broken.append(f"{path.name}: {type(exc).__name__}: {exc}")
    assert not broken, (
        "GitHub cannot parse these files, so the workflows in them cannot run: " + "; ".join(broken)
    )


def test_the_strict_loader_actually_rejects_a_duplicate_key() -> None:
    """A negative control for the test above.

    A checker that cannot fail reads exactly like a checker that found nothing wrong, which
    is precisely how the substring check this replaced stayed green through the outage. So
    the loader is required to reject a duplicate here, on a document shaped like the one that
    broke. Without this, swapping ``_StrictLoader`` back to a plain ``SafeLoader`` would leave
    the suite green.
    """
    duplicated = (
        "jobs:\n  build:\n    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 10\n    timeout-minutes: 20\n"
    )
    with pytest.raises(DuplicateKeyError, match="timeout-minutes"):
        _load_strict(duplicated)

    # The same document minus the duplicate must load, or the control above would pass for
    # the wrong reason - a loader that rejected everything would satisfy it just as well.
    once = "jobs:\n  build:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20\n"
    assert _load_strict(once)["jobs"]["build"]["timeout-minutes"] == 20


def test_every_job_bounds_its_own_runtime() -> None:
    """A hung job holds a runner for the six-hour default. `pages.yml` probes 45 third-party
    servers from three OS images, so the default is six hours per image."""
    missing: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job, section in _jobs(path):
            if "uses" in section:
                # A job that calls a reusable workflow has no `runs-on` of its own, and GitHub
                # rejects `timeout-minutes` on it. The called workflow bounds its own jobs.
                continue
            if "timeout-minutes" not in section:
                missing.append(f"{path.name}:{job}")
    assert not missing, f"jobs with no timeout-minutes: {missing}"


def test_the_job_scan_finds_the_jobs_it_is_meant_to_bound() -> None:
    """A scan over nothing passes trivially, which is the failure mode to guard."""
    found = {
        f"{path.name}:{job}" for path in sorted(WORKFLOWS.glob("*.yml")) for job, _ in _jobs(path)
    }
    assert {"pages.yml:probe", "pages.yml:grade", "pages.yml:deploy"} <= found
    assert {"verify.yml:verify", "security.yml:codeql"} <= found
    assert {"release.yml:release-tests", "release.yml:build"} <= found
