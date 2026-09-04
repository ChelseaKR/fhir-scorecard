"""The README's Status paragraph counts what the registry holds, and names the real version.

Two things went wrong in that paragraph at once, in opposite directions.

The version underclaimed: it read ``v0.1.0-dev`` while ``pyproject.toml`` and
``CITATION.cff`` both said ``0.1.0``, ``v0.1.0`` was an annotated tag, and the GitHub
Release for it was published on 2026-08-16 and not marked pre-release. Underclaiming is the
same defect as overclaiming, because the sentence is a statement about the artifact and it
was false.

The counts were right and ungated. "Forty-five endpoints", "Forty were verified", "five are
listed", "three curated cohorts" are the most citable figures on the page and were
maintained by hand, which is the arrangement that produces a stale number rather than a
failing build. ``tests/test_headline_counts.py`` already makes the same argument about the
site's own headline: a count that only moves when somebody edits a file is not a
measurement.

So the paragraph is now derived. The counts come from ``data/registry.json`` through the
real loader, which is what decides ``verification_basis``, rather than from a regex over the
file. The version comes from the packaging metadata.

Not checked here: the tag and the GitHub Release, which need the network and a full clone.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from fhir_scorecard.registry import Endpoint, load_registry

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
REGISTRY = ROOT / "data" / "registry.json"
COHORTS = ROOT / "data" / "cohorts"

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "forty": 40,
    "forty-five": 45,
    "fifty-three": 53,
    "sixty-three": 63,
    "sixty-nine": 69,
    "eighty-one": 81,
    "thirteen": 13,
}
"""Only the words this paragraph actually uses, plus enough neighbours that a changed count
fails on the number rather than on a missing dictionary entry."""


def readme() -> str:
    # Newlines folded: these sentences wrap, and where they wrap is not a fact.
    return re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))


def stated(pattern: str) -> str:
    matches = re.findall(pattern, readme(), flags=re.IGNORECASE)
    assert matches, f"the README no longer states this; pattern found nothing: {pattern}"
    assert len(matches) == 1, f"pattern is ambiguous, matched {len(matches)}: {pattern}"
    return str(matches[0])


def counted(pattern: str) -> int:
    word = stated(pattern).lower()
    assert word in NUMBER_WORDS, f"the README spells a count this test cannot read: {word!r}"
    return NUMBER_WORDS[word]


def entries() -> list[Endpoint]:
    return list(load_registry(REGISTRY))


class TestTheStatusParagraphCountsTheRegistry:
    def test_it_states_how_many_endpoints_there_are(self) -> None:
        assert counted(r"([A-Za-z-]+) endpoints across payers") == len(entries())

    def test_it_states_how_many_were_verified_from_a_conformance_document(self) -> None:
        live = [e for e in entries() if e.verification_basis == "live_capability"]
        assert counted(r"([A-Za-z-]+) were verified from a retrieved") == len(live)

    def test_it_states_how_many_rest_on_the_publisher_s_own_document(self) -> None:
        documented = [e for e in entries() if e.verification_basis == "publisher_documented"]
        assert counted(r"([A-Za-z-]+) are listed on the organization's own") == len(documented)

    def test_the_two_bases_account_for_every_entry(self) -> None:
        """The paragraph splits the registry in two. If a third basis is ever added, the
        two sentences stop adding up and this says so rather than the sum quietly drifting.
        """
        live = counted(r"([A-Za-z-]+) were verified from a retrieved")
        documented = counted(r"([A-Za-z-]+) are listed on the organization's own")
        assert live + documented == len(entries())

    def test_it_states_how_many_cohorts_are_curated(self) -> None:
        cohorts = sorted(p.stem for p in COHORTS.glob("*.json"))
        assert counted(r"in ([A-Za-z-]+) curated cohorts") == len(cohorts)

    def test_it_names_the_cohorts_it_counts(self) -> None:
        """Naming three and shipping a different three would pass a count check."""
        text = readme()
        for cohort in sorted(p.stem for p in COHORTS.glob("*.json")):
            first = cohort.split("-")[0]
            assert first.lower() in text.lower(), f"the README names no cohort for {cohort}"


class TestTheStatusParagraphNamesTheReleasedVersion:
    def packaged_version(self) -> str:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        return str(data["project"]["version"])

    def test_the_status_line_names_the_packaged_version(self) -> None:
        assert stated(r"## Status (?:\s*)v(\d+\.\d+\.\d+),") == self.packaged_version()

    def test_the_citation_metadata_agrees(self) -> None:
        cited = re.search(
            r"(?m)^version:\s*(\S+)\s*$", (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        )
        assert cited is not None, "CITATION.cff declares no version"
        assert cited.group(1).strip("\"'") == self.packaged_version()

    def test_the_status_line_does_not_call_a_released_version_a_draft(self) -> None:
        """``v0.1.0-dev`` was the wrong word for a version that had shipped.

        A ``-dev``/``-rc``/``-alpha`` suffix on the packaged version would be a real
        pre-release and belongs in ``pyproject.toml``, where the check above would find it.
        Written only into the prose, it is a claim with nothing under it.
        """
        assert re.search(r"-(dev|rc\d*|alpha|beta)\b", stated(r"## Status(.{0,40})")) is None
