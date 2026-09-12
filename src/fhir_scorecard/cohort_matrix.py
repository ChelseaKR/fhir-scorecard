"""What a cohort's listed endpoints declare, counted within a kind (#102, the aggregate half).

The endpoint pages say what one endpoint declares. This says, for a cohort, how many of its
listed endpoints declare each resource and each interaction on it: the census move the cohort
pages already make about who publishes an endpoint, taken one layer down to what the published
endpoints say they support.

**Within a kind, never across one.** A Patient Access API and a Provider Directory API answer to
different rules, and a count that added them would describe neither. Each kind a cohort lists
gets its own page.

**Three populations, and only one of them is a denominator.** Of a kind's listed endpoints, some
had a readable declaration on this run, some returned a document that could not be read, and
some returned nothing. Every count is "n of D", where D is the endpoints with a readable
declaration, including any that declare nothing at all, because those were read. The other two
populations are stated beside it and never folded in: an endpoint whose document was not
retrieved has not declared an absence of ``Patient``, and counting it as one would publish a
finding nobody observed.

**Counts, not percentages.** A kind in a cohort can have one readable declaration. "1 of 1" says
what was observed; "100%" asserts something about a population of one that the data cannot
support. Whether and where to attach intervals and a small-cell floor is #103's decision, and
this module takes no position on it.

**No interaction code is dropped.** R4's nine type-level interaction codes each get a column, in
the order the specification lists them, and any other code a document declares is listed in a
final column with its count, rather than falling off the edge of a fixed table.
"""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts
from fhir_scorecard.cohort import Cohort
from fhir_scorecard.matrix import (
    DECLARED,
    DECLARES_NOTHING,
    NOT_RETRIEVED,
    TABLE_BYTE_BUDGET,
    UNREADABLE,
    state_of,
)
from fhir_scorecard.site import KIND_LABELS, Page

#: R4 ``TypeRestfulInteraction`` codes, in the order the specification lists them. A code a
#: document declares outside this list is counted in the last column, never discarded.
R4_TYPE_INTERACTIONS: tuple[str, ...] = (
    "read",
    "vread",
    "update",
    "patch",
    "delete",
    "history-instance",
    "history-type",
    "create",
    "search-type",
)


@dataclass(frozen=True)
class KindCensus:
    """One kind's listed endpoints in one cohort, and what the readable ones declare."""

    cohort_id: str
    cohort_name: str
    kind: str
    listed: tuple[str, ...]
    readable: tuple[str, ...]
    unreadable: tuple[str, ...]
    not_retrieved: tuple[str, ...]
    #: (resource type, endpoints declaring it), resource types A to Z.
    resources: tuple[tuple[str, int], ...]
    #: (resource type, interaction code, endpoints declaring that code on that type).
    interactions: tuple[tuple[str, str, int], ...]

    @property
    def path(self) -> str:
        return f"{self.cohort_id}/capabilities/{self.kind}"


def _listed_ids(cohort: Cohort) -> list[str]:
    """Distinct listed endpoint ids, in roster order. Two plans on one surface count once."""
    return list(dict.fromkeys(eid for member in cohort.included for eid in member.endpoint_ids))


def census(
    cohort: Cohort,
    kind: str,
    declared: dict[str, CapabilityFacts],
    kinds: dict[str, str],
) -> KindCensus:
    """Count one kind of one cohort. ``declared`` must hold every listed endpoint of that kind."""
    listed = tuple(eid for eid in _listed_ids(cohort) if kinds.get(eid) == kind)
    states = {eid: state_of(declared[eid]) for eid in listed}
    readable = tuple(eid for eid in listed if states[eid] in {DECLARED, DECLARES_NOTHING})
    resources: Counter[str] = Counter()
    interactions: Counter[tuple[str, str]] = Counter()
    for eid in readable:
        for resource, codes in declared[eid].resource_interactions:
            resources[resource] += 1
            for code in codes:
                interactions[(resource, code)] += 1
    return KindCensus(
        cohort_id=cohort.cohort_id,
        cohort_name=cohort.name,
        kind=kind,
        listed=listed,
        readable=readable,
        unreadable=tuple(eid for eid in listed if states[eid] == UNREADABLE),
        not_retrieved=tuple(eid for eid in listed if states[eid] == NOT_RETRIEVED),
        resources=tuple(sorted(resources.items())),
        interactions=tuple(
            (resource, code, count) for (resource, code), count in sorted(interactions.items())
        ),
    )


def censuses(
    cohort: Cohort, declared: dict[str, CapabilityFacts], kinds: dict[str, str]
) -> tuple[KindCensus, ...]:
    """One census per kind the cohort lists, in a stable order."""
    present = sorted({kinds[eid] for eid in _listed_ids(cohort) if eid in kinds})
    return tuple(census(cohort, kind, declared, kinds) for kind in present)


def _row(item: KindCensus, resource: str, count: int, codes: dict[str, int]) -> str:
    standard = "".join(f"<td>{codes.get(code, 0)}</td>" for code in R4_TYPE_INTERACTIONS)
    other = ", ".join(
        f"{html.escape(code)} ({n})"
        for code, n in sorted(codes.items())
        if code not in R4_TYPE_INTERACTIONS
    )
    return (
        f'<tr><th scope="row">{html.escape(resource)}</th>'
        f"<td>{count} of {len(item.readable)}</td>{standard}<td>{other or 'none'}</td></tr>"
    )


