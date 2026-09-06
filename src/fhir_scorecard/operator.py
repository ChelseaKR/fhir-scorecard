"""An operator's own list of endpoints, for checking several of them in one CI run.

:mod:`fhir_scorecard.registry` refuses an entry with no verification record, and that refusal is
policy rather than plumbing: the public registry publishes an attribution, so every listed
address has to carry how and when somebody established who answers at it.

An operator checking their own endpoints publishes nothing. There is no attribution to make, so
there is no verification record to demand, and demanding one would push an operator into
writing a verification claim about themselves that this project would then have to ignore. So
this is a separate loader with a separate type, and the separation runs both ways: a
``verification`` block here is an error, not an ignored field, because an operator who writes
one is describing a record nothing in this file will read, and a silent ignore would let them
believe otherwise.

Everything the public registry validates that is about the *address* rather than the *claim* is
validated identically, by importing the same patterns rather than restating them: the id slug,
the kind vocabulary, the declared-intent release, and https.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fhir_scorecard.gate import GRADE_ORDER
from fhir_scorecard.registry import _EXPECTS, _ID_RE, _KINDS, _require_str


@dataclass(frozen=True)
class OperatorEndpoint:
    """One endpoint an operator has asked this run to check.

    Deliberately not :class:`fhir_scorecard.registry.Endpoint`. That type carries
    ``verified_method`` and ``verified_date``, and a value of ``""`` in those fields would be an
    empty verification record rather than the absence of one -- the same conflation between "no
    measurement" and "a measurement that came out empty" this project splits everywhere else.
    """

    endpoint_id: str
    name: str
    kind: str
    base_url: str
    expects: str = "r4"
    enabled: bool = True
    #: Per-entry threshold, overriding the run-wide ``--min-grade``. Empty means "use the
    #: run-wide one", which may itself be empty, in which case this entry is informational.
    min_grade: str = ""


class OperatorRegistryError(ValueError):
    """The operator registry could not be read as one."""


def load_operator_registry(path: Path) -> list[OperatorEndpoint]:
    """Parse an operator registry, or raise :class:`OperatorRegistryError`.

    Every failure names the index of the entry that caused it, because an operator with twelve
    endpoints in one file needs to know which line to fix.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OperatorRegistryError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise OperatorRegistryError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("endpoints"), list):
        raise OperatorRegistryError("operator registry must be an object with an 'endpoints' list")
    endpoints: list[OperatorEndpoint] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["endpoints"]):
        if not isinstance(item, dict):
            raise OperatorRegistryError(f"endpoints[{i}] is not an object")
        endpoints.append(_parse(i, item, seen))
    if not endpoints:
        # An empty list is a usage error, not a clean run over nothing. A registry mode that
        # exited 0 having graded no endpoint would be a gate that cannot fail.
        raise OperatorRegistryError("operator registry lists no endpoints")
    return endpoints


def _parse(i: int, item: dict[str, object], seen: set[str]) -> OperatorEndpoint:
    endpoint_id = _require_str(i, item, "id")
    if not _ID_RE.match(endpoint_id):
        raise OperatorRegistryError(f"endpoints[{i}].id {endpoint_id!r} is not a lowercase slug")
    if endpoint_id in seen:
        raise OperatorRegistryError(f"duplicate endpoint id {endpoint_id!r}")
    seen.add(endpoint_id)

    kind = _require_str(i, item, "kind")
    if kind not in _KINDS:
        raise OperatorRegistryError(f"endpoints[{i}].kind must be one of {sorted(_KINDS)}")

    base_url = _require_str(i, item, "base_url").rstrip("/")
    if not base_url.startswith("https://"):
        raise OperatorRegistryError(f"endpoints[{i}].base_url must be https")

    if "verification" in item:
        raise OperatorRegistryError(
            f"endpoints[{i}] carries a verification block. An operator registry publishes no "
            "attribution, so nothing here reads one; remove it rather than leave a record this "
            "run will not act on"
        )

    expects = item.get("expects", "r4")
    if not isinstance(expects, str) or expects not in _EXPECTS:
        raise OperatorRegistryError(f"endpoints[{i}].expects must be one of {sorted(_EXPECTS)}")

    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise OperatorRegistryError(f"endpoints[{i}].enabled must be boolean")

    min_grade = item.get("min_grade", "")
    if not isinstance(min_grade, str) or (min_grade and min_grade not in GRADE_ORDER):
        raise OperatorRegistryError(
            f"endpoints[{i}].min_grade must be one of {list(GRADE_ORDER)} or absent"
        )

    return OperatorEndpoint(
        endpoint_id=endpoint_id,
        name=_require_str(i, item, "name"),
        kind=kind,
        base_url=base_url,
        expects=expects,
        enabled=enabled,
        min_grade=min_grade,
    )
