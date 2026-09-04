"""Every Python file a consumer downloads must be inside the gates that claim to check it.

`action/render_result.py` runs on someone else's runner on every
`uses: ChelseaKR/fhir-scorecard@<tag>`, and it sat outside `make lint`, `make format` and
`make typecheck`, all three of which are scoped by path. The consequence was not a bug in that
file; it was that the three gates printed "All checks passed!", "35 files already formatted" and
"Success: no issues found" without having opened it. A green signal that would look identical
whether the file was clean or absent is not a signal.

This reads the gate definitions rather than re-implementing them, so widening or narrowing a
scope has to be a deliberate edit to a list this test can see.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import tomllib
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
PYPROJECT = ROOT / "pyproject.toml"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


def _shipped_python_files() -> set[str]:
    """The `.py` files in the tree a consumer's runner checks out and installs.

    `git archive` applies `.gitattributes`, so this is the same view
    `test_the_action_source_archive_is_runtime_bounded` asserts the size of.
    """
    git = shutil.which("git")
    assert git is not None
    archive = subprocess.run(  # noqa: S603 - resolved git binary over this checkout
        [git, "archive", "--worktree-attributes", "--format=tar", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
        return {name for name in bundle.getnames() if name.endswith(".py")}


def _make_target(name: str) -> str:
    """The recipe lines of one Makefile target."""
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(rf"^{name}:.*$\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert match is not None, f"the Makefile has no {name} target"
    return match.group(1)


def _covers(scope: list[str], path: str) -> bool:
    return any(path == entry or path.startswith(f"{entry.rstrip('/')}/") for entry in scope)


def test_the_gates_and_the_shipped_tree_are_both_non_empty() -> None:
    """Both halves of every comparison below, asserted before they are compared."""
    shipped = _shipped_python_files()
    assert len(shipped) >= 10, f"the archive holds almost no Python: {sorted(shipped)}"
    assert "action/render_result.py" in shipped, (
        "the Action's result renderer is no longer shipped; if that is intended, this whole "
        "file is what needs revisiting, not the assertion"
    )


def test_lint_and_format_cover_every_file_that_ships() -> None:
    for target in ("lint", "format"):
        recipe = _make_target(target)
        assert "ruff" in recipe, f"the {target} target no longer runs ruff"
        scope = (
            recipe.split()[recipe.split().index("--check") + 1 :]
            if target == "format"
            else (recipe.split()[recipe.split().index("check") + 1 :])
        )
        assert scope, f"the {target} target names no paths"
        for path in sorted(_shipped_python_files()):
            assert _covers(scope, path), (
                f"`make {target}` does not cover {path}, which ships to a consumer's runner; "
                f"its scope is {scope}"
            )


def test_the_type_checker_covers_every_file_that_ships() -> None:
    scope = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mypy"]["files"]
    assert scope, "mypy names no files"
    for path in sorted(_shipped_python_files()):
        assert _covers(scope, path), (
            f"mypy does not check {path}, which ships to a consumer's runner; its scope is {scope}"
        )


def test_the_local_hooks_mirror_the_gate_of_record() -> None:
    """A local pass and a CI pass have to mean the same thing, which is why the hooks are scoped
    at all. Drift here is how a contributor gets a clean commit that CI then rejects."""
    hooks = PRE_COMMIT.read_text(encoding="utf-8")
    patterns = re.findall(r"^\s+files: \^\((.+)\)/$", hooks, re.MULTILINE)
    assert patterns, "no ruff hook is path-scoped any more; re-read this test's premise"
    for pattern in patterns:
        scope = pattern.split("|")
        for path in sorted(_shipped_python_files()):
            assert _covers(scope, path), (
                f"the pre-commit ruff hooks skip {path}; their scope is {scope}"
            )


def test_the_hooks_run_the_versions_the_lockfile_and_the_workflow_pin() -> None:
    """A local pass and a CI pass have to mean the same thing, and `ruff format` moves.

    `.pre-commit-config.yaml` pinned ruff v0.16.2 while `uv.lock` resolved 0.16.4, so a
    contributor whose hook reformatted a file could still fail `make format` in CI, or pass
    locally on formatting CI would reject. Dependabot has no `pre-commit` ecosystem, so nothing
    was ever going to notice: the skew is structural rather than a one-off, which is why it is
    asserted here instead of being bumped and forgotten.
    """
    root = Path(__file__).resolve().parent.parent
    hooks = (root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    security = (root / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")

    locked_ruff = re.search(r'\[\[package\]\]\nname = "ruff"\nversion = "([^"]+)"', lock)
    assert locked_ruff, "uv.lock does not pin ruff"
    hook_ruff = re.search(r"ruff-pre-commit\n\s+rev: [0-9a-f]{40} # v([0-9.]+)", hooks)
    assert hook_ruff, ".pre-commit-config.yaml does not pin a ruff-pre-commit revision"
    assert hook_ruff.group(1) == locked_ruff.group(1), (
        f"pre-commit runs ruff v{hook_ruff.group(1)} but uv.lock pins {locked_ruff.group(1)}; "
        "`ruff format` output differs between versions, so the two gates disagree"
    )

    hook_gitleaks = re.search(r"gitleaks\n\s+rev: [0-9a-f]{40} # v([0-9.]+)", hooks)
    workflow_gitleaks = re.search(r"GL=([0-9.]+)", security)
    assert hook_gitleaks and workflow_gitleaks
    assert hook_gitleaks.group(1) == workflow_gitleaks.group(1), (
        "the pre-commit secret scan and the CI secret scan run different gitleaks versions"
    )
