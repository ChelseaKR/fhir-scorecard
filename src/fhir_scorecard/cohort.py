"""Curated cohorts: named views over the registry, with the exclusions carried as data.

A cohort answers a question the kind pages cannot: not "which payer endpoints can be checked"
but "which of *these specific organizations'* endpoints can be checked". The first cohort is
California (Medi-Cal managed care plans and Covered California issuers), where the membership
list is public and finite, so the plans that publish no discoverable endpoint are as much a part
of the answer as the plans that do.

That is why an excluded member is a first-class record here rather than a missing row. Every
member either points at registry endpoints or carries an exclusion with a reason, a review
record, and a source, and the loader refuses anything that does neither or both. A cohort page
that silently listed only the members with endpoints would read as "this is the cohort", which
is the same zero-for-null confusion the registry policy exists to prevent.

Endpoint references are validated against the graded registry, so a cohort can never claim an
endpoint the registry does not stand behind: the verification burden stays where it already is,
in ``data/registry.json``, and a cohort only ever points at it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Same slug discipline as registry endpoint ids.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

# Program tags a member may carry. Not cosmetic: a cohort page states which program makes each
# plan a member, and a tag outside this set would render as an unexplained word. Adding a cohort
# therefore requires adding its tags here and their labels in ``site.py``, which is deliberate:
# the label is what a reader sees, and a tag with no label is a word nobody wrote.
PROGRAMS = {"medi-cal", "covered-ca", "tx-marketplace"}

# How far the review behind an exclusion went. Same two strengths as the registry's candidate
# work: retrieving the plan's own documentation and finding no base URL is a stronger claim than
# searching and coming up empty, and the page renders them differently.
EXCLUSION_BASES = {"portal_reviewed", "not_located"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Exclusion:
    """Why a cohort member is not listed, and how that was established."""

    reason: str
    basis: str
    method: str
    date: str
    source: str


@dataclass(frozen=True)
class CohortMember:
    member_id: str
    name: str
    programs: tuple[str, ...]
    endpoint_ids: tuple[str, ...] = ()
    exclusion: Exclusion | None = None


@dataclass(frozen=True)
class CohortSource:
    """Where the membership list itself comes from, so the cohort is checkable end to end."""

    label: str
    url: str
    date: str


@dataclass(frozen=True)
class Cohort:
    cohort_id: str
    name: str
    description: str
    notes: tuple[str, ...]
    sources: tuple[CohortSource, ...]
    members: tuple[CohortMember, ...]

    @property
    def included(self) -> tuple[CohortMember, ...]:
        return tuple(m for m in self.members if m.endpoint_ids)

    @property
    def excluded(self) -> tuple[CohortMember, ...]:
        return tuple(m for m in self.members if m.exclusion is not None)


def _require_str(where: str, item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} missing or empty")
    return value.strip()


def _parse_exclusion(where: str, raw: object) -> Exclusion:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}.excluded must be an object")
    basis = _require_str(where + ".excluded", raw, "basis")
    if basis not in EXCLUSION_BASES:
        raise ValueError(f"{where}.excluded.basis must be one of {sorted(EXCLUSION_BASES)}")
    reviewed = raw.get("reviewed")
    if not isinstance(reviewed, dict):
        raise ValueError(
            f"{where}.excluded has no review record; an exclusion without one is "
            "an assertion, not a finding"
        )
    date = _require_str(where + ".excluded.reviewed", reviewed, "date")
    if not _DATE_RE.match(date):
        raise ValueError(f"{where}.excluded.reviewed.date must be YYYY-MM-DD")
    return Exclusion(
        reason=_require_str(where + ".excluded", raw, "reason"),
        basis=basis,
        method=_require_str(where + ".excluded.reviewed", reviewed, "method"),
        date=date,
        source=_require_str(where + ".excluded.reviewed", reviewed, "source"),
    )


def _parse_programs(where: str, raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where}.programs must be a non-empty list")
    programs: list[str] = []
    for program in raw:
        if not isinstance(program, str) or program not in PROGRAMS:
            raise ValueError(f"{where}.programs entries must be one of {sorted(PROGRAMS)}")
        if program not in programs:
            programs.append(program)
    return tuple(programs)


def _parse_endpoint_ids(where: str, raw: object, registry_ids: frozenset[str]) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{where}.endpoints must be a list")
    endpoint_ids: list[str] = []
    for endpoint_id in raw:
        if not isinstance(endpoint_id, str) or endpoint_id not in registry_ids:
            raise ValueError(
                f"{where}.endpoints references {endpoint_id!r}, which is not a graded registry "
                "endpoint; a cohort only ever points at endpoints the registry stands behind"
            )
        endpoint_ids.append(endpoint_id)
    return tuple(endpoint_ids)


def _parse_member(
    where: str, item: dict[str, object], registry_ids: frozenset[str], seen: set[str]
) -> CohortMember:
    member_id = _require_str(where, item, "id")
    if not _ID_RE.match(member_id):
        raise ValueError(f"{where}.id {member_id!r} is not a lowercase slug")
    if member_id in seen:
        raise ValueError(f"duplicate cohort member id {member_id!r}")
    seen.add(member_id)

    programs = _parse_programs(where, item.get("programs"))
    endpoint_ids = _parse_endpoint_ids(where, item.get("endpoints", []), registry_ids)
    excluded_raw = item.get("excluded")
    # Exactly one of the two, enforced rather than assumed: a member with endpoints and an
    # exclusion is contradictory, and a member with neither is a claim with no evidence either
    # way, which is precisely the state this file exists to make unrepresentable.
    if endpoint_ids and excluded_raw is not None:
        raise ValueError(f"{where} has both endpoints and an exclusion; it cannot be both")
    if not endpoint_ids and excluded_raw is None:
        raise ValueError(
            f"{where} has neither endpoints nor an exclusion; every member must "
            "carry one or the other"
        )

    return CohortMember(
        member_id=member_id,
        name=_require_str(where, item, "name"),
        programs=programs,
        endpoint_ids=endpoint_ids,
        exclusion=_parse_exclusion(where, excluded_raw) if excluded_raw is not None else None,
    )


def _parse_sources(raw: object) -> tuple[CohortSource, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("cohort.sources must be a list")
    sources: list[CohortSource] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"cohort.sources[{i}] is not an object")
        where = f"cohort.sources[{i}]"
        date = _require_str(where, item, "date")
        if not _DATE_RE.match(date):
            raise ValueError(f"{where}.date must be YYYY-MM-DD")
        sources.append(
            CohortSource(
                label=_require_str(where, item, "label"),
                url=_require_str(where, item, "url"),
                date=date,
            )
        )
    return tuple(sources)


def load_cohort(path: Path, registry_ids: frozenset[str]) -> Cohort:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cohort"), dict):
        raise ValueError(f"{path}: expected an object with a 'cohort' object")
    head = raw["cohort"]
    cohort_id = _require_str("cohort", head, "id")
    if not _ID_RE.match(cohort_id):
        raise ValueError(f"cohort.id {cohort_id!r} is not a lowercase slug")

    notes_raw = raw.get("notes", [])
    if not isinstance(notes_raw, list) or not all(isinstance(n, str) for n in notes_raw):
        raise ValueError(f"{path}: notes must be a list of strings")

    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or not members_raw:
        raise ValueError(
            f"{path}: members must be a non-empty list; a cohort with no members "
            "is a page with nothing to say"
        )
    seen: set[str] = set()
    members: list[CohortMember] = []
    for i, item in enumerate(members_raw):
        if not isinstance(item, dict):
            raise ValueError(f"members[{i}] is not an object")
        members.append(_parse_member(f"members[{i}]", item, registry_ids, seen))

    return Cohort(
        cohort_id=cohort_id,
        name=_require_str("cohort", head, "name"),
        description=_require_str("cohort", head, "description"),
        notes=tuple(n.strip() for n in notes_raw if n.strip()),
        sources=_parse_sources(raw.get("sources")),
        members=tuple(members),
    )


def load_cohort_dir(directory: Path, registry_ids: frozenset[str]) -> tuple[Cohort, ...]:
    """Every cohort file in a directory, sorted by filename for a deterministic build.

    A directory that does not exist yields no cohorts rather than an error, because most
    invocations (tests, offline demos) have no cohort curation and should not be forced to
    create one. A file that exists and does not parse is an error: a cohort that half-loads
    would publish a membership list with members quietly missing.
    """
    if not directory.is_dir():
        return ()
    return tuple(load_cohort(path, registry_ids) for path in sorted(directory.glob("*.json")))
