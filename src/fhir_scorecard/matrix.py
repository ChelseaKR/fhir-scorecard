"""Each endpoint's declared resource and interaction matrix, published as data (#102).

The capability transparency dimension grades whether a CapabilityStatement says what its server
runs. The declaration itself - which resources, which interactions on each, which search
parameters, which operations, which profiles - is retrieved on every run and was published
nowhere. "Does this payer support ``Coverage`` search by ``patient``?" is the first question an
app developer asks, and the answer is in a document this project already holds.

This is an observation of what a publisher *said*, dated and hashed. It is never a claim that
any of it works: nothing here was requested or exercised, and the page says so where the
table is.

**Four states, never three and never two.** A document no vantage retrieved, a document that
arrived and could not be read as a CapabilityStatement, a CapabilityStatement that declares
nothing, and one that declares something. The first two render a sentence and no table, and
they are different sentences: "nothing was retrieved on this run" is a fact about the network,
"this document could not be read" is a fact about the document, and ``capability.CapabilityFacts``
already keeps them apart with ``observed``. An empty table would say "declares nothing" about all
three of the others, which is this project's dominant defect in its most literal form.

**One reader.** The flat rows in ``api/capabilities/<id>.json`` are built once, and the page is
grouped from those rows rather than read from the document a second time. Two readers of one
declaration would agree today and disagree the day one of them was tightened, and nothing would
say so.

**Paged by resource, never truncated.** Measured over the 68 CapabilityStatements the live
probes retrieved on 2026-09-10: the flat shape runs from a median of 599 rows to 6,755
(``hapi-fhir-r5``: 3,555 search parameters and 1,761 operations), which at about 170 bytes a row
puts even the median page past the per-page weight budget. So the page carries one row per
*resource*, with its interactions, search parameter names and operation names inline, which fits
51 of the 68 on one page and pages the other 17 by resource: 86 pages in all, the largest
49,692 bytes against the 65,536-byte budget, measured by rendering every one of them. A page
break never falls inside a resource's row, every page says which resources it lists out of how
many, and every row on every page is in the data. A resource whose row would not fit on a page by itself is written with
counts instead of names, says so, and keeps every name in the data - so no third party can
declare its way past the weight gate and stop the site publishing.

**Why ``api/capabilities/`` and not ``api/endpoint/<id>/capabilities.json``.** The issue named
the second path. ``snapshot._collect`` walks ``api/endpoint`` recursively, so a file there would
enter every dated snapshot - 14.2 MB across today's registry, measured - without anybody deciding it
should. What a signed dated dataset carries is the maintainer's call, so the declaration is
published beside the per-endpoint API rather than inside the tree the snapshot copies, and
``tests/test_matrix.py`` fails if a snapshot ever starts carrying it by accident.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass

from fhir_scorecard.capability import CapabilityFacts
from fhir_scorecard.site import Page
from fhir_scorecard.weight import MAX_PAGE_BYTES

#: Version of the ``api/capabilities/<id>.json`` shape. Moves when a field's meaning moves.
SCHEMA_VERSION = 1

#: Site-relative directory the per-endpoint declaration files are written into.
API_DIR = "api/capabilities"

#: The JSON Schema the declaration files are documented by, beside ``dataset.schema.json``.
SCHEMA_FILE = "capabilities.schema.json"

#: Bytes of table rows one page may carry. The rest of a page - the site shell, measured at
#: 5.8 to 5.9 KB on 2026-09-10, the heading and counts, and a page list that grows by about a
#: hundred bytes a page - has to fit in what is left of ``weight.MAX_PAGE_BYTES``, so this is
#: derived from that ceiling rather than typed beside it: a budget moved in one place moves here.
TABLE_BYTE_BUDGET = MAX_PAGE_BYTES * 5 // 8

#: A single resource's row larger than this is written with counts instead of names. Half the
#: table budget, so the rows that still carry their names always leave room for others.
ROW_BYTE_CEILING = TABLE_BYTE_BUDGET // 2

#: Longest resource type label a counts-only row writes out; anything longer is described by its
#: length and given in full in the data. A resource type is a third party's string.
_LABEL_CEILING = 200

NOT_RETRIEVED = "not_retrieved"
UNREADABLE = "unreadable"
DECLARES_NOTHING = "declares_nothing"
DECLARED = "declared"

#: Every state a declaration can be in, with what it means. ``tests/test_matrix.py`` holds the
#: renderer and the schema to this map, so a state cannot ship without a documented meaning.
STATES: dict[str, str] = {
    NOT_RETRIEVED: "no vantage retrieved a CapabilityStatement on this run",
    UNREADABLE: "a document was retrieved and could not be read as a CapabilityStatement",
    DECLARES_NOTHING: "a CapabilityStatement was read and declares nothing this project can list",
    DECLARED: "a CapabilityStatement was read and its declarations are listed",
}

#: Every kind of row the flat data carries. ``resource`` rows exist so a resource declared with
#: no interaction, search parameter or operation still appears: without them the flat shape
#: could not tell "declares Patient with nothing on it" from "does not declare Patient".
ROW_KINDS: tuple[str, ...] = ("resource", "interaction", "search_parameter", "operation", "profile")

#: What every page and every file says about itself, once.
OBSERVATION_NOTE = (
    "An observation of what this endpoint's CapabilityStatement declared when it was retrieved. "
    "Nothing listed here was requested or exercised, so none of it is a claim that the "
    "declared interaction works."
)


@dataclass(frozen=True)
class Row:
    """One declaration, flat. ``resource`` is ``None`` for one made on the whole server."""

    kind: str
    resource: str | None
    name: str
    type: str | None = None
    definition: str | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "resource": self.resource,
            "name": self.name,
            "type": self.type,
            "definition": self.definition,
        }


@dataclass(frozen=True)
class ResourceView:
    """One resource's declarations, grouped from the rows for the page."""

    resource: str | None
    interactions: tuple[str, ...]
    search_parameters: tuple[str, ...]
    operations: tuple[str, ...]
    profiles: int


