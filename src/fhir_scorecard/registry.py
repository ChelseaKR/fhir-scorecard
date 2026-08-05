"""Endpoint registry: load and validate, refusing anything unverified or non-HTTPS.

Every entry records how and when it was verified. Entries without a verification record are
rejected at load time, which is the code-level enforcement of the project's registry policy:
no endpoint ships on a guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
# Kinds are not cosmetic: grades are only comparable within a kind. A payer Patient Access API
# and an EHR vendor's sandbox answer to different implementation guides and different
# expectations, so the report groups by kind and never ranks across them.
_KINDS = {"reference", "payer", "payer_provider_directory", "ehr", "provider"}
# Declared-intent FHIR releases. Values map to the version prefix a CapabilityStatement must
# carry: STU3 declares 3.x, R4 declares 4.x, R5 declares 5.x.
_EXPECTS = {"stu3": "3.", "r4": "4.", "r5": "5."}


@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    name: str
    kind: str
    base_url: str
    verified_method: str
    verified_date: str
    enabled: bool = True
    # Which FHIR release this endpoint intends to serve. Grading checks the server against its
    # own declared intent, so a deliberately-R5 server is not marked down for not being R4.
    # "r4" is the default because the CMS interoperability rules require R4 of the payer APIs
    # that are this project's subject.
    expects: str = "r4"


def version_prefix(expects: str) -> str:
    """The fhirVersion prefix an endpoint declaring ``expects`` should carry."""
    return _EXPECTS.get(expects, "4.")


def load_registry(path: Path) -> list[Endpoint]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("endpoints"), list):
        raise ValueError("registry must be an object with an 'endpoints' list")
    endpoints: list[Endpoint] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["endpoints"]):
        if not isinstance(item, dict):
            raise ValueError(f"endpoints[{i}] is not an object")
        endpoints.append(_parse_entry(i, item, seen))
    return endpoints


def _require_str(i: int, item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"endpoints[{i}].{key} missing or empty")
    return value.strip()


def _parse_entry(i: int, item: dict[str, object], seen: set[str]) -> Endpoint:
    endpoint_id = _require_str(i, item, "id")
    if not _ID_RE.match(endpoint_id):
        raise ValueError(f"endpoints[{i}].id {endpoint_id!r} is not a lowercase slug")
    if endpoint_id in seen:
        raise ValueError(f"duplicate endpoint id {endpoint_id!r}")
    seen.add(endpoint_id)

    kind = _require_str(i, item, "kind")
    if kind not in _KINDS:
        raise ValueError(f"endpoints[{i}].kind must be one of {sorted(_KINDS)}")

    base_url = _require_str(i, item, "base_url").rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError(f"endpoints[{i}].base_url must be https")

    verification = item.get("verification")
    if not isinstance(verification, dict):
        raise ValueError(f"endpoints[{i}] has no verification record; unverified entries "
                         "are refused by policy")
    method = verification.get("method")
    date = verification.get("date")
    if not isinstance(method, str) or not method.strip():
        raise ValueError(f"endpoints[{i}].verification.method missing")
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError(f"endpoints[{i}].verification.date must be YYYY-MM-DD")

    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"endpoints[{i}].enabled must be boolean")

    expects = item.get("expects", "r4")
    if not isinstance(expects, str) or expects not in _EXPECTS:
        raise ValueError(f"endpoints[{i}].expects must be one of {sorted(_EXPECTS)}")

    return Endpoint(
        endpoint_id=endpoint_id,
        name=_require_str(i, item, "name"),
        kind=kind,
        base_url=base_url,
        verified_method=method.strip(),
        verified_date=date,
        enabled=enabled,
        expects=expects,
    )
