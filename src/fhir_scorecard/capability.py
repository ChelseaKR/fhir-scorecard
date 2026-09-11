"""Parse a FHIR CapabilityStatement and a SMART discovery document into flat facts.

Parsing is defensive throughout: malformed input produces facts with ``parsed=False`` and a
reason, never an exception. Grading decides what missing facts cost; parsing only observes.

``observed`` separates the two ways there can be no facts, which the grader must not confuse.
A document that was retrieved and could not be parsed is an observation of the endpoint. A
document that was never retrieved is an observation of nothing, and every check downstream of it
has to say so rather than report absence as a property of the server.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityFacts:
    parsed: bool
    # False only when no vantage retrieved the document at all. Never False for a document that
    # arrived and turned out to be unparseable, empty, or the wrong resource type.
    observed: bool = True
    resource_type_ok: bool = False
    fhir_version: str | None = None
    software_name: str | None = None
    software_version: str | None = None
    implementation_description: str | None = None
    title: str | None = None
    name: str | None = None
    # ``CapabilityStatement.publisher``: the name of the organization or individual that
    # published the document. Nothing grades it and ``drift._FINGERPRINT_FIELDS`` does not
    # list it, so adding it moves no stored history. ``reverify`` reads it, alongside the
    # other name-bearing elements, to show a person what the document says about who runs
    # the server. It is one string a third party controls, never a verdict.
    publisher: str | None = None
    resource_count: int = 0
    resources_with_interactions: int = 0
    # Canonicals from ``rest.resource.supportedProfile`` alone. Kept as its own field because the
    # drift fingerprint counts it, and widening what it means would report a change no server made.
    supported_profiles: tuple[str, ...] = field(default=())
    # (element, canonical) for every conformance declaration R4 defines a place for. Grading a
    # profile claim from one element and then reporting "no profiles declared" was a conclusion
    # drawn from a place the server was never obliged to use.
    conformance_profiles: tuple[tuple[str, str], ...] = field(default=())
    # (resource type, interaction codes) for each type the document declares, sorted, with a type
    # declared more than once merged into one entry whose interactions are the union. Nothing
    # scores it; `diff.py` reads it to say which resource lost `search-type` rather than only that
    # `resource_count` moved. Deliberately not part of the drift fingerprint: that field list is
    # explicit, and widening it would rewrite what every stored history means.
    resource_interactions: tuple[tuple[str, tuple[str, ...]], ...] = field(default=())
    declares_oauth_security: bool = False
    parse_error: str | None = None
    # Everything below exists for the declared-capability matrix (#102) and nothing grades it.
    # None of it is in ``drift._FINGERPRINT_FIELDS``, which is an explicit list, so adding it
    # moves no stored history and no published score; ``tests/test_matrix.py`` pins both.
    #
    # SHA-256 of the bytes this document was parsed from, for every document that was
    # retrieved - including one that turned out not to be JSON or not to be a
    # CapabilityStatement, because "a document with this digest arrived and could not be read"
    # is a checkable claim and "could not be read" alone is not. ``None`` only when nothing was
    # retrieved at all.
    document_sha256: str | None = None
    # ``rest.resource[].interaction[]`` entries with a readable code, counted as written. The
    # matrix lists each distinct (resource, interaction) once, so it publishes this beside its
    # own count: a document that repeats ``read`` under one resource declares it once and
    # wrote it twice, and a reader comparing counts deserves both numbers.
    interaction_entries: int = 0
    # (resource type, name, search parameter type, definition), in document order.
    search_parameters: tuple[tuple[str, str, str | None, str | None], ...] = field(default=())
    # (resource type, or None for an operation declared on the whole server, name, definition).
    operations: tuple[tuple[str | None, str, str | None], ...] = field(default=())
    # ``rest.interaction[].code``: interactions declared on the server rather than on a type.
    system_interactions: tuple[str, ...] = field(default=())
    # (resource type, profile canonicals), merged per type and sorted, from ``supportedProfile``
    # and ``profile``. ``conformance_profiles`` holds the same canonicals without the type,
    # which is what grading needs and not what a reader of one resource's row needs.
    resource_profiles: tuple[tuple[str, tuple[str, ...]], ...] = field(default=())
    # Declarations in the server block that could not be read - an interaction with no code, a
    # search parameter or operation with no name, a resource entry with no type. Counted, never
    # listed and never dropped silently: a matrix that quietly omitted them would be a truncated
    # declaration published as a complete one.
    unreadable_declarations: int = 0


@dataclass(frozen=True)
class SmartFacts:
    parsed: bool
    observed: bool = True
    has_authorization_endpoint: bool = False
    has_token_endpoint: bool = False
    parse_error: str | None = None


#: Facts for a document no vantage retrieved. Distinct from ``parse_capability(b"")``, which
#: describes a server that answered with nothing; these describe a run that heard nothing.
NO_CAPABILITY_RETRIEVED = CapabilityFacts(
    parsed=False,
    observed=False,
    parse_error="no CapabilityStatement was retrieved from any vantage on this run",
)
NO_SMART_RETRIEVED = SmartFacts(
    parsed=False,
    observed=False,
    parse_error="no SMART discovery document was retrieved from any vantage on this run",
)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _server_rest(doc: dict[str, object]) -> dict[str, object]:
    """The ``rest`` block this project reads: the first one in server mode or carrying resources.

    One function, so the grader and the declared-capability matrix read the same block and
    cannot disagree about which declaration a server made.
    """
    for rest in _as_list(doc.get("rest")):
        rest_d = _as_dict(rest)
        if rest_d.get("mode") == "server" or "resource" in rest_d:
            return rest_d
    return {}


def _rest_resources(doc: dict[str, object]) -> list[dict[str, object]]:
    return [_as_dict(r) for r in _as_list(_server_rest(doc).get("resource"))]


def _security_declares_oauth(doc: dict[str, object]) -> bool:
    for rest in _as_list(doc.get("rest")):
        security = _as_dict(_as_dict(rest).get("security"))
        for service in _as_list(security.get("service")):
            for coding in _as_list(_as_dict(service).get("coding")):
                code = _as_str(_as_dict(coding).get("code")) or ""
                if code.upper().replace("-", "") in {"SMARTONFHIR", "OAUTH", "OAUTH2"}:
                    return True
    return False


def _canonical(value: object) -> str | None:
    """A canonical URL, whether written as one or as a Reference-shaped object.

    R4 types ``rest.resource.profile`` as a canonical string, and servers migrated from STU3
    sometimes still send ``{"reference": "..."}``. Reading both costs nothing and avoids
    concluding "nothing declared" from a shape difference.
    """
    if isinstance(value, str):
        return _as_str(value)
    if isinstance(value, dict):
        return _as_str(value.get("reference"))
    return None


def _conformance_profiles(
    doc: dict[str, object], resources: list[dict[str, object]]
) -> list[tuple[str, str]]:
    """Every profile canonical the document declares, tagged with the element it came from.

    R4 gives a server several honest places to declare conformance. Reading one of them and
    publishing "no recognized interoperability profiles declared" states a conclusion about all
    of them.
    """
    found: list[tuple[str, str]] = []
    for element, raw in (
        ("CapabilityStatement.instantiates", doc.get("instantiates")),
        ("CapabilityStatement.imports", doc.get("imports")),
        ("meta.profile", _as_dict(doc.get("meta")).get("profile")),
    ):
        for value in _as_list(raw):
            canonical = _canonical(value)
            if canonical:
                found.append((element, canonical))
    for resource in resources:
        for value in _as_list(resource.get("supportedProfile")):
            canonical = _canonical(value)
            if canonical:
                found.append(("rest.resource.supportedProfile", canonical))
        single = _canonical(resource.get("profile"))
        if single:
            found.append(("rest.resource.profile", single))
    return found


def _resource_interactions(
    resources: list[dict[str, object]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Interaction codes per declared resource type, sorted, duplicates merged.

    A type declared more than once in one ``rest`` block becomes one entry whose interactions are
    the union, so a document that splits a type across two entries is not read as two resources
    with half the interactions each. ``resource_count`` deliberately still counts entries rather
    than types: it is a fingerprint field, and changing what it counts would rewrite the meaning
    of every drift observation already on record.
    """
    interactions: dict[str, set[str]] = {}
    for resource in resources:
        type_name = _as_str(resource.get("type"))
        if type_name is None:
            continue
        codes = interactions.setdefault(type_name, set())
        for entry in _as_list(resource.get("interaction")):
            code = _as_str(_as_dict(entry).get("code"))
            if code:
                codes.add(code)
    return tuple((name, tuple(sorted(codes))) for name, codes in sorted(interactions.items()))