def state_of(facts: CapabilityFacts) -> str:
    """Which of the four states this declaration is in. See the module docstring."""
    if not facts.observed:
        return NOT_RETRIEVED
    if not facts.parsed or not facts.resource_type_ok:
        return UNREADABLE
    return DECLARED if rows_of(facts) else DECLARES_NOTHING


def _server_rows(facts: CapabilityFacts) -> list[Row]:
    rows = [Row("interaction", None, code) for code in facts.system_interactions]
    rows += [
        Row("operation", None, name, definition=definition)
        for resource, name, definition in facts.operations
        if resource is None
    ]
    return rows


def rows_of(facts: CapabilityFacts) -> tuple[Row, ...]:
    """Every declaration, flat: the whole server first, then each resource type A to Z.

    Interactions are the distinct codes per type, which is how ``capability`` already merges a
    type declared twice; ``interaction_entries`` keeps the count as written. Search parameters,
    operations and profiles are listed as declared. Nothing here reads the document: it reads
    what ``parse_capability`` extracted, so the grader and the matrix cannot be describing two
    different parses of one document.
    """
    if not facts.parsed or not facts.resource_type_ok:
        return ()
    by_type: dict[str, list[Row]] = {}
    for name, codes in facts.resource_interactions:
        by_type.setdefault(name, []).append(Row("resource", name, name))
        by_type[name] += [Row("interaction", name, code) for code in codes]
    for resource, name, kind, definition in facts.search_parameters:
        by_type.setdefault(resource, []).append(
            Row("search_parameter", resource, name, type=kind, definition=definition)
        )
    for owner, name, definition in facts.operations:
        if owner is not None:
            by_type.setdefault(owner, []).append(
                Row("operation", owner, name, definition=definition)
            )
    for resource, canonicals in facts.resource_profiles:
        by_type.setdefault(resource, []).extend(
            Row("profile", resource, canonical) for canonical in canonicals
        )
    rows = _server_rows(facts)
    for name in sorted(by_type):
        rows += by_type[name]
    return tuple(rows)