def _rows(item: KindCensus) -> list[str]:
    per_type: dict[str, dict[str, int]] = {}
    for resource, code, count in item.interactions:
        per_type.setdefault(resource, {})[code] = count
    return [
        _row(item, resource, count, per_type.get(resource, {}))
        for resource, count in item.resources
    ]


def _chunks(rows: list[str]) -> list[list[str]]:
    """Rows split into pages of at most ``TABLE_BYTE_BUDGET`` bytes, never inside a row."""
    pages: list[list[str]] = [[]]
    size = 0
    for row in rows:
        width = len(row.encode("utf-8"))
        if pages[-1] and size + width > TABLE_BYTE_BUDGET:
            pages.append([])
            size = 0
        pages[-1].append(row)
        size += width
    return [page for page in pages if page]


def _named(ids: tuple[str, ...]) -> str:
    return ", ".join(
        f'<a href="/endpoint/{html.escape(eid)}/">{html.escape(eid)}</a>' for eid in ids
    )


def _populations(item: KindCensus, label: str) -> str:
    excluded = len(item.unreadable) + len(item.not_retrieved)
    parts = [
        f"<p>{len(item.listed)} listed {html.escape(label)} "
        f"{'endpoint' if len(item.listed) == 1 else 'endpoints'} in this cohort: "
        f"<strong>{len(item.readable)}</strong> with a readable declaration on this run, "
        f"{len(item.unreadable)} whose document could not be read, and "
        f"{len(item.not_retrieved)} not retrieved on this run.",
    ]
    if excluded:
        parts.append(
            f" Every count below is out of the {len(item.readable)} with a readable declaration. "
            f"The other {excluded} are not counted as declaring anything or as declaring nothing: "
            "a document that was not read says nothing about what the endpoint supports."
        )
    parts.append("</p>")
    if item.unreadable:
        parts.append(f"<p>Document could not be read: {_named(item.unreadable)}.</p>")
    if item.not_retrieved:
        parts.append(f"<p>Not retrieved on this run: {_named(item.not_retrieved)}.</p>")
    return "".join(parts)


def pages_for(item: KindCensus) -> list[Page]:
    """This census as one page, or one per page of resource types when it will not fit."""
    label = KIND_LABELS.get(item.kind, item.kind)
    head = (
        '<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">'
        '<li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link">'
        "<span>Home</span></a></li>"
        f'<li class="usa-breadcrumb__list-item"><a href="/{html.escape(item.cohort_id)}/" '
        f'class="usa-breadcrumb__link"><span>{html.escape(item.cohort_name)}</span></a></li>'
        '<li class="usa-breadcrumb__list-item usa-current" aria-current="page">'
        f"<span>What {html.escape(label)} endpoints declare</span></li></ol></nav>"
        '<p class="eyebrow">Declared, not tested</p>'
        f"<h1>{html.escape(item.cohort_name)}: what its {html.escape(label)} endpoints declare</h1>"
        + _populations(item, label)
    )
    rows = _rows(item)
    if not rows:
        body = head + (
            "<h2>Declared resources</h2><p>No listed endpoint of this kind had a readable "
            "declaration that names a resource on this run, so there is nothing to count.</p>"
        )
        return [_page(item, label, body, 1, 1)]
    chunks = _chunks(rows)
    header = (
        '<thead><tr><th scope="col">Resource</th><th scope="col">Endpoints declaring it</th>'
        + "".join(f'<th scope="col">{code}</th>' for code in R4_TYPE_INTERACTIONS)
        + '<th scope="col">Other interaction codes</th></tr></thead>'
    )
    pages = []
    for number, chunk in enumerate(chunks, start=1):
        where = (
            ""
            if len(chunks) == 1
            else f"<p>Page {number} of {len(chunks)}, listing {len(chunk)} of the {len(rows)} resource types.</p>"
        )
        body = (
            head
            + where
            + "<h2>Declared resources</h2>"
            + '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
            + 'aria-label="Declared resources and interactions, counted">'
            + '<table class="usa-table usa-table--striped"><caption>How many readable '
            + "declarations name each resource and each interaction on it</caption>"
            + header
            + "<tbody>"
            + "".join(chunk)
            + "</tbody></table></div>"
        )
        pages.append(_page(item, label, body, number, len(chunks)))
    return pages


def _page(item: KindCensus, label: str, body: str, number: int, total: int) -> Page:
    suffix = "" if total == 1 else f", page {number} of {total}"
    path = item.path if number == 1 else f"{item.path}/{number}"
    return Page(
        path=path,
        title=f"{item.cohort_name}: what its {label} endpoints declare{suffix}",
        description=(
            f"How many of the {len(item.readable)} {label} endpoints in {item.cohort_name} with "
            "a readable declaration on the latest run declare each resource and interaction. "
            "Declared, not tested."
        ),
        body=body
        + '<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">'
        + '<p class="usa-alert__text">A declaration is what a publisher says its server supports. '
        + "Nothing counted here was requested or exercised, and none of it is graded.</p></div></div>",
        priority="0.3",
    )