@dataclass(frozen=True)
class _Declarations:
    """What the matrix reads from the server block, gathered in one pass."""

    interaction_entries: int
    search_parameters: tuple[tuple[str, str, str | None, str | None], ...]
    operations: tuple[tuple[str | None, str, str | None], ...]
    system_interactions: tuple[str, ...]
    resource_profiles: tuple[tuple[str, tuple[str, ...]], ...]
    unreadable: int


def _named(items: object, key: str) -> tuple[list[dict[str, object]], int]:
    """The entries of ``items`` carrying a readable ``key``, and how many did not."""
    readable: list[dict[str, object]] = []
    unreadable = 0
    for item in _as_list(items):
        entry = _as_dict(item)
        if _as_str(entry.get(key)) is None:
            unreadable += 1
        else:
            readable.append(entry)
    return readable, unreadable


def _declarations(rest: dict[str, object], resources: list[dict[str, object]]) -> _Declarations:
    """Search parameters, operations, profiles and server-level entries, as declared."""
    search: list[tuple[str, str, str | None, str | None]] = []
    operations: list[tuple[str | None, str, str | None]] = []
    profiles: dict[str, set[str]] = {}
    entries = 0
    unreadable = 0
    for resource in resources:
        type_name = _as_str(resource.get("type"))
        if type_name is None:
            unreadable += 1
            continue
        coded, missing = _named(resource.get("interaction"), "code")
        entries += len(coded)
        unreadable += missing
        params, missing = _named(resource.get("searchParam"), "name")
        unreadable += missing
        search.extend(
            (type_name, str(p["name"]), _as_str(p.get("type")), _as_str(p.get("definition")))
            for p in params
        )
        ops, missing = _named(resource.get("operation"), "name")
        unreadable += missing
        operations.extend((type_name, str(o["name"]), _as_str(o.get("definition"))) for o in ops)
        declared = profiles.setdefault(type_name, set())
        declared.update(
            c for c in (_canonical(v) for v in _as_list(resource.get("supportedProfile"))) if c
        )
        single = _canonical(resource.get("profile"))
        if single:
            declared.add(single)
    server_ops, missing = _named(rest.get("operation"), "name")
    unreadable += missing
    operations.extend((None, str(o["name"]), _as_str(o.get("definition"))) for o in server_ops)
    server_codes, missing = _named(rest.get("interaction"), "code")
    unreadable += missing
    return _Declarations(
        interaction_entries=entries,
        search_parameters=tuple(search),
        operations=tuple(operations),
        system_interactions=tuple(sorted({str(i["code"]) for i in server_codes})),
        resource_profiles=tuple(
            (name, tuple(sorted(canonicals))) for name, canonicals in sorted(profiles.items())
        ),
        unreadable=unreadable,
    )


