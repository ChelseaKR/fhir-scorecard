"""A dated, hash-manifested copy of the published dataset.

ROADMAP phase 2 asked for *"a monthly dated dataset release, signed"*. This is the half a
program can produce: the artifact. The signature is not, and phase 13 of the plan says why -
`.github/workflows/release.yml` publishes only from an SSH-signed annotated tag verified against
`.github/allowed_signers`, and only the holder of that key can make one. A workflow written to
publish an unsigned snapshot would be a release path that skips the control every other release
in this repository passes, so this stops at the artifact.

What a snapshot is: the machine-readable files of one published build, copied under a stated
date, with a manifest recording every file's size and SHA-256. Not the HTML. A page is a
rendering of the data and changes when the templates change; the dataset is the thing somebody
would cite, and it is what a dated copy should hold.

Two properties make the artifact worth dating.

**Reproducible.** Building a snapshot twice from one site produces byte-identical files and an
identical manifest, so a reader who rebuilds can tell that nothing moved. The manifest is
written with sorted keys and a fixed separator for that reason.

**Checkable without this tool.** The manifest is plain JSON of relative paths to
``{"bytes": n, "sha256": "..."}``. :func:`verify` walks it, but so can `sha256sum`; a manifest
only this program can read would be a receipt nobody can audit.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Name of the manifest inside a snapshot directory.
MANIFEST_NAME = "manifest.json"

#: The published files a snapshot carries, relative to a built site. A path that is absent from
#: the build is absent from the snapshot and named in the manifest's ``missing`` list rather
#: than silently skipped: a dated copy that quietly held less than the one before it would be
#: indistinguishable from a build that stopped emitting a file.
DATASET_FILES: tuple[str, ...] = (
    "dataset.csv",
    "dataset.schema.json",
    "scorecards.json",
    "api/index.json",
)

#: Directories copied whole, relative to a built site.
DATASET_TREES: tuple[str, ...] = ("api/endpoint", "api/history")

#: How many bytes to hash at a time. A published scorecards.json is small today; reading a file
#: in chunks costs nothing and keeps a large one from being held in memory.
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Manifest:
    """What one snapshot holds. Returned by :func:`build` so a caller needs no cast to read it."""

    date: str
    files: dict[str, dict[str, object]]
    #: Dataset paths the build did not carry. Named rather than omitted: a snapshot holding less
    #: than the previous one must be readable as such without diffing two manifests.
    missing: tuple[str, ...]

    def to_json(self) -> str:
        """Sorted keys and a trailing newline, so two builds of one site are byte-identical."""
        return (
            json.dumps(
                {"date": self.date, "files": self.files, "missing": list(self.missing)},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


@dataclass(frozen=True)
class Mismatch:
    """One way a snapshot disagrees with its manifest."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(site: Path) -> tuple[list[str], list[str]]:
    """(relative paths present, relative paths expected but absent), both sorted."""
    present: list[str] = []
    missing: list[str] = []
    for name in DATASET_FILES:
        (present if (site / name).is_file() else missing).append(name)
    for tree in DATASET_TREES:
        root = site / tree
        if not root.is_dir():
            missing.append(tree)
            continue
        found = sorted(p.relative_to(site).as_posix() for p in root.rglob("*") if p.is_file())
        present.extend(found)
        if not found:
            missing.append(tree)
    return sorted(present), sorted(missing)


def build(site: Path, out: Path, date: str) -> Manifest:
    """Copy the dataset files of ``site`` into ``out`` and write the manifest. Returns it.

    ``out`` must not already exist. Overwriting a dated snapshot in place would let a snapshot
    change after its date, which is the one thing dating it is supposed to prevent.
    """
    if out.exists():
        raise FileExistsError(
            f"{out} already exists. A dated snapshot is written once; overwriting one would let "
            "it change after its date"
        )
    present, missing = _collect(site)
    if not present:
        raise ValueError(
            f"{site} carries none of the published dataset files, so there is nothing to "
            "snapshot. An empty snapshot would date a build that produced no dataset"
        )
    files: dict[str, dict[str, object]] = {}
    for relative in present:
        source = site / relative
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[relative] = {"bytes": source.stat().st_size, "sha256": sha256_of(source)}
    manifest = Manifest(date=date, files=files, missing=tuple(missing))
    (out / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def verify(snapshot: Path) -> list[Mismatch]:
    """Every way ``snapshot`` disagrees with its own manifest, in a stable order.

    An empty list means every file the manifest names is present with the recorded size and
    digest, and that the snapshot carries no dataset file the manifest does not name.
    """
    manifest_path = snapshot / MANIFEST_NAME
    if not manifest_path.is_file():
        return [Mismatch(MANIFEST_NAME, "no manifest, so there is nothing to verify against")]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Mismatch(MANIFEST_NAME, f"is not readable as JSON: {exc}")]
    recorded = manifest.get("files")
    if not isinstance(recorded, dict) or not recorded:
        return [Mismatch(MANIFEST_NAME, "records no files")]

    mismatches = []
    for relative, expected in sorted(recorded.items()):
        target = snapshot / relative
        if not target.is_file():
            mismatches.append(Mismatch(relative, "named by the manifest and not in the snapshot"))
            continue
        size = target.stat().st_size
        if size != expected.get("bytes"):
            mismatches.append(
                Mismatch(relative, f"is {size} bytes, manifest records {expected.get('bytes')}")
            )
        digest = sha256_of(target)
        if digest != expected.get("sha256"):
            mismatches.append(
                Mismatch(
                    relative,
                    f"hashes to {digest[:16]}..., manifest records "
                    f"{str(expected.get('sha256'))[:16]}...",
                )
            )
    on_disk = {
        p.relative_to(snapshot).as_posix()
        for p in snapshot.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }
    for extra in sorted(on_disk - set(recorded)):
        mismatches.append(Mismatch(extra, "is in the snapshot and not named by the manifest"))
    return mismatches
