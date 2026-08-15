"""Parse a FHIR CapabilityStatement and a SMART discovery document into flat facts.

Parsing is defensive throughout: malformed input produces facts with ``parsed=False`` and a
reason, never an exception. Grading decides what missing facts cost; parsing only observes.

``observed`` separates the two ways there can be no facts, which the grader must not confuse.
A document that was retrieved and could not be parsed is an observation of the endpoint. A
document that was never retrieved is an observation of nothing, and every check downstream of it
has to say so rather than report absence as a property of the server.
"""

from __future__ import annotations

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
    resource_count: int = 0
    resources_with_interactions: int = 0
    supported_profiles: tuple[str, ...] = field(default=())
    declares_oauth_security: bool = False
    parse_error: str | None = None


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
    parsed=False, observed=False,
    parse_error="no CapabilityStatement was retrieved from any vantage on this run")
NO_SMART_RETRIEVED = SmartFacts(
    parsed=False, observed=False,
    parse_error="no SMART discovery document was retrieved from any vantage on this run")


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _rest_resources(doc: dict[str, object]) -> list[dict[str, object]]:
    for rest in _as_list(doc.get("rest")):
        rest_d = _as_dict(rest)
        if rest_d.get("mode") == "server" or "resource" in rest_d:
            return [_as_dict(r) for r in _as_list(rest_d.get("resource"))]
    return []


def _security_declares_oauth(doc: dict[str, object]) -> bool:
    for rest in _as_list(doc.get("rest")):
        security = _as_dict(_as_dict(rest).get("security"))
        for service in _as_list(security.get("service")):
            for coding in _as_list(_as_dict(service).get("coding")):
                code = _as_str(_as_dict(coding).get("code")) or ""
                if code.upper().replace("-", "") in {"SMARTONFHIR", "OAUTH", "OAUTH2"}:
                    return True
    return False


def parse_capability(body: bytes) -> CapabilityFacts:
    try:
        doc_raw = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return CapabilityFacts(parsed=False, parse_error=f"not JSON: {type(exc).__name__}")
    doc = _as_dict(doc_raw)
    if not doc:
        return CapabilityFacts(parsed=False, parse_error="JSON body is not an object")
    if doc.get("resourceType") != "CapabilityStatement":
        return CapabilityFacts(parsed=True, resource_type_ok=False,
                               parse_error=f"resourceType is {doc.get('resourceType')!r}")

    software = _as_dict(doc.get("software"))
    implementation = _as_dict(doc.get("implementation"))
    resources = _rest_resources(doc)
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
        resource_count=len(resources),
        resources_with_interactions=with_interactions,
        supported_profiles=tuple(profiles),
        declares_oauth_security=_security_declares_oauth(doc),
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
