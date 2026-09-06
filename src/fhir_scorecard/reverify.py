"""Re-check registry entries and propose a dated ``reverified`` block for a person to accept.

Registry honesty is this project's asset, and it decays quietly. A base URL rotates brands and
then starts answering 401; a plan is acquired; a vendor moves a tenant. Thirteen curation waves
have added entries whose re-check dates drift the moment they are written, and re-checking has
been manual throughout.

``fhir-scorecard reverify`` retrieves each selected entry's CapabilityStatement under the
existing probe contract and writes a **proposal file**. It never edits ``data/registry.json``
in place, and it never decides anything.

Three rules, and each one is load-bearing.

**A stale date must never read as a fresh one.** An entry whose document could not be retrieved
gets a row saying exactly that and **no proposed block at all**. ``--apply`` cannot refresh a
date from a row that observed nothing, because there is nothing in the row to apply. This is the
whole reason ``reverified`` is a separate dated record rather than an overwrite of ``date``
(see ``registry``'s module docstring), and a re-check verb that quietly refreshed dates for
endpoints that did not answer would undo it.

**Attribution is a human judgement, and this verb does not make it.** ``CONTRIBUTING.md`` is
explicit about why: a vendor-hosted multi-tenant platform usually describes the platform rather
than the tenant, sometimes names nobody, and one such platform returned three different brand
names across three consecutive fetches of a fixed URL. A machine cannot read that and conclude
anything. So the outcomes here are ``match``, ``unconfirmed`` and ``not_observed`` --
deliberately not "mismatch". A document that does not repeat the plan's name is the ordinary
case for a vendor platform, not evidence against the entry, and calling it a mismatch would put
a false accusation in a file a person is being asked to skim.

**Nothing a third-party server publishes steers anything.** Every observed string is bounded and
stripped before it reaches the proposal file, for the reason ``reprobe._safe`` gives: these
strings end up inside Markdown and inside a file a person reads quickly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fhir_scorecard.capability import CapabilityFacts, parse_capability
from fhir_scorecard.fetch import fetch_json
from fhir_scorecard.registry import Endpoint

#: The document was retrieved and something in it carries the name the entry claims.
MATCH = "match"
#: The document was retrieved and nothing in it repeats the entry's name. Not an accusation:
#: for a vendor-hosted multi-tenant platform this is the expected shape. A person decides.
UNCONFIRMED = "unconfirmed"
#: No CapabilityStatement came back, or what came back was not one. No block is proposed.
NOT_OBSERVED = "not_observed"

OUTCOMES = (MATCH, UNCONFIRMED, NOT_OBSERVED)

#: Longest remote-supplied string this proposal will quote. Same bound and the same reason as
#: ``reprobe._MAX_QUOTED``: a free-text field on a server this project deliberately does not
#: trust ends up in text a person reads.
_MAX_QUOTED = 120

_PROPOSAL_SCHEMA = "fhir-scorecard/reverify-proposal/v1"

# Words carried by so many organization names that matching on one alone would report a match
# between any two health plans in the country.
_STOPWORDS = frozenset(
    {
        "health",
        "healthcare",
        "plan",
        "plans",
        "care",
        "medical",
        "insurance",
        "system",
        "systems",
        "group",
        "inc",
        "llc",
        "corp",
        "company",
        "the",
        "of",
        "and",
        "fhir",
        "api",
        "server",
        "r4",
    }
)

_WORD = re.compile(r"[a-z0-9]+")


def _safe(value: str | None) -> str:
    """A remote server's string, bounded and stripped of anything that could restructure text."""
    if value is None:
        return ""
    cleaned = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in value)
    cleaned = cleaned.replace("`", "'").strip()
    if len(cleaned) > _MAX_QUOTED:
        cleaned = cleaned[:_MAX_QUOTED] + "..."
    return cleaned


def _significant_words(value: str) -> set[str]:
    return {
        word for word in _WORD.findall(value.lower()) if word not in _STOPWORDS and len(word) > 2
    }


@dataclass(frozen=True)
class Observed:
    """The name-bearing elements of a CapabilityStatement, bounded.

    Five elements rather than one because ``CONTRIBUTING.md`` names three of them as places a
    publisher may be established from, and because a document that states its publisher in only
    one of them is the norm rather than the exception.
    """

    publisher: str = ""
    title: str = ""
    name: str = ""
    software_name: str = ""
    implementation_description: str = ""

    def strings(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.publisher,
                self.title,
                self.name,
                self.software_name,
                self.implementation_description,
            )
            if value
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "publisher": self.publisher,
            "title": self.title,
            "name": self.name,
            "software_name": self.software_name,
            "implementation_description": self.implementation_description,
        }


def observed_from(facts: CapabilityFacts) -> Observed:
    return Observed(
        publisher=_safe(facts.publisher),
        title=_safe(facts.title),
        name=_safe(facts.name),
        software_name=_safe(facts.software_name),
        implementation_description=_safe(facts.implementation_description),
    )


