"""Endpoint registry: load and validate, refusing anything unverified or non-HTTPS.

Every entry records how and when it was verified. Entries without a verification record are
rejected at load time, which is the code-level enforcement of the project's registry policy:
no endpoint ships on a guess.

Two things a verification record has to be able to say, which a single free-text ``method`` could
only say in prose a machine cannot read:

**On what basis the entry is here.** ``live_capability`` means a CapabilityStatement was retrieved
and the publisher established from it or from the plan's own publication of the address.
``publisher_documented`` means the organization publishes this base URL in its own materials and
the document was *not* retrievable on the verification date. The second kind is listed on purpose:
an endpoint an organization publishes and that does not answer is a finding about the public
record, and a registry that quietly drops it publishes a cohort pruned of its own failures. Such
an entry must carry the ``source`` that printed the URL and what the probe ``observed``, so the
claim is checkable and so nobody can mistake it for a graded endpoint.

**When it was last checked, separately from when it was first curated.** ``reverified`` is its own
dated record rather than an overwrite of ``date``: overwriting loses the curation date, and
leaving ``date`` alone lets a two-week-old check read as today's. Endpoints that were not
re-checked simply have no ``reverified`` block, which is what keeps a stale date from passing as a
fresh one.
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
KINDS = frozenset({"reference", "payer", "payer_provider_directory", "ehr", "provider"})
_KINDS = KINDS
# Declared-intent FHIR releases. Values map to the version prefix a CapabilityStatement must
# carry: STU3 declares 3.x, R4 declares 4.x, R5 declares 5.x.
_EXPECTS = {"stu3": "3.", "r4": "4.", "r5": "5."}
#: The declared-intent values a caller may name, for CLI choices. Public because the
#: single-endpoint check takes one from the command line and must reject anything else, rather
#: than silently falling back to the R4 prefix the way :func:`version_prefix` does.
EXPECTS = tuple(sorted(_EXPECTS))
#: How an entry earned its place. See the module docstring; ``live_capability`` is the default
#: because it is what every entry meant before the second basis existed.
VERIFICATION_BASES = frozenset({"live_capability", "publisher_documented"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    # See the module docstring. An entry whose basis is "publisher_documented" is one this
    # project could not retrieve on the verification date and lists anyway, with the receipts.
    verification_basis: str = "live_capability"
    verification_source: str = ""
    verification_observed: str = ""
    # A later dated re-check, if one happened. Empty means nobody re-checked this entry, and the
    # site says so rather than letting the curation date read as a current one.
    reverified_date: str = ""
    reverified_method: str = ""

    @property
    def verified_as_of(self) -> str:
        """The most recent date anyone checked this entry, whichever record carries it."""
        return self.reverified_date or self.verified_date


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
        raise ValueError(
            f"endpoints[{i}] has no verification record; unverified entries are refused by policy"
        )
    method = verification.get("method")
    date = verification.get("date")
    if not isinstance(method, str) or not method.strip():
        raise ValueError(f"endpoints[{i}].verification.method missing")
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise ValueError(f"endpoints[{i}].verification.date must be YYYY-MM-DD")
    basis, source, observed = _parse_basis(i, verification)
    reverified_date, reverified_method = _parse_reverification(i, verification)

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
        verification_basis=basis,
        verification_source=source,
        verification_observed=observed,
        reverified_date=reverified_date,
        reverified_method=reverified_method,
    )


def _parse_basis(i: int, verification: dict[str, object]) -> tuple[str, str, str]:
    """The basis this entry is listed on, and the receipts a documented-only entry must carry."""
    basis = verification.get("basis", "live_capability")
    if not isinstance(basis, str) or basis not in VERIFICATION_BASES:
        raise ValueError(
            f"endpoints[{i}].verification.basis must be one of {sorted(VERIFICATION_BASES)}"
        )
    source = verification.get("source", "")
    observed = verification.get("observed", "")
    if not isinstance(source, str) or not isinstance(observed, str):
        raise ValueError(f"endpoints[{i}].verification.source and .observed must be strings")
    if basis == "publisher_documented":
        # Listing an endpoint nobody could retrieve is only honest with both halves of the claim:
        # where the organization published the address, and what happened when it was asked for.
        if not source.strip():
            raise ValueError(
                f"endpoints[{i}].verification.source is required when basis is "
                "publisher_documented; the entry rests entirely on where the organization "
                "published this base URL"
            )
        if not observed.strip():
            raise ValueError(
                f"endpoints[{i}].verification.observed is required when basis is "
                "publisher_documented; an endpoint listed as unretrievable must say what the "
                "probe actually saw"
            )
    return basis, source.strip(), observed.strip()


def _parse_reverification(i: int, verification: dict[str, object]) -> tuple[str, str]:
    """A later dated re-check, or a pair of empty strings when nobody made one."""
    raw = verification.get("reverified")
    if raw is None:
        return "", ""
    if not isinstance(raw, dict):
        raise ValueError(f"endpoints[{i}].verification.reverified must be an object")
    date = raw.get("date")
    method = raw.get("method")
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise ValueError(f"endpoints[{i}].verification.reverified.date must be YYYY-MM-DD")
    if not isinstance(method, str) or not method.strip():
        raise ValueError(
            f"endpoints[{i}].verification.reverified.method missing; a re-check with no "
            "method recorded is a date, not a verification"
        )
    return date, method.strip()