def views_of(rows: tuple[Row, ...]) -> tuple[ResourceView, ...]:
    """The rows grouped per resource, in the order they arrive - server first, then A to Z."""
    order: list[str | None] = []
    grouped: dict[str | None, dict[str, list[str]]] = {}
    for row in rows:
        if row.resource not in grouped:
            order.append(row.resource)
            grouped[row.resource] = {kind: [] for kind in ROW_KINDS}
        grouped[row.resource][row.kind].append(row.name)
    return tuple(
        ResourceView(
            resource=resource,
            interactions=tuple(grouped[resource]["interaction"]),
            search_parameters=tuple(grouped[resource]["search_parameter"]),
            operations=tuple(grouped[resource]["operation"]),
            profiles=len(grouped[resource]["profile"]),
        )
        for resource in order
    )


def counts_of(facts: CapabilityFacts) -> dict[str, int] | None:
    """The numbers a reader compares, or ``None`` where there is no declaration to count.

    ``None``, never zeros, for a document that was not retrieved or could not be read: a
    consumer handed ``{"resources": 0}`` could not tell "declares none" from "we never saw it".
    """
    if state_of(facts) in {NOT_RETRIEVED, UNREADABLE}:
        return None
    rows = rows_of(facts)
    return {
        "resources": sum(1 for row in rows if row.kind == "resource"),
        "interaction_entries_declared": facts.interaction_entries,
        "interaction_rows": sum(1 for row in rows if row.kind == "interaction"),
        "search_parameters": sum(1 for row in rows if row.kind == "search_parameter"),
        "operations": sum(1 for row in rows if row.kind == "operation"),
        "profiles": sum(1 for row in rows if row.kind == "profile"),
        "rows": len(rows),
    }


def state_sentence(facts: CapabilityFacts) -> str:
    """What this declaration's state means for this endpoint, in one or two sentences."""
    state = state_of(facts)
    if state == NOT_RETRIEVED:
        return (
            "No CapabilityStatement was retrieved from any vantage on this run, so nothing here "
            "describes what this endpoint declares. That is a statement about this run, not "
            "about the endpoint."
        )
    if state == UNREADABLE:
        return (
            "A document was retrieved and could not be read as a CapabilityStatement "
            f"({facts.parse_error or 'no reason recorded'}), so there is no declaration to list. "
            "That is a finding about the document, not a declaration of nothing."
        )
    if state == DECLARES_NOTHING:
        unread = facts.unreadable_declarations
        return (
            "The CapabilityStatement was read and declares no resource, interaction, search "
            "parameter or operation this project can list."
            + (
                f" {unread} {'entry' if unread == 1 else 'entries'} in it could not be read and "
                f"{'is' if unread == 1 else 'are'} counted here rather than listed."
                if unread
                else ""
            )
        )
    return STATES[DECLARED][0].upper() + STATES[DECLARED][1:] + "."


def capabilities_json(
    facts: CapabilityFacts,
    *,
    endpoint_id: str,
    name: str,
    retrieved_on: str,
    generated_at: str,
) -> str:
    """The declaration as data, in the shape ``capabilities.schema.json`` documents.

    Compact rather than indented, deliberately and unlike the other API files. Measured over the
    68 declarations the live probes retrieved on 2026-09-10: 19.8 MB indented against 14.2 MB
    compact, so indenting would add 5.5 MB to every daily publish. The largest single file,
    ``hapi-fhir-r5``'s, is 1.4 MB indented and 1.0 MB compact.
    """
    state = state_of(facts)
    rows = rows_of(facts)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "endpoint_id": endpoint_id,
        "name": name,
        "generated_at": generated_at,
        "state": state,
        "state_detail": state_sentence(facts),
        # Null when nothing was retrieved: there is no retrieval to date.
        "retrieved_on": None if state == NOT_RETRIEVED else retrieved_on,
        "document_sha256": facts.document_sha256,
        "fhir_version": facts.fhir_version if state in {DECLARED, DECLARES_NOTHING} else None,
        "unreadable_declarations": (
            facts.unreadable_declarations if state in {DECLARED, DECLARES_NOTHING} else None
        ),
        "counts": counts_of(facts),
        # Null, not an empty list, where nothing could be read: an empty list is what a
        # CapabilityStatement that declares nothing produces, and the two must not look alike.
        "rows": [row.as_json() for row in rows] if state in {DECLARED, DECLARES_NOTHING} else None,
        "note": OBSERVATION_NOTE,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _nullable(kind: str) -> dict[str, object]:
    return {"type": [kind, "null"]}


