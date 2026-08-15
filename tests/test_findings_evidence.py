"""The published findings must agree with the evidence beside them.

A findings document that drifts from its own data is worse than no findings document, so
every number in ``docs/findings/`` is recomputed here from ``data/cohorts/california.json``,
``data/registry.json`` and the classification artifact, rather than trusted. The same tests
guard two things a write-up could quietly get wrong on its way out: naming an organization
that is not on the roster, and inflating what this project's measurements can support.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COHORT = ROOT / "data" / "cohorts" / "california.json"
REGISTRY = ROOT / "data" / "registry.json"
FINDINGS = ROOT / "docs" / "findings"
CLASSIFICATION = FINDINGS / "2026-08-15-california-payer-cohort.json"
COHORT_WRITEUP = FINDINGS / "2026-08-15-california-payer-cohort.md"
ANTHEM_WRITEUP = FINDINGS / "2026-08-15-anthem-multi-tenant-attribution.md"

#: The outcome vocabulary the write-up counts by, and the words its roster table uses for
#: each. A value outside this map means a classification nothing in the prose accounts for.
OUTCOME_WORDS = {
    "no_public_base_url": "no public base URL",
    "documented_unreachable": "documented, unreachable",
    "sandbox_only": "sandbox only",
    "answered_unattributable": "answers, unattributable",
}

ATTRIBUTION_KINDS = {
    "publisher_names_the_plan",
    "only_the_implementation_description_names_the_plan",
    "names_the_vendor_not_the_plan",
    "names_no_deployment",
}

PROGRAM_LABELS = {"medi-cal": "Medi-Cal", "covered-ca": "Covered California"}

#: Spelled-out numbers the prose uses, so a sentence written in words is still tied to the
#: count it came from.
WORDS = {
    3: "three",
    4: "four",
    6: "six",
    7: "seven",
    8: "eight",
    11: "eleven",
    12: "twelve",
    18: "eighteen",
    19: "nineteen",
    23: "twenty-three",
    27: "twenty-seven",
}


def _json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _members() -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = _json(COHORT)["members"]
    return members


def _excluded() -> list[dict[str, Any]]:
    return [m for m in _members() if "excluded" in m]


def _included() -> list[dict[str, Any]]:
    return [m for m in _members() if m.get("endpoints")]


def _classified() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = _json(CLASSIFICATION)["excluded"]
    return records


def _listed_endpoints() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = _json(CLASSIFICATION)["listed_endpoints"]
    return records


def _rows(text: str) -> list[list[str]]:
    """Every markdown table row, as its stripped cells."""
    return [
        [cell.strip() for cell in line.split("|")]
        for line in text.splitlines()
        if line.startswith("|")
    ]


def _headline(text: str) -> dict[str, int]:
    """Label -> count, from every table row whose second cell is a bare integer."""
    return {
        cells[1]: int(cells[2])
        for cells in _rows(text)
        if len(cells) >= 4 and re.fullmatch(r"\d+", cells[2])
    }


def _flat(path: Path) -> str:
    """The document with its line wrapping removed, so a wrapped sentence still matches."""
    return " ".join(path.read_text(encoding="utf-8").split())


def _programs(member: dict[str, Any]) -> str:
    return ", ".join(PROGRAM_LABELS[p] for p in member["programs"])


# --- the classification cannot drift from the cohort it classifies ----------------------


def test_the_classification_covers_every_excluded_member_exactly_once() -> None:
    classified = [record["member_id"] for record in _classified()]
    assert len(classified) == len(set(classified)), "a member is classified twice"
    assert set(classified) == {m["id"] for m in _excluded()}


def test_every_classification_excerpt_is_still_verbatim_in_the_cohort() -> None:
    reasons = {m["id"]: m["excluded"]["reason"] for m in _excluded()}
    for record in _classified():
        member_id = record["member_id"]
        assert record["reason_excerpt"] in reasons[member_id], (
            f"{member_id}: the clause this outcome rests on is no longer in the committed "
            "reason; the classification has to be revisited, not the excerpt"
        )


def test_the_outcome_vocabulary_is_closed() -> None:
    declared = set(_json(CLASSIFICATION)["outcomes"])
    assert declared == set(OUTCOME_WORDS)
    assert {record["outcome"] for record in _classified()} <= declared


def test_the_attribution_covers_every_listed_endpoint_exactly_once() -> None:
    listed = [eid for m in _included() for eid in m["endpoints"]]
    classified = [record["endpoint_id"] for record in _listed_endpoints()]
    assert sorted(classified) == sorted(listed)
    assert {record["attribution"] for record in _listed_endpoints()} <= ATTRIBUTION_KINDS
    assert set(_json(CLASSIFICATION)["attributions"]) == ATTRIBUTION_KINDS


def test_every_attribution_excerpt_is_still_verbatim_in_the_registry() -> None:
    methods = {e["id"]: e["verification"]["method"] for e in _json(REGISTRY)["endpoints"]}
    for record in _listed_endpoints():
        endpoint_id = record["endpoint_id"]
        assert record["method_excerpt"] in methods[endpoint_id], (
            f"{endpoint_id}: the verification clause this attribution rests on has changed"
        )


# --- the write-up cannot drift from either ----------------------------------------------


def test_the_headline_numbers_match_the_evidence() -> None:
    outcomes = Counter(record["outcome"] for record in _classified())
    basis = Counter(m["excluded"]["basis"] for m in _excluded())
    attribution = Counter(record["attribution"] for record in _listed_endpoints())
    expected = {
        "Listed, with a base URL this project verified": len(_included()),
        "Not listed": len(_excluded()),
        "Publishes no base URL readable without an account": outcomes["no_public_base_url"],
        "Publishes a base URL that did not resolve or did not answer": outcomes[
            "documented_unreachable"
        ],
        "Publishes a sandbox base URL only, production unfilled": outcomes["sandbox_only"],
        "Publishes a base URL that answers but cannot be attributed to the plan": outcomes[
            "answered_unattributable"
        ],
        "The plan's own documentation was retrieved (`portal_reviewed`)": basis["portal_reviewed"],
        "No interoperability documentation could be located at all (`not_located`)": basis[
            "not_located"
        ],
        "The `publisher` field names the plan": attribution["publisher_names_the_plan"],
        "`publisher` is empty and only `implementation.description` names the plan": (
            attribution["only_the_implementation_description_names_the_plan"]
        ),
        "`publisher` names the platform vendor, not the plan": attribution[
            "names_the_vendor_not_the_plan"
        ],
        "The document names no deployment at all": attribution["names_no_deployment"],
    }
    headline = _headline(COHORT_WRITEUP.read_text(encoding="utf-8"))
    for label, count in expected.items():
        assert headline.get(label) == count, label
    assert sum(outcomes.values()) == len(_excluded()), "the outcomes do not partition the 19"


def test_the_prose_counts_match_the_evidence() -> None:
    members = _members()
    included = _included()
    excluded = _excluded()
    outcomes = Counter(record["outcome"] for record in _classified())
    attribution = Counter(record["attribution"] for record in _listed_endpoints())
    medi_cal = [m for m in members if "medi-cal" in m["programs"]]
    covered_ca = [m for m in members if "covered-ca" in m["programs"]]
    both = [m for m in members if len(m["programs"]) == 2]
    endpoints = sum(len(m["endpoints"]) for m in included)
    named = attribution["publisher_names_the_plan"]
    text = _flat(COHORT_WRITEUP)

    for sentence in (
        f"{WORDS[len(members)].capitalize()} organizations:",
        f"{WORDS[len(medi_cal)].capitalize()} are Medi-Cal plans, "
        f"{WORDS[len(covered_ca)]} are Covered California issuers, "
        f"and {WORDS[len(both)]} are both.",
        f"The {WORDS[len(included)]} listed plans account for {WORDS[endpoints]} verified "
        f"endpoints, {WORDS[len([m for m in included if 'medi-cal' in m['programs']])]} "
        f"Medi-Cal plans and "
        f"{WORDS[len([m for m in included if 'covered-ca' in m['programs']])]} "
        f"Covered California issuers, "
        f"{WORDS[len([m for m in included if len(m['programs']) == 2])]} of which are both.",
        f"{WORDS[len({m['excluded']['reviewed']['source'] for m in excluded})].capitalize()} "
        "distinct sources cover them",
        f"{WORDS[len(excluded)].capitalize()} of {len(members)} organizations could not be listed.",
        f"{WORDS[len([m for m in included if 'medi-cal' in m['programs']])].capitalize()} of "
        f"{len(medi_cal)} Medi-Cal managed care plans are listed, and "
        f"{WORDS[len([m for m in included if 'covered-ca' in m['programs']])]} of "
        f"{len(covered_ca)} Covered California issuers",
        f"{WORDS[outcomes['no_public_base_url']].capitalize()} plans published no base URL",
        f"{WORDS[len(excluded) - outcomes['no_public_base_url']].capitalize()} plans did "
        "publish a base URL",
        f"For {WORDS[endpoints - named]} of {WORDS[endpoints]}, the CapabilityStatement's "
        "`publisher` field does not name the plan",
    ):
        assert sentence in text, sentence


def test_the_write_up_dates_every_review_from_the_data() -> None:
    dates = {m["excluded"]["reviewed"]["date"] for m in _excluded()}
    assert len(dates) == 1, "the reviews no longer share one date; the write-up says they do"
    assert f"completed on {dates.pop()}" in _flat(COHORT_WRITEUP)


def test_the_grouping_behind_finding_2_matches_the_committed_reasons() -> None:
    reasons = [m["excluded"]["reason"] for m in _excluded()]
    shared_platform = Counter(reasons)[
        "developer portal published on a shared vendor platform; no base URL is rendered to "
        "an unregistered visitor"
    ]
    partner_portal = sum(
        1 for r in reasons if "routes developers to the Centene partner portal" in r
    )
    text = _flat(COHORT_WRITEUP)
    assert shared_platform == 5 and partner_portal == 2, (
        "the reasons regrouped; Finding 2's sentences have to be rewritten from them"
    )
    assert "Five of them route developers to a developer portal on a shared vendor" in text
    assert "Two more route to the same partner portal" in text


# --- the roster tables are the roster ---------------------------------------------------


def test_the_excluded_roster_table_is_the_excluded_set_with_its_outcomes() -> None:
    outcome_of = {r["member_id"]: r["outcome"] for r in _classified()}
    expected = {m["name"]: (_programs(m), OUTCOME_WORDS[outcome_of[m["id"]]]) for m in _excluded()}
    found = {
        cells[1]: (cells[2], cells[3])
        for cells in _rows(COHORT_WRITEUP.read_text(encoding="utf-8"))
        if len(cells) == 5 and cells[3] in OUTCOME_WORDS.values()
    }
    assert found == expected


def test_the_listed_roster_table_is_the_listed_set_with_its_endpoint_counts() -> None:
    expected = {m["name"]: (_programs(m), str(len(m["endpoints"]))) for m in _included()}
    found = {
        cells[1]: (cells[2], cells[3])
        for cells in _rows(COHORT_WRITEUP.read_text(encoding="utf-8"))
        if len(cells) == 5 and re.fullmatch(r"\d+", cells[3]) and cells[1] in expected
    }
    assert found == expected


# --- what the write-ups are not allowed to say ------------------------------------------


def test_no_finding_claims_more_independence_than_the_probing_has() -> None:
    """Every vantage is a GitHub-hosted runner on one provider's network, and the published
    wording is held to "one network" until a genuinely independent vantage exists. A findings
    document is the easiest place for that to quietly inflate."""
    forbidden = (
        "several vantages",
        "independent networks",
        "multiple networks",
        "three networks",
        "independent vantages",
        "three independent",
    )
    for path in sorted(FINDINGS.glob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in text, f"{path.name} claims '{phrase}'"
        assert "one network" in text, (
            f"{path.name} reports observations without saying they came from one network"
        )


def test_no_finding_makes_a_compliance_claim() -> None:
    for path in (COHORT_WRITEUP, ANTHEM_WRITEUP):
        text = _flat(path)
        assert "compliance determination" in text, f"{path.name} does not disclaim one"
        assert "not compliant" not in text.lower(), f"{path.name} uses a regulator's word"
        assert "non-compliant" not in text.lower(), f"{path.name} uses a regulator's word"


def test_the_anthem_write_up_matches_the_committed_observation() -> None:
    member = next(m for m in _excluded() if m["id"] == "anthem-blue-cross")
    reason = member["excluded"]["reason"]
    reviewed = member["excluded"]["reviewed"]
    text = ANTHEM_WRITEUP.read_text(encoding="utf-8")
    for brand in ("AnthemBlueCrossBlueShield", "AnthemBlueCross", "Wellpoint"):
        assert brand in reason, f"{brand} is no longer in the committed reason"
        assert brand in text, f"{brand} is not in the write-up"
    assert "Elevance Health, Inc" in reviewed["method"]
    assert reviewed["date"] in text, "the write-up does not carry the observation's date"
    assert "thirteen brands" in reviewed["method"] and "thirteen" in text