def names_the_entry(entry_name: str, observed: Observed) -> str:
    """Which observed element carries the entry's name, or ``""`` when none does.

    Deliberately conservative and deliberately one-directional. It asks whether every
    significant word of the registry name appears in one observed string. It does not stem, it
    does not score similarity, and it does not attribute on the base URL -- ``CONTRIBUTING.md``
    forbids that outright ("Never attribute on a URL path segment"), and a fuzzy score here
    would be exactly the plausible-and-wrong resolution the project refuses.

    It is still only a *name* check, and it can say ``match`` where a person would not. A
    registry name whose only significant word is shared with another organization -- "Community
    Care Plan" against a document published by "Community Care Alliance" -- satisfies it. That
    is why ``match`` proposes a block for a person to accept rather than writing one, and why
    the proposed ``method`` quotes the string the match was made on: the reader sees what the
    document actually said and can reject the row.
    """
    wanted = _significant_words(entry_name)
    if not wanted:
        return ""
    for label, value in (
        ("publisher", observed.publisher),
        ("title", observed.title),
        ("name", observed.name),
        ("software_name", observed.software_name),
        ("implementation_description", observed.implementation_description),
    ):
        if value and wanted <= _significant_words(value):
            return label
    return ""


@dataclass(frozen=True)
class ReverifyRow:
    endpoint_id: str
    name: str
    base_url: str
    outcome: str
    detail: str
    observed: Observed = field(default_factory=Observed)
    proposed: dict[str, str] | None = None
    """The ``reverified`` block a person may accept, or ``None``.

    ``None`` for every row that observed no document. A block carrying today's date for an
    endpoint that did not answer is the one thing this verb must never produce, so it is not
    produced: there is nothing for ``--apply`` to write.
    """
    accepted: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "endpoint_id": self.endpoint_id,
            "name": self.name,
            "base_url": self.base_url,
            "outcome": self.outcome,
            "detail": self.detail,
            "observed": self.observed.to_payload(),
            "proposed": self.proposed,
            # A person sets this to true on the rows they approve. `--apply` reads it and
            # nothing else; it never infers acceptance from the outcome.
            "accepted": self.accepted,
        }


def _proposed_block(outcome: str, where: str, observed: Observed, today: str) -> dict[str, str]:
    if outcome == MATCH:
        quoted = getattr(observed, where)
        method = (
            f"reverify: live CapabilityStatement fetch; {where} {quoted!r} carries the "
            f"registry name"
        )
    else:
        stated = "; ".join(observed.strings()) or "no name-bearing element"
        method = (
            f"reverify: live CapabilityStatement fetch; the document does not repeat the "
            f"registry name. Observed: {stated}. A person confirmed the attribution."
        )
    return {"date": today, "method": method}


def reverify_one(entry: Endpoint, *, today: str, timeout: int = 20) -> ReverifyRow:
    """Re-check one entry. Retrieves one document and decides nothing."""
    result = fetch_json(f"{entry.base_url.rstrip('/')}/metadata", timeout=timeout)
    if not result.ok:
        return ReverifyRow(
            endpoint_id=entry.endpoint_id,
            name=entry.name,
            base_url=entry.base_url,
            outcome=NOT_OBSERVED,
            detail=_safe(result.error) or f"HTTP {result.status}",
        )
    facts = parse_capability(result.body)
    if not facts.parsed or not facts.resource_type_ok:
        return ReverifyRow(
            endpoint_id=entry.endpoint_id,
            name=entry.name,
            base_url=entry.base_url,
            outcome=NOT_OBSERVED,
            detail=f"answered, but not a CapabilityStatement: {_safe(facts.parse_error)}",
        )
    observed = observed_from(facts)
    where = names_the_entry(entry.name, observed)
    outcome = MATCH if where else UNCONFIRMED
    detail = (
        f"the {where} carries the registry name"
        if where
        else "no element repeats the registry name; a person must confirm the attribution"
    )
    return ReverifyRow(
        endpoint_id=entry.endpoint_id,
        name=entry.name,
        base_url=entry.base_url,
        outcome=outcome,
        detail=detail,
        observed=observed,
        proposed=_proposed_block(outcome, where, observed, today),
    )


def _older_than_days(spec: str) -> int:
    match = re.fullmatch(r"(\d+)d?", spec.strip())
    if not match:
        raise ValueError(f"--older-than must be a number of days, e.g. 90d; got {spec!r}")
    return int(match.group(1))