def schema_doc(origin: str) -> str:
    """JSON Schema for the declaration files.

    Written with a deliberately small vocabulary - ``type``, ``required``, ``properties``,
    ``enum`` and ``items`` - so that ``tests/test_matrix.py`` can hold every published file to
    it without adding a schema library to a project whose dependency list is empty on purpose;
    that test also fails if the schema grows a keyword its reader does not implement.
    """
    counts = [
        "resources",
        "interaction_entries_declared",
        "interaction_rows",
        "search_parameters",
        "operations",
        "profiles",
        "rows",
    ]
    row_fields = ["kind", "resource", "name", "type", "definition"]
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{origin}/{SCHEMA_FILE}",
            "title": "Declared FHIR capabilities, one file per endpoint",
            "description": OBSERVATION_NOTE,
            "type": "object",
            "required": [
                "schema_version",
                "endpoint_id",
                "name",
                "generated_at",
                "state",
                "state_detail",
                "retrieved_on",
                "document_sha256",
                "fhir_version",
                "unreadable_declarations",
                "counts",
                "rows",
                "note",
            ],
            "properties": {
                "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
                "endpoint_id": {"type": "string"},
                "name": {"type": "string"},
                "generated_at": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": list(STATES),
                    "description": "; ".join(f"{key}: {text}" for key, text in STATES.items()),
                },
                "state_detail": {"type": "string"},
                "retrieved_on": {
                    **_nullable("string"),
                    "description": "Date of the run that retrieved the document; null when "
                    "nothing was retrieved",
                },
                "document_sha256": {
                    **_nullable("string"),
                    "description": "SHA-256 of the retrieved bytes, including a document that "
                    "could not be read; null when nothing was retrieved",
                },
                "fhir_version": _nullable("string"),
                "unreadable_declarations": {
                    **_nullable("integer"),
                    "description": "Entries in the server block that could not be read, counted "
                    "and not listed; null when there was no declaration to read",
                },
                "counts": {
                    **_nullable("object"),
                    "description": "Null, never zeros, when there is no declaration to count",
                    "required": counts,
                    "properties": {key: {"type": "integer"} for key in counts},
                },
                "rows": {
                    **_nullable("array"),
                    "description": "Every declaration, flat. Null when nothing could be read; an "
                    "empty list only for a CapabilityStatement that declares nothing",
                    "items": {
                        "type": "object",
                        "required": row_fields,
                        "properties": {
                            "kind": {"type": "string", "enum": list(ROW_KINDS)},
                            "resource": {
                                **_nullable("string"),
                                "description": "Null for a declaration made on the whole server",
                            },
                            "name": {"type": "string"},
                            "type": _nullable("string"),
                            "definition": _nullable("string"),
                        },
                    },
                },
                "note": {"type": "string"},
            },
        },
        indent=2,
    )


def _label(resource: str | None, *, bounded: bool) -> str:
    if resource is None:
        return "(whole server)"
    if bounded and len(resource) > _LABEL_CEILING:
        return f"(a resource type name of {len(resource)} characters, given in full in the data)"
    return resource


def _listed(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none declared"


def _row_html(view: ResourceView, *, counted: bool) -> str:
    """One resource's table row. ``counted`` writes numbers where the names would not fit."""
    if counted:
        cells = (
            f"{len(view.interactions)} interactions",
            f"{len(view.search_parameters)} search parameters",
            f"{len(view.operations)} operations",
        )
    else:
        cells = (
            _listed(view.interactions),
            _listed(view.search_parameters),
            _listed(view.operations),
        )
    label = _label(view.resource, bounded=counted)
    return (
        f'<tr><th scope="row">{html.escape(label)}</th>'
        + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells)
        + f"<td>{view.profiles}</td></tr>"
    )


