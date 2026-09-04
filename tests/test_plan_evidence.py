"""The planning and status documents must agree with the data they argue from.

Issue #25 found ROADMAP.md building its sequencing argument on 19 endpoints and a
seven-of-nine anecdote while ``data/registry.json`` held 30, because nothing tied the
document that decides what gets built next to the data it decides from. These tests
recompute every load-bearing figure in ROADMAP.md, the README's cohort and status
sections, and docs/SAMPLING-FRAME.md's frame table from the committed data - the
registry, the cohort files, and the roster CSVs - so the next drift fails the build
instead of waiting for a reader.

The rosters are evidence, not decoration: each marketplace cohort's denominator is a
committed CSV derived from CMS's QHP Landscape file, each member carries the exact
``roster_name`` CMS prints, and the correspondence is asserted in both directions, so
a member that joined without a roster row and a roster row that lost its member both
fail. The per-state rosters must also be exact state-slices of the national frame CSV,
so the three files cannot drift apart from one another.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from fhir_scorecard.cohort import PROGRAMS, load_cohort_dir
from fhir_scorecard.registry import load_registry
from fhir_scorecard.site import _PROGRAM_LABELS

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "registry.json"
COHORT_DIR = ROOT / "data" / "cohorts"
FRAME_CSV = ROOT / "data" / "frames" / "qhp-landscape-py2026-individual-medical.csv"


def _norm(text: str) -> str:
    """Collapse whitespace so a claim wrapped across source lines still matches."""
    return " ".join(text.split())


ROADMAP = _norm((ROOT / "ROADMAP.md").read_text(encoding="utf-8"))
README = _norm((ROOT / "README.md").read_text(encoding="utf-8"))
SAMPLING_FRAME = _norm((ROOT / "docs" / "SAMPLING-FRAME.md").read_text(encoding="utf-8"))

#: Cohorts whose denominator is a committed roster CSV, and the state that slices the
#: national frame down to it.
ROSTERED_COHORTS = {"texas-marketplace": "TX", "florida-marketplace": "FL"}

#: Spelled-out numbers the prose uses, so a sentence written in words stays tied to the
#: count it came from.
WORDS = {
    2: "two",
    3: "three",
    5: "five",
    6: "six",
    8: "eight",
    9: "nine",
    15: "fifteen",
    23: "twenty-three",
    27: "twenty-seven",
    40: "forty",
    45: "forty-five",
    57: "fifty-seven",
}


def _registry():
    return load_registry(REGISTRY)


def _cohorts():
    eps = _registry()
    return {
        c.cohort_id: c for c in load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in eps))
    }


def _read_roster(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _roster_stats(rows: list[dict[str, str]]) -> tuple[int, int, int]:
    """(organizations, HIOS issuer IDs, plan-county rows) for a roster slice."""
    hios = {h for r in rows for h in r["hios_issuer_ids"].split()}
    return len(rows), len(hios), sum(int(r["plan_county_rows"]) for r in rows)


def test_every_program_tag_has_a_site_label() -> None:
    # A tag with no label renders as a word nobody wrote (see cohort.PROGRAMS).
    assert set(_PROGRAM_LABELS) >= PROGRAMS


def test_cohort_members_are_exactly_the_roster_in_both_directions() -> None:
    cohorts = _cohorts()
    for cohort_id, state in ROSTERED_COHORTS.items():
        roster = _read_roster(COHORT_DIR / f"{cohort_id}.roster.csv")
        assert all(r["state_code"] == state for r in roster)
        roster_names = {r["issuer_name"] for r in roster}
        raw = json.loads((COHORT_DIR / f"{cohort_id}.json").read_text(encoding="utf-8"))
        member_roster_names = {m["roster_name"] for m in raw["members"]}
        assert member_roster_names == roster_names, cohort_id
        assert len(raw["members"]) == len(roster), cohort_id
        assert len(cohorts[cohort_id].members) == len(roster), cohort_id


def test_state_rosters_are_exact_slices_of_the_national_frame() -> None:
    frame = _read_roster(FRAME_CSV)
    for cohort_id, state in ROSTERED_COHORTS.items():
        state_slice = [r for r in frame if r["state_code"] == state]
        roster = _read_roster(COHORT_DIR / f"{cohort_id}.roster.csv")
        assert roster == state_slice, cohort_id


def test_the_prose_roster_totals_are_the_csv_totals() -> None:
    # The sentences that cite each roster carry (rows, HIOS IDs, organizations); build
    # the exact strings from the CSVs and require them verbatim where they are claimed.
    tx_orgs, tx_hios, tx_rows = _roster_stats(
        _read_roster(COHORT_DIR / "texas-marketplace.roster.csv")
    )
    fl_orgs, fl_hios, fl_rows = _roster_stats(
        _read_roster(COHORT_DIR / "florida-marketplace.roster.csv")
    )
    tx_claim = f"{tx_rows:,} plan-county rows, {tx_hios} HIOS issuer IDs"
    fl_claim = f"{fl_rows:,} plan-county rows, {fl_hios} HIOS issuer IDs"
    assert tx_claim in README and f"**{tx_orgs} issuer organizations**" in README
    assert fl_claim in README and f"**{fl_orgs} issuer organizations**" in README
    assert fl_claim in SAMPLING_FRAME
    fl_cohort_notes = " ".join(
        json.loads((COHORT_DIR / "florida-marketplace.json").read_text(encoding="utf-8"))["notes"]
    )
    assert f"{fl_rows:,} plan-county rows, {fl_hios} HIOS issuer IDs" in fl_cohort_notes
    assert f"{fl_orgs} issuer names" in fl_cohort_notes


def test_the_national_frame_arithmetic_is_recomputed_everywhere_it_is_cited() -> None:
    frame = _read_roster(FRAME_CSV)
    states = {r["state_code"] for r in frame}
    orgs, hios, rows = _roster_stats(frame)
    reviewed = [r for r in frame if r["state_code"] in ROSTERED_COHORTS.values()]
    unreviewed = len(frame) - len(reviewed)

    frame_claim = f"{orgs} state-issuer organizations"
    assert f"{frame_claim} across {len(states)} states" in ROADMAP
    assert f"reviewed {len(reviewed)}" in ROADMAP
    assert f"other {unreviewed} are *not yet reviewed*" in ROADMAP
    assert f"{frame_claim} across {len(states)} federally-facilitated-exchange states" in README
    assert f"reviewed {len(reviewed)}; the other {unreviewed}" in README
    assert (
        f"{len(states)} states, {orgs} state-issuer organizations, "
        f"{hios} HIOS issuer IDs, {rows:,} plan-county rows" in SAMPLING_FRAME
    )
    assert (
        f"{len(ROSTERED_COHORTS)} of {len(states)} states, "
        f"{len(reviewed)} of {orgs} state-issuer organizations reviewed" in SAMPLING_FRAME
    )


def test_the_roadmap_counts_are_the_registry_and_cohort_counts() -> None:
    eps = _registry()
    cohorts = _cohorts()
    total_members = sum(len(c.members) for c in cohorts.values())
    total_included = sum(len(c.included) for c in cohorts.values())

    assert f"**{len(eps)} endpoints**" in ROADMAP
    assert f"across **{WORDS[len(cohorts)]} cohort pages**" in ROADMAP
    assert f"**{total_included} of {total_members} roster organizations" in ROADMAP
    ca, tx, fl = (
        cohorts["california"],
        cohorts["texas-marketplace"],
        cohorts["florida-marketplace"],
    )
    assert (
        f"California's {len(ca.members)} organizations, Texas's {len(tx.members)}, "
        f"Florida's {len(fl.members)}" in ROADMAP
    )
    assert f"{WORDS[len(eps)].capitalize()} endpoints with a defensible method" in ROADMAP
    # The stale figure this test exists to prevent from returning.
    assert "**19 endpoints**" not in ROADMAP
    assert "Nineteen endpoints" not in ROADMAP


def test_the_readme_status_paragraph_is_recomputed() -> None:
    eps = _registry()
    cohorts = _cohorts()
    basis = Counter(e.verification_basis for e in eps)
    assert f"{WORDS[len(eps)].capitalize()} endpoints across" in README
    assert f"in {WORDS[len(cohorts)]} curated cohorts" in README
    assert (
        f"{WORDS[basis['live_capability']].capitalize()} were verified from a retrieved "
        f"conformance document; {WORDS[basis['publisher_documented']]} are listed on the "
        "organization's own publication of a base URL that does not answer" in README
    )


def test_the_readme_cohort_hit_rates_are_the_cohort_files() -> None:
    cohorts = _cohorts()
    ca, tx, fl = (
        cohorts["california"],
        cohorts["texas-marketplace"],
        cohorts["florida-marketplace"],
    )
    assert f"deduplicated to {len(ca.members)} organizations" in README
    assert f"{WORDS[len(ca.included)].capitalize()} publish a base URL" in README
    assert f"{WORDS[len(tx.included)].capitalize()} publish a base URL" in README
    assert (
        f"{WORDS[len(fl.included)].capitalize()} of the {WORDS[len(fl.members)]} publish" in README
    )


def test_florida_members_sharing_a_family_share_its_endpoints() -> None:
    # The cohort note promises the corporate-sibling pairs point at the same endpoints;
    # promise, meet data.
    raw = json.loads((COHORT_DIR / "florida-marketplace.json").read_text(encoding="utf-8"))
    members = {m["id"]: m for m in raw["members"]}
    assert members["florida-blue"]["endpoints"] == members["florida-blue-hmo"]["endpoints"]
    assert (
        members["cigna-healthcare"]["endpoints"]
        == members["cigna-healthcare-of-florida"]["endpoints"]
    )


def test_the_coverage_populations_are_the_ones_the_docs_print() -> None:
    """The four populations, each recomputed and each found in the prose that states it.

    ``test_plan_evidence`` never imported ``coverage``, so ``/coverage/`` - the page carrying
    this project's central distinction between four things that must never be merged - was the
    one published surface no doc-drift test covered. README and ROADMAP both print the split.
    """
    from fhir_scorecard.coverage import (
        DOCUMENTED_UNREACHABLE,
        NO_PUBLIC_URL_FOUND,
        NOT_YET_REVIEWED,
        VERIFIED,
        classify,
        counts,
        read_frame,
        read_reviewed_rows_by_cohort,
    )

    endpoints = [e for e in load_registry(REGISTRY) if e.enabled]
    cohorts = load_cohort_dir(COHORT_DIR, frozenset(e.endpoint_id for e in endpoints))
    tally = counts(
        classify(
            read_frame(FRAME_CSV), cohorts, endpoints, read_reviewed_rows_by_cohort(COHORT_DIR)
        )
    )

    assert tally[VERIFIED] == 13
    assert tally[DOCUMENTED_UNREACHABLE] == 2
    assert tally[NO_PUBLIC_URL_FOUND] == 15
    assert tally[NOT_YET_REVIEWED] == 146

    # README states them as a parenthesised run; ROADMAP as a named list. Both must carry the
    # computed numbers, not merely a total that two of them could be traded within.
    assert f"retrieved from ({tally[VERIFIED]})" in README
    assert f"did not answer ({tally[DOCUMENTED_UNREACHABLE]})" in README
    assert f"a stranger can read ({tally[NO_PUBLIC_URL_FOUND]})" in README
    assert f"not yet reviewed ({tally[NOT_YET_REVIEWED]})" in README

    assert f"{tally[VERIFIED]} verified" in ROADMAP
    assert f"{tally[DOCUMENTED_UNREACHABLE]} documented but unreachable" in ROADMAP
    assert f"{tally[NOT_YET_REVIEWED]} not yet reviewed" in ROADMAP


def test_a_shipped_phase_is_not_described_as_future_work() -> None:
    """The three-year section was a plan that got executed and never updated to say so.

    It read "none of it is browsable. This phase renders it" while phase 2 above it read "the
    record is now browsable at /history/", and "The floor is not met across the registry today"
    against phase 5's "the floor puts 30 of 45 endpoints in the tables". A reader uses this
    document to judge what is real, so a phase whose module ships has to say it shipped.
    """
    delivered = {
        6: "audit",
        7: "accessibility",
        8: "archive",
        9: "drift",
        10: "leaderboard",
        11: "coverage",
        12: "over_time",
        13: "snapshot",
    }
    src = ROOT / "src" / "fhir_scorecard"
    for phase, module in delivered.items():
        assert (src / f"{module}.py").is_file(), module
        heading = f"### Phase {phase}:"
        assert heading in ROADMAP, heading
        # ROADMAP is whitespace-normalised, so a phase's section runs from its heading to the
        # next one. The delivery statement must sit inside it, naming the module that ships.
        section = ROADMAP.split(heading, 1)[1].split("### Phase ", 1)[0]
        assert "**Delivered 20" in section, f"phase {phase} claims no delivery"
        assert f"fhir_scorecard/{module}.py" in section, f"phase {phase} names no module"

    # Phases 14 and 15 are blocked on something outside this repository and must stay that way,
    # not quietly acquire a delivery line.
    for phase in (14, 15):
        section = ROADMAP.split(f"### Phase {phase}:", 1)[1].split("### Phase ", 1)[0]
        assert "*Blocked on" in section, phase
        assert "**Delivered" not in section, phase