def select(
    entries: list[Endpoint],
    *,
    older_than: str | None = None,
    endpoint_id: str | None = None,
    today: str | None = None,
) -> list[Endpoint]:
    """Which entries this run re-checks.

    ``--older-than`` selects on ``verified_as_of``, which is the reverification date when there
    is one and the curation date otherwise. An entry nobody has ever re-checked is therefore
    selected by its curation date rather than skipped for having no reverification record: the
    entries most in need of a re-check are exactly the ones that have never had one.
    """
    chosen = [entry for entry in entries if entry.enabled]
    if endpoint_id is not None:
        chosen = [entry for entry in chosen if entry.endpoint_id == endpoint_id]
        if not chosen:
            raise ValueError(f"no enabled registry entry with id {endpoint_id!r}")
    if older_than is not None:
        days = _older_than_days(older_than)
        cutoff = date.fromisoformat(today or date.today().isoformat()).toordinal() - days
        chosen = [
            entry
            for entry in chosen
            if date.fromisoformat(entry.verified_as_of).toordinal() <= cutoff
        ]
    return chosen


def build_proposal(
    rows: list[ReverifyRow], *, today: str, registry_path: Path
) -> dict[str, object]:
    counts = {outcome: sum(1 for row in rows if row.outcome == outcome) for outcome in OUTCOMES}
    return {
        "schema": _PROPOSAL_SCHEMA,
        "generated": today,
        "registry": str(registry_path),
        "counts": counts,
        "how_to_use": (
            'Set "accepted": true on each row whose attribution you have confirmed, then run '
            '`fhir-scorecard reverify --apply <this file>`. Rows with "proposed": null '
            "observed no document and can never be applied; their existing reverified date is "
            "left exactly as it was. An 'unconfirmed' outcome is not an accusation: a "
            "vendor-hosted platform usually describes the platform rather than the tenant."
        ),
        "rows": [row.to_payload() for row in rows],
    }


def format_report(rows: list[ReverifyRow]) -> str:
    counts = {outcome: sum(1 for row in rows if row.outcome == outcome) for outcome in OUTCOMES}
    lines = [
        f"re-checked {len(rows)} registry entries: "
        f"{counts[MATCH]} name-confirmed, {counts[UNCONFIRMED]} unconfirmed, "
        f"{counts[NOT_OBSERVED]} not observed"
    ]
    for row in rows:
        lines.append(f"  [{row.outcome}] {row.endpoint_id}: {row.detail}")
    lines.append("")
    lines.append(
        "Nothing here has been written to the registry. No row refreshes a date on its own, "
        "and a row that observed no document proposes nothing at all."
    )
    return "\n".join(lines)


class ApplyError(ValueError):
    """A proposal file could not be applied as written."""


def load_proposal(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != _PROPOSAL_SCHEMA:
        raise ApplyError(f"{path} is not a {_PROPOSAL_SCHEMA} proposal")
    if not isinstance(raw.get("rows"), list):
        raise ApplyError(f"{path}: 'rows' must be a list")
    return raw


def accepted_blocks(proposal: dict[str, object]) -> dict[str, dict[str, str]]:
    """The ``reverified`` block per endpoint id, for accepted rows only.

    A row that observed no document is refused rather than skipped. Marking one accepted is a
    person asking for a date the run never earned, and answering that request quietly -- by
    doing nothing and reporting success -- is how a stale date becomes a fresh-looking one.
    """
    blocks: dict[str, dict[str, str]] = {}
    rows = proposal.get("rows")
    if not isinstance(rows, list):
        raise ApplyError("'rows' must be a list")
    for row in rows:
        if not isinstance(row, dict) or row.get("accepted") is not True:
            continue
        endpoint_id = str(row.get("endpoint_id") or "")
        proposed = row.get("proposed")
        if not isinstance(proposed, dict) or not proposed.get("date"):
            raise ApplyError(
                f"row {endpoint_id!r} is marked accepted but observed no document, so there "
                f"is no dated block to apply. Re-run reverify for it rather than accepting a "
                f"row whose outcome is {row.get('outcome')!r}."
            )
        blocks[endpoint_id] = {
            "date": str(proposed["date"]),
            "method": str(proposed.get("method", "")),
        }
    return blocks


def apply_to_registry(registry_path: Path, blocks: dict[str, dict[str, str]]) -> int:
    """Merge accepted blocks into the registry file. Returns how many entries moved.

    Writes nothing when there is nothing accepted, so the file is byte-for-byte untouched by a
    run that approved no row. That early return is not an optimisation: this writer normalises
    the file to two-space JSON, so re-serialising a registry formatted any other way would
    rewrite every line of it to record no decision at all.
    """
    if not blocks:
        return 0
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    endpoints = raw.get("endpoints")
    if not isinstance(endpoints, list):
        raise ApplyError(f"{registry_path}: 'endpoints' must be a list")
    by_id = {str(entry.get("id")): entry for entry in endpoints if isinstance(entry, dict)}
    unknown = sorted(set(blocks) - set(by_id))
    if unknown:
        raise ApplyError(f"accepted rows name endpoints not in the registry: {unknown}")
    for endpoint_id, block in blocks.items():
        entry = by_id[endpoint_id]
        verification = entry.get("verification")
        if not isinstance(verification, dict):
            raise ApplyError(f"{endpoint_id} has no verification record to re-check")
        verification["reverified"] = block
    registry_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(blocks)