@dataclass(frozen=True)
class _Placed:
    view: ResourceView
    html: str
    counted: bool


def paginate(views: tuple[ResourceView, ...]) -> tuple[tuple[_Placed, ...], ...]:
    """Resources split into pages of at most ``TABLE_BYTE_BUDGET`` bytes of rows.

    Greedy and in order, and a page break only ever falls between two resources. Every row is
    at most ``ROW_BYTE_CEILING`` bytes by construction, so every page holds at least one and
    no row is larger than a page.
    """
    pages: list[list[_Placed]] = [[]]
    size = 0
    for view in views:
        row = _row_html(view, counted=False)
        counted = len(row.encode("utf-8")) > ROW_BYTE_CEILING
        if counted:
            row = _row_html(view, counted=True)
        width = len(row.encode("utf-8"))
        if pages[-1] and size + width > TABLE_BYTE_BUDGET:
            pages.append([])
            size = 0
        pages[-1].append(_Placed(view, row, counted))
        size += width
    return tuple(tuple(page) for page in pages if page)


def page_path(endpoint_id: str, number: int = 1) -> str:
    """Site-relative directory of one page of an endpoint's declaration."""
    base = f"endpoint/{endpoint_id}/capabilities"
    return base if number == 1 else f"{base}/{number}"


def _span(placed: tuple[_Placed, ...]) -> str:
    first = _label(placed[0].view.resource, bounded=True)
    last = _label(placed[-1].view.resource, bounded=True)
    return first if first == last else f"{first} to {last}"


def _page_list(endpoint_id: str, pages: tuple[tuple[_Placed, ...], ...], current: int) -> str:
    if len(pages) == 1:
        return ""
    items = []
    for number, placed in enumerate(pages, start=1):
        text = f"Page {number}: {html.escape(_span(placed))}"
        if number == current:
            items.append(f'<li><span aria-current="page">{text}</span></li>')
        else:
            items.append(f'<li><a href="/{page_path(endpoint_id, number)}/">{text}</a></li>')
    return (
        '<nav aria-label="Pages of this declaration"><ul class="usa-list">'
        + "".join(items)
        + "</ul></nav>"
    )


def _counts_sentence(counts: dict[str, int]) -> str:
    repeated = counts["interaction_entries_declared"] - counts["interaction_rows"]
    repeat_note = (
        f" ({counts['interaction_entries_declared']} interaction entries as written; "
        f"{repeated} repeat a code already declared on the same resource and are listed once)"
        if repeated > 0
        else ""
    )
    return (
        f"{counts['resources']} resources, {counts['interaction_rows']} resource and interaction "
        f"pairs{repeat_note}, {counts['search_parameters']} search parameters, "
        f"{counts['operations']} operations and {counts['profiles']} declared profiles"
    )


def _shell_top(endpoint_id: str, name: str, facts: CapabilityFacts, retrieved_on: str) -> str:
    digest = facts.document_sha256
    hashed = f"<code>{html.escape(digest)}</code>" if digest else "no document was retrieved"
    return f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item"><a href="/endpoint/{html.escape(endpoint_id)}/" class="usa-breadcrumb__link"><span>{html.escape(name)}</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page"><span>Declared capabilities</span></li>
</ol></nav>
<p class="eyebrow">Declared, not tested</p>
<h1>{html.escape(name)}: declared capabilities</h1>
<p class="lede">{html.escape(OBSERVATION_NOTE)}</p>
<dl class="facts">
  <dt>Retrieved on the run of</dt><dd>{html.escape(retrieved_on) if digest else "not retrieved on this run"}</dd>
  <dt>Document SHA-256</dt><dd>{hashed}</dd>
</dl>
"""


def _shell_bottom(endpoint_id: str) -> str:
    return f"""
