"""A merge is not a publishing failure, and the sentinel has to be able to tell.

`live-integrity` compares the live site with `main`. `grade-and-publish` runs at 14:17 UTC and
the sentinel at 20:07, so a change to a checked input merged in between is on `main` and not
yet on the site: for 5h50m of every day -- 24% of the clock, and the working part of it -- the
two legitimately disagree, and that was reported as a failure. It fired once, on 2026-09-07,
when #120 added a CSV column at 16:24 after that day's 14:27 publish. One of one reds, spurious,
and the next red will look identical to a person who has learned to dismiss this one.

The repair is not to compare less. `tools/verify_live_site.py` recomputes the expected bytes
from the committed inputs on every run and has no false-positive mode of its own, so the
reference *bytes* were never the bug; the reference *timestamp* was. Two conditions are told
apart by dating the newest commit that touches a checked input against the live publish stamp:

  (a) the publish ran after the change and the bytes still differ -- red, exactly as before;
  (b) no publish has run since the change -- reported and not failed.

This file is the proof that the repair did not blunt the check, so it asserts (a) still fails
and the freshness bound still fails with a publish pending, not only that (b) now passes.
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "verify_live_site.py"
SENTINEL = ROOT / ".github" / "workflows" / "live-integrity.yml"
PUBLISH = ROOT / ".github" / "workflows" / "pages.yml"


def _load_tool() -> ModuleType:
    """`tools/` is not a package, and the check is not importable any other way.

    It is loaded rather than re-implemented on purpose: a test that restated the decision
    would pass while the shipped one drifted, which is the failure this whole file is about.
    """
    specification = importlib.util.spec_from_file_location("verify_live_site", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # Registered before execution: a dataclass declared at module scope resolves its own
    # module out of sys.modules while the decorator runs, and raises if it is not there.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


tool = _load_tool()

GIT = shutil.which("git")


def _utc(stamp: str) -> dt.datetime:
    return dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)


def _git(repo: Path, *arguments: str) -> str:
    """git, with the developer's global configuration cut out of the picture.

    A fixture repository must not inherit a signing key, a hook path, a default branch name or
    a commit template from whoever is running the suite; those are all things that would make
    this pass on one machine and fail on another.
    """
    assert GIT is not None, "git is required to test how this check dates a change"
    absent = repo.parent / "absent-git-configuration"
    environment = dict(os.environ) | {
        "GIT_CONFIG_GLOBAL": str(absent),
        "GIT_CONFIG_SYSTEM": str(absent),
        "GIT_AUTHOR_NAME": "Sentinel Fixture",
        "GIT_AUTHOR_EMAIL": "sentinel@example.invalid",
        "GIT_COMMITTER_NAME": "Sentinel Fixture",
        "GIT_COMMITTER_EMAIL": "sentinel@example.invalid",
    }
    return subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [GIT, "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout


def _write(repo: Path, relative: str, text: str) -> str:
    """Write one path and return what has to be staged for it, directory or file."""
    target = repo / relative
    if target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return relative
    target.mkdir(parents=True, exist_ok=True)
    (target / "site.css").write_text(text, encoding="utf-8")
    return f"{relative}/site.css"


def _commit(repo: Path, relative: str, text: str, message: str) -> str:
    staged = _write(repo, relative, text)
    _git(repo, "add", "--", staged)
    _git(repo, "commit", "--no-gpg-sign", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _checkout(root: Path) -> Path:
    """A repository carrying every checked input, plus a file that is not one."""
    repo = root / "checkout"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    for relative in tool.CHECKED_INPUTS:
        _write(repo, relative, "first\n")
    _git(repo, "add", "--", *tool.CHECKED_INPUTS)
    _git(repo, "commit", "--no-gpg-sign", "-m", "every checked input")
    return repo


def _observation(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "differences": ["dataset.csv: header is [...], this checkout's columns are [...]"],
        "staleness": [],
        "generated": _utc("2026-09-07 14:27"),
        "assets_compared": 58,
        "endpoints_compared": 81,
    }
    return tool.Observation(**(defaults | overrides))


def _commit_at(stamp: str) -> Any:
    return tool.InputCommit(
        sha="98129f4cdaf929da44211a6353cb105f2b423e3f",
        committed=_utc(stamp),
        subject="Say why an endpoint was not reached: gated apart from broken, as data",
    )


# --------------------------------------------------------------------------------------
# What counts as an input, derived rather than remembered
# --------------------------------------------------------------------------------------


def test_every_checked_input_is_a_path_this_repository_has() -> None:
    assert tool.CHECKED_INPUTS, "a list of inputs that is empty dates nothing"
    for relative in tool.CHECKED_INPUTS:
        assert (ROOT / relative).exists(), f"{relative} is dated but is not in the tree"


def test_every_module_the_expectations_are_computed_from_is_a_checked_input() -> None:
    """The list has to follow the imports, or it silently stops covering the check.

    `site.robots`, `dataset.schema_doc`, `dataset._COLUMNS` and `load_registry` are what turn
    a checkout into the bytes the live site is compared against. A module that joins them and
    not `CHECKED_INPUTS` would be a change nobody could date, and every merge touching it would
    be red again for six hours -- which is the bug this is the fix for.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(TOOL.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module == "fhir_scorecard":
            imported |= {alias.name for alias in node.names}
        elif node.module.startswith("fhir_scorecard."):
            imported.add(node.module.split(".", 1)[1])
    assert imported, "this check imports nothing from fhir_scorecard; re-read this test"
    for name in sorted(imported):
        assert f"src/fhir_scorecard/{name}.py" in tool.CHECKED_INPUTS, (
            f"verify_live_site.py computes what it expects out of fhir_scorecard.{name}, which "
            f"is not one of the paths it dates: {tool.CHECKED_INPUTS}"
        )


def test_the_files_the_check_reads_from_disk_are_checked_inputs() -> None:
    for path in (tool.ASSETS, tool.REGISTRY):
        relative = Path(path).relative_to(ROOT).as_posix()
        assert relative in tool.CHECKED_INPUTS, f"{relative} is compared but is never dated"


# --------------------------------------------------------------------------------------
# The decision itself. These three are the negative controls, as tests.
# --------------------------------------------------------------------------------------


def test_a_site_generated_before_the_newest_input_commit_is_pending_not_broken() -> None:
    """The 2026-09-07 red, to the minute: #120 merged 16:24, that day's publish ran 14:27."""
    pending = tool.pending_publish(_observation(), _commit_at("2026-09-07 16:24"))
    assert pending is not None
    assert pending.short_sha == "98129f4cdaf9"


def test_a_site_generated_after_the_newest_input_commit_is_still_a_failure() -> None:
    """Condition (a): the publish ran, and it produced something other than what main says."""
    assert tool.pending_publish(_observation(), _commit_at("2026-09-07 09:00")) is None


def test_a_publish_stamped_the_same_minute_as_the_commit_is_still_a_failure() -> None:
    """`generated_at` has minute resolution and a commit has second resolution, so a tie is
    unresolvable. It resolves to red: erring toward a spurious failure is recoverable, and
    erring toward excusing a real one is what this check exists to prevent."""
    assert tool.pending_publish(_observation(), _commit_at("2026-09-07 14:27")) is None


def test_a_stale_site_is_a_failure_even_when_a_publish_is_pending() -> None:
    """The 48-hour bound is what catches a publish that stopped running, and a merge must not
    excuse it. Same commit and same stamp as the pending case above; only the staleness line
    is added, and the verdict has to flip back to red."""
    stale = _observation(
        staleness=["the live site was generated 71.0 hours ago, past the 48 hour limit."]
    )
    assert tool.pending_publish(stale, _commit_at("2026-09-07 16:24")) is None


def test_an_undatable_checkout_keeps_the_difference_a_failure() -> None:
    """Unknown is not pending. A checkout that cannot say when an input changed stays red."""
    assert tool.pending_publish(_observation(), None) is None


def test_a_site_that_matches_is_never_reported_as_pending() -> None:
    assert (
        tool.pending_publish(_observation(differences=[]), _commit_at("2026-09-07 16:24")) is None
    )


# --------------------------------------------------------------------------------------
# Dating the change, against real git
# --------------------------------------------------------------------------------------


def test_the_newest_commit_touching_an_input_is_the_one_reported(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    wanted = _commit(repo, "src/fhir_scorecard/site.py", "changed\n", "change an input")
    _commit(repo, "README.md", "later\n", "change something that is not an input")
    dated = tool.last_input_commit(repo=repo)
    assert dated is not None
    assert dated.sha == wanted
    assert dated.subject == "change an input"
    assert dated.committed.tzinfo is not None


def test_a_commit_touching_nothing_checked_does_not_move_the_date(tmp_path: Path) -> None:
    """Otherwise every merge would excuse every difference, which is a check that cannot fail."""
    repo = _checkout(tmp_path)
    before = tool.last_input_commit(repo=repo)
    _commit(repo, "CHANGELOG.md", "an entry\n", "prose only")
    after = tool.last_input_commit(repo=repo)
    assert before is not None and after is not None
    assert after.sha == before.sha


def test_an_asset_is_dated_like_any_other_input(tmp_path: Path) -> None:
    """#140 changed `assets/site.css` inside the window on 2026-09-12 and only escaped a red
    because a republish was dispatched by hand. An asset is a directory, not a file, so it is
    the one entry whose path shape differs and the one most likely to be dated by nothing."""
    repo = _checkout(tmp_path)
    wanted = _commit(repo, "src/fhir_scorecard/assets", "body { color: red }\n", "restyle")
    dated = tool.last_input_commit(repo=repo)
    assert dated is not None and dated.sha == wanted


def test_a_depth_one_checkout_is_refused_rather_than_dated_to_now(tmp_path: Path) -> None:
    """This is the one that would silently blunt the check.

    A shallow clone grafts its oldest fetched commit into a parentless root, so at depth 1
    every file in the tree reads as having been added at HEAD. The inputs would date to the
    newest commit on main whatever it touched, every difference would be newer than the
    publish, and the sentinel would excuse a genuinely broken deployment. Refused instead.
    """
    repo = _checkout(tmp_path)
    _commit(repo, "src/fhir_scorecard/site.py", "changed\n", "change an input")
    _commit(repo, "README.md", "later\n", "prose only")
    shallow = tmp_path / "shallow"
    _git(repo, "clone", "--depth", "1", f"file://{repo}", str(shallow))
    assert _git(shallow, "rev-parse", "--is-shallow-repository").strip() == "true"
    assert _git(shallow, "log", "-1", "--format=%H", "--", "src/fhir_scorecard/site.py").strip(), (
        "a depth-1 clone was expected to report HEAD as having added this path; if git has "
        "stopped doing that, this test is asserting a hazard that no longer exists"
    )
    assert tool.last_input_commit(repo=shallow) is None


def test_a_shallow_checkout_deep_enough_to_see_the_change_still_dates_it(tmp_path: Path) -> None:
    """Refusing the graft is not refusing every truncated history: `git log -1` walks back from
    HEAD, so an answer that is not the graft is the newest commit touching the path whatever
    was truncated behind it."""
    repo = _checkout(tmp_path)
    # One commit below the change, so the clone's graft lands under it rather than on it.
    _commit(repo, "README.md", "earlier\n", "prose only, before the change")
    wanted = _commit(repo, "data/registry.json", '{"endpoints": []}\n', "change an input")
    for step in range(2):
        _commit(repo, "README.md", f"later {step}\n", f"prose only {step}")
    shallow = tmp_path / "deeper"
    _git(repo, "clone", "--depth", "4", f"file://{repo}", str(shallow))
    assert _git(shallow, "rev-list", "--count", "HEAD").strip() == "4"
    assert _git(shallow, "rev-parse", "--is-shallow-repository").strip() == "true"
    dated = tool.last_input_commit(repo=shallow)
    assert dated is not None and dated.sha == wanted


def test_a_directory_that_is_not_a_repository_dates_nothing(tmp_path: Path) -> None:
    assert tool.last_input_commit(repo=tmp_path) is None


# --------------------------------------------------------------------------------------
# The sentence the pending report prints, and the workflow that has to support it
# --------------------------------------------------------------------------------------


def test_the_sentinel_checks_out_enough_history_to_date_a_change() -> None:
    document = yaml.safe_load(SENTINEL.read_text(encoding="utf-8"))
    steps = document["jobs"]["verify-live"]["steps"]
    checkouts = [step for step in steps if "actions/checkout" in str(step.get("uses", ""))]
    assert len(checkouts) == 1, "the sentinel no longer has exactly one checkout"
    options = checkouts[0].get("with", {})
    assert options.get("ref") == "main", "the sentinel compares the live site against main"
    assert options.get("fetch-depth") == 0, (
        "the sentinel checks out shallow, so `git log` cannot say when a checked input last "
        "changed and every change merged in the 5h50m before this run is red again"
    )


def _daily_cron(path: Path) -> str:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = document.get("on", document.get(True))
    schedules = triggers["schedule"]
    assert len(schedules) == 1, "pages.yml no longer has exactly one schedule"
    return str(schedules[0]["cron"])


def test_the_next_publish_is_read_from_the_workflow_that_publishes() -> None:
    """Parsed out of pages.yml rather than restated, so the two cannot disagree. Checked here
    against a real YAML loader rather than against the same regex that produced it."""
    minute, hour, *rest = _daily_cron(PUBLISH).split()
    assert rest == ["*", "*", "*"], "pages.yml is no longer on a plain daily cron"
    now = _utc("2026-09-07 20:21")
    following = tool.next_scheduled_publish(now)
    assert following is not None
    assert (following.hour, following.minute) == (int(hour), int(minute))
    assert now < following <= now + dt.timedelta(days=1)


def test_the_next_publish_rolls_forward_only_when_today_is_spent() -> None:
    assert tool.next_scheduled_publish(_utc("2026-09-07 06:00")) == _utc("2026-09-07 14:17")
    assert tool.next_scheduled_publish(_utc("2026-09-07 20:21")) == _utc("2026-09-08 14:17")


@pytest.mark.parametrize(
    "schedule",
    [
        'on:\n  schedule:\n    - cron: "*/5 * * * *"\n',
        'on:\n  schedule:\n    - cron: "17 14 * * *"\n    - cron: "17 2 * * *"\n',
        "on:\n  workflow_dispatch:\n",
    ],
)
def test_a_schedule_this_cannot_read_prints_no_time_rather_than_a_wrong_one(
    tmp_path: Path, schedule: str
) -> None:
    workflow = tmp_path / "pages.yml"
    workflow.write_text(schedule, encoding="utf-8")
    assert tool.next_scheduled_publish(_utc("2026-09-07 20:21"), workflow=workflow) is None


def test_a_missing_workflow_prints_no_time_rather_than_raising(tmp_path: Path) -> None:
    assert tool.next_scheduled_publish(_utc("2026-09-07 20:21"), workflow=tmp_path / "gone") is None


# --------------------------------------------------------------------------------------
# End to end through main(), with the network stood in for
# --------------------------------------------------------------------------------------


def _run(monkeypatch: pytest.MonkeyPatch, observation: Any, commit: Any) -> int:
    monkeypatch.setattr(tool, "observe", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(tool, "last_input_commit", lambda *_args, **_kwargs: commit)
    return int(tool.main(["--url", "https://fhir.example.test/", "--attempts", "1"]))


def test_main_reports_a_pending_publish_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch, _observation(), _commit_at("2026-09-07 16:24"))
    printed = capsys.readouterr()
    assert code == 0
    assert "::notice title=pending publish::" in printed.out, (
        "a green check with nothing attached to it is indistinguishable from a site that was "
        "already up to date; the one outcome this added has to be visible"
    )
    assert "98129f4cdaf9" in printed.out, "the pending report has to name the commit"
    assert "14:17 UTC" in printed.out, "the pending report has to name the next publish"
    assert "no longer matches" not in printed.err


def test_main_fails_when_the_publish_ran_and_the_bytes_still_differ(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch, _observation(), _commit_at("2026-09-07 09:00"))
    printed = capsys.readouterr()
    assert code == tool.EXIT_DIFFERS
    assert "no longer matches what this checkout publishes" in printed.err


def test_main_fails_when_the_site_is_stale_and_a_publish_is_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = _observation(staleness=["the live site was generated 71.0 hours ago"])
    code = _run(monkeypatch, stale, _commit_at("2026-09-07 16:24"))
    printed = capsys.readouterr()
    assert code == tool.EXIT_DIFFERS
    assert "71.0 hours ago" in printed.err, "the staleness line has to survive into the report"


def test_main_warns_before_failing_a_difference_it_could_not_date(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch, _observation(), None)
    printed = capsys.readouterr()
    assert code == tool.EXIT_DIFFERS
    assert "::warning title=undatable checkout::" in printed.err


def test_main_still_passes_a_site_that_matches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch, _observation(differences=[]), _commit_at("2026-09-07 16:24"))
    printed = capsys.readouterr()
    assert code == 0
    assert "still matches what this checkout publishes" in printed.out
    assert "58 assets" in printed.out and "81 registry endpoints" in printed.out