def parse_capability(body: bytes) -> CapabilityFacts:
    digest = hashlib.sha256(body).hexdigest()
    try:
        doc_raw = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return CapabilityFacts(
            parsed=False, parse_error=f"not JSON: {type(exc).__name__}", document_sha256=digest
        )
    doc = _as_dict(doc_raw)
    if not doc:
        return CapabilityFacts(
            parsed=False, parse_error="JSON body is not an object", document_sha256=digest
        )
    if doc.get("resourceType") != "CapabilityStatement":
        return CapabilityFacts(
            parsed=True,
            resource_type_ok=False,
            parse_error=f"resourceType is {doc.get('resourceType')!r}",
            document_sha256=digest,
        )

    software = _as_dict(doc.get("software"))
    implementation = _as_dict(doc.get("implementation"))
    resources = _rest_resources(doc)
    declarations = _declarations(_server_rest(doc), resources)
    with_interactions = sum(1 for r in resources if _as_list(r.get("interaction")))
    profiles: list[str] = []
    for r in resources:
        for p in _as_list(r.get("supportedProfile")):
            if isinstance(p, str):
                profiles.append(p)

    return CapabilityFacts(
        parsed=True,
        resource_type_ok=True,
        fhir_version=_as_str(doc.get("fhirVersion")),
        software_name=_as_str(software.get("name")),
        software_version=_as_str(software.get("version")),
        implementation_description=_as_str(implementation.get("description")),
        title=_as_str(doc.get("title")),
        name=_as_str(doc.get("name")),
        publisher=_as_str(doc.get("publisher")),
        resource_count=len(resources),
        resources_with_interactions=with_interactions,
        supported_profiles=tuple(profiles),
        conformance_profiles=tuple(_conformance_profiles(doc, resources)),
        resource_interactions=_resource_interactions(resources),
        declares_oauth_security=_security_declares_oauth(doc),
        document_sha256=digest,
        interaction_entries=declarations.interaction_entries,
        search_parameters=declarations.search_parameters,
        operations=declarations.operations,
        system_interactions=declarations.system_interactions,
        resource_profiles=declarations.resource_profiles,
        unreadable_declarations=declarations.unreadable,
    )


def parse_smart(body: bytes) -> SmartFacts:
    try:
        doc_raw = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return SmartFacts(parsed=False, parse_error=f"not JSON: {type(exc).__name__}")
    doc = _as_dict(doc_raw)
    if not doc:
        return SmartFacts(parsed=False, parse_error="JSON body is not an object")
    return SmartFacts(
        parsed=True,
        has_authorization_endpoint=_as_str(doc.get("authorization_endpoint")) is not None,
        has_token_endpoint=_as_str(doc.get("token_endpoint")) is not None,
    )