<p>The same declaration as data, every row of it:
<a href="/{API_DIR}/{html.escape(endpoint_id)}.json">{API_DIR}/{html.escape(endpoint_id)}.json</a>,
documented by <a href="/{SCHEMA_FILE}">{SCHEMA_FILE}</a>.</p>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">A declaration is what a publisher says its server supports. This page
does not test any of it and grades none of it; the grade on the endpoint page is computed
separately, from the same document, by the method on <a href="/how-we-grade/">how we grade</a>.</p>
</div></div>
"""


def _declared_page(
    endpoint_id: str,
    name: str,
    facts: CapabilityFacts,
    retrieved_on: str,
    pages: tuple[tuple[_Placed, ...], ...],
    number: int,
) -> Page:
    placed = pages[number - 1]
    counts = counts_of(facts) or {}
    total = sum(len(page) for page in pages)
    where = (
        ""
        if len(pages) == 1
        else (
            f"<p>Page {number} of {len(pages)}, listing {len(placed)} of the {total} rows below "
            f"({html.escape(_span(placed))}). Pages are split between resources and never "
            "inside one, and every row on every page is in the data.</p>"
        )
    )
    counted = [p for p in placed if p.counted]
    counted_note = (
        f"<p>{len(counted)} {'resource on this page is' if len(counted) == 1 else 'resources on this page are'} "
        "written with counts rather than names, because the names would not fit on one page; "
        "every name is in the data.</p>"
        if counted
        else ""
    )
    unread = facts.unreadable_declarations
    unread_note = (
        f"<p>{unread} {'entry' if unread == 1 else 'entries'} in the document could not be read "
        f"(an interaction with no code, a search parameter or operation with no name, or a "
        f"resource with no type) and {'is' if unread == 1 else 'are'} counted here, not listed.</p>"
        if unread
        else ""
    )
    body = (
        _shell_top(endpoint_id, name, facts, retrieved_on)
        + f"<p>It declares {html.escape(_counts_sentence(counts))}.</p>"
        + unread_note
        + where
        + _page_list(endpoint_id, pages, number)
        + "<h2>Declared resources</h2>"
        + '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        + 'aria-label="Declared resources, interactions, search parameters and operations">'
        + '<table class="usa-table usa-table--striped"><caption>Declared resources, '
        + "interactions, search parameters and operations</caption>"
        + '<thead><tr><th scope="col">Resource</th><th scope="col">Interactions</th>'
        + '<th scope="col">Search parameters</th><th scope="col">Operations</th>'
        + '<th scope="col">Profiles declared</th></tr></thead><tbody>'
        + "".join(p.html for p in placed)
        + "</tbody></table></div>"
        + counted_note
        + _shell_bottom(endpoint_id)
    )
    suffix = "" if len(pages) == 1 else f", page {number} of {len(pages)}"
    return Page(
        path=page_path(endpoint_id, number),
        title=f"{name}: declared FHIR capabilities{suffix}",
        description=(
            f"What {name}'s CapabilityStatement declared when retrieved on {retrieved_on}: "
            f"{_counts_sentence(counts)}. Declared, not tested."
        ),
        body=body,
        priority="0.4",
    )


def pages_for(endpoint_id: str, name: str, facts: CapabilityFacts, retrieved_on: str) -> list[Page]:
    """Every page of one endpoint's declaration: one, or one per page of resources."""
    state = state_of(facts)
    if state != DECLARED:
        body = (
            _shell_top(endpoint_id, name, facts, retrieved_on)
            + "<h2>Declared resources</h2>"
            + f"<p>{html.escape(state_sentence(facts))}</p>"
            + _shell_bottom(endpoint_id)
        )
        return [
            Page(
                path=page_path(endpoint_id),
                title=f"{name}: declared FHIR capabilities",
                description=f"{name}: {STATES[state]}.",
                body=body,
                priority="0.3",
            )
        ]
    pages = paginate(views_of(rows_of(facts)))
    return [
        _declared_page(endpoint_id, name, facts, retrieved_on, pages, number)
        for number in range(1, len(pages) + 1)
    ]
