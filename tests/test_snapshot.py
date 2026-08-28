"""A dated snapshot, and the four ways verification has to be able to fail.

A manifest that cannot report a mismatch is a receipt for nothing, so every mismatch class gets
a test that corrupts exactly one thing about a snapshot that verified a moment earlier. The
unmodified snapshot verifying is the positive control they all lean on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhir_scorecard.cli import main
from fhir_scorecard.snapshot import (
    DATASET_FILES,
    MANIFEST_NAME,
    Manifest,
    Mismatch,
    build,
    sha256_of,
    verify,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def site(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(ROOT / "tests" / "fixtures"),
                "--registry",
                str(ROOT / "tests" / "fixtures" / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "history.json"),
            ]
        )
        == 0
    )
    return out


@pytest.fixture
def snapshot(site: Path, tmp_path: Path) -> Path:
    build(site, tmp_path / "snap", "2026-08-27")
    return tmp_path / "snap"


# --- what a snapshot holds ---


def test_a_snapshot_carries_the_dataset_and_not_the_pages(snapshot: Path) -> None:
    """A page is a rendering of the data and changes when the templates change. The dataset is
    the thing somebody would cite."""
    for name in DATASET_FILES:
        assert (snapshot / name).is_file(), name
    assert not list(snapshot.rglob("*.html"))
    assert (snapshot / "api" / "endpoint").is_dir()


def test_the_manifest_records_a_size_and_digest_for_every_file(snapshot: Path) -> None:
    manifest = json.loads((snapshot / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["date"] == "2026-08-27"
    assert manifest["files"]
    for relative, record in manifest["files"].items():
        target = snapshot / relative
        assert record["bytes"] == target.stat().st_size
        assert record["sha256"] == sha256_of(target)


def test_two_builds_of_one_site_are_byte_identical(site: Path, tmp_path: Path) -> None:
    """The property that makes dating the artifact meaningful: a reader who rebuilds can tell
    that nothing moved."""
    first = build(site, tmp_path / "a", "2026-08-27")
    second = build(site, tmp_path / "b", "2026-08-27")
    assert first.to_json() == second.to_json()
    assert (tmp_path / "a" / MANIFEST_NAME).read_bytes() == (
        tmp_path / "b" / MANIFEST_NAME
    ).read_bytes()


def test_a_dated_snapshot_is_written_once(site: Path, tmp_path: Path) -> None:
    """Overwriting one in place would let a snapshot change after its date, which is the single
    thing dating it is supposed to prevent."""
    build(site, tmp_path / "snap", "2026-08-27")
    with pytest.raises(FileExistsError, match="written once"):
        build(site, tmp_path / "snap", "2026-08-27")


def test_a_site_with_no_dataset_is_refused_rather_than_dated(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="nothing to snapshot"):
        build(empty, tmp_path / "snap", "2026-08-27")


def test_a_dataset_file_the_build_did_not_write_is_named_not_skipped(
    site: Path, tmp_path: Path
) -> None:
    """A snapshot holding less than the previous one has to be readable as such without diffing
    two manifests."""
    (site / "scorecards.json").unlink()
    manifest = build(site, tmp_path / "snap", "2026-08-27")
    assert "scorecards.json" in manifest.missing
    assert "scorecards.json" not in manifest.files
    assert "scorecards.json" in (tmp_path / "snap" / MANIFEST_NAME).read_text(encoding="utf-8")


# --- verification, from both sides ---


def test_an_untouched_snapshot_verifies(snapshot: Path) -> None:
    assert verify(snapshot) == []


def test_a_changed_byte_is_caught(snapshot: Path) -> None:
    target = snapshot / "dataset.csv"
    body = target.read_bytes()
    target.write_bytes(body[:-1] + bytes([body[-1] ^ 0x01]))
    reasons = [m.reason for m in verify(snapshot)]
    assert any("hashes to" in reason for reason in reasons), reasons


def test_a_changed_length_is_caught_as_well_as_a_changed_digest(snapshot: Path) -> None:
    (snapshot / "dataset.csv").write_bytes(b"truncated")
    reasons = " ".join(m.reason for m in verify(snapshot))
    assert "bytes, manifest records" in reasons
    assert "hashes to" in reasons


def test_a_deleted_file_is_caught(snapshot: Path) -> None:
    (snapshot / "api" / "index.json").unlink()
    assert [str(m) for m in verify(snapshot)] == [
        "api/index.json: named by the manifest and not in the snapshot"
    ]


def test_a_file_added_after_the_fact_is_caught(snapshot: Path) -> None:
    """Otherwise a manifest would certify a directory it had never seen the whole of."""
    (snapshot / "api" / "smuggled.json").write_text("{}", encoding="utf-8")
    assert [str(m) for m in verify(snapshot)] == [
        "api/smuggled.json: is in the snapshot and not named by the manifest"
    ]


def test_a_missing_manifest_is_not_a_pass(snapshot: Path) -> None:
    (snapshot / MANIFEST_NAME).unlink()
    assert verify(snapshot) == [
        Mismatch(MANIFEST_NAME, "no manifest, so there is nothing to verify against")
    ]


def test_an_unreadable_manifest_is_not_a_pass(snapshot: Path) -> None:
    (snapshot / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert [m.path for m in verify(snapshot)] == [MANIFEST_NAME]


def test_a_manifest_recording_nothing_is_not_a_pass(snapshot: Path) -> None:
    """An empty file list would otherwise verify trivially: nothing to check, nothing wrong."""
    (snapshot / MANIFEST_NAME).write_text(json.dumps({"files": {}}), encoding="utf-8")
    assert [m.reason for m in verify(snapshot)] == ["records no files"]


def test_the_manifest_is_checkable_without_this_program(snapshot: Path) -> None:
    """Plain JSON of relative path to size and digest. A receipt only this code could read
    would not be a receipt."""
    manifest = json.loads((snapshot / MANIFEST_NAME).read_text(encoding="utf-8"))
    relative, record = next(iter(sorted(manifest["files"].items())))
    import hashlib

    assert hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() == record["sha256"]


# --- the commands ---


def test_the_commands_round_trip(site: Path, tmp_path: Path) -> None:
    out = tmp_path / "dated"
    assert main(["snapshot", str(site), "--out", str(out), "--date", "2026-08-27"]) == 0
    assert main(["verify-snapshot", str(out)]) == 0


def test_verify_exits_nonzero_on_a_corrupted_snapshot(snapshot: Path) -> None:
    (snapshot / "dataset.csv").write_bytes(b"tampered")
    assert main(["verify-snapshot", str(snapshot)]) == 1


def test_a_usage_error_is_exit_two_and_never_reads_as_a_clean_snapshot(tmp_path: Path) -> None:
    assert (
        main(["snapshot", str(tmp_path / "absent"), "--out", str(tmp_path / "o"), "--date", "d"])
        == 2
    )
    assert main(["verify-snapshot", str(tmp_path / "absent")]) == 2


def test_building_over_an_existing_directory_is_a_usage_error(site: Path, tmp_path: Path) -> None:
    out = tmp_path / "dated"
    out.mkdir()
    assert main(["snapshot", str(site), "--out", str(out), "--date", "2026-08-27"]) == 2


def test_the_command_reports_what_the_build_did_not_carry(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (site / "scorecards.json").unlink()
    assert main(["snapshot", str(site), "--out", str(tmp_path / "d"), "--date", "2026-08-27"]) == 0
    assert "not in this build" in capsys.readouterr().out


def test_a_mismatch_renders_with_its_path() -> None:
    assert str(Mismatch("api/index.json", "gone")) == "api/index.json: gone"


def test_a_manifest_dataclass_serialises_deterministically() -> None:
    one = Manifest("2026-08-27", {"b": {"bytes": 1}, "a": {"bytes": 2}}, ("x",))
    two = Manifest("2026-08-27", {"a": {"bytes": 2}, "b": {"bytes": 1}}, ("x",))
    assert one.to_json() == two.to_json()
    assert one.to_json().endswith("\n")


def test_an_empty_dataset_directory_is_missing_not_present(site: Path, tmp_path: Path) -> None:
    """A directory that exists and holds nothing is the same result as one that does not exist:
    the build carried no per-endpoint records. Treating "the folder is there" as coverage is how
    a snapshot comes to certify an absence."""
    endpoint_dir = site / "api" / "endpoint"
    for path in endpoint_dir.iterdir():
        path.unlink()
    manifest = build(site, tmp_path / "snap", "2026-08-27")
    assert "api/endpoint" in manifest.missing
    assert not any(name.startswith("api/endpoint/") for name in manifest.files)
    assert verify(tmp_path / "snap") == []
