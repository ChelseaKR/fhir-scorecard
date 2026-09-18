"""The concentration measurement, and the three ways it could quietly lie.

Ordered by what they protect:

1. **An absence must not rank as a party.** The one bucket that names no party is the endpoints
   whose documents declared no ``software`` element, and the first run of the module reported it
   as the largest intermediary in the registry and crossed a threshold with it.
2. **"Nobody has read this" must not become "this declares nothing".** Those are the two states
   the platform axis is most likely to merge, and merging them turns a fact about this project's
   probing into a fact about what payers publish.
3. **Both population numbers travel together.** A footprint count with no denominator is the
   shape of every stale number this repository has had to repair.

The last group runs the module over the committed registry, because a measurement that only ever
sees synthetic endpoints is not evidence about the data it will be quoted on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhir_scorecard.concentration import (
    DECLARED_NO_SOFTWARE,
    HOST,
    MULTI_LABEL_SUFFIXES,
    PLATFORM,
    by_host,
    by_platform,
    crosses,
    fingerprints_from_history,
    host_of,
    registrable_domain,
    render,
)
from fhir_scorecard.registry import Endpoint, load_registry

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "registry.json"


def _endpoint(endpoint_id: str, name: str, base_url: str) -> Endpoint:
    return Endpoint(
        endpoint_id=endpoint_id,
        name=name,
        kind="payer",
        base_url=base_url,
        verified_method="fixture",
        verified_date="2026-09-13",
    )


# --------------------------------------------------------------------------------------------
# 1. An absence must not rank as a party.
# --------------------------------------------------------------------------------------------


def test_the_no_software_bucket_is_never_the_largest_intermediary() -> None:
    """The regression this module was corrected for, pinned at the value that produced it.

    Four organizations declaring no software element outrank a two-endpoint platform on raw
    count. They have nothing in common but the absence, so ``largest_shared`` must skip them and
    name the platform instead.
    """
    endpoints = [
        _endpoint(f"quiet-{i}", f"Quiet Payer {i}", f"https://quiet{i}.example.com/fhir")
        for i in range(4)
    ] + [
        _endpoint("loud-a", "Loud Payer A", "https://a.example.net/fhir"),
        _endpoint("loud-b", "Loud Payer B", "https://b.example.net/fhir"),
    ]
    fingerprints: dict[str, dict[str, object]] = {
        f"quiet-{i}": {"software_name": None} for i in range(4)
    }
    fingerprints["loud-a"] = {"software_name": "Some Platform"}
    fingerprints["loud-b"] = {"software_name": "Some Platform"}

    result = by_platform(endpoints, fingerprints)

    absence = next(f for f in result.footprints if f.key == DECLARED_NO_SOFTWARE)
    assert absence.endpoints == 4
    assert absence.serves_several_organizations is True
    assert absence.names_a_party is False
    assert absence.could_be_an_intermediary is False

    largest = result.largest_shared
    assert largest is not None
    assert largest.key == "Some Platform"
    assert largest.endpoints == 2


def test_a_threshold_is_not_crossed_by_the_absence_bucket() -> None:
    """The same rule, through the entry point a caller would actually gate on."""
    endpoints = [
        _endpoint(f"quiet-{i}", f"Quiet Payer {i}", f"https://quiet{i}.example.com/fhir")
        for i in range(5)
    ]
    fingerprints: dict[str, dict[str, object]] = {
        f"quiet-{i}": {"software_name": ""} for i in range(5)
    }
    assert crosses(by_platform(endpoints, fingerprints), 3) is None


def test_a_threshold_below_one_is_refused() -> None:
    """A gate that cannot fail is worse than no gate; 0 would make every footprint a crossing."""
    result = by_host([_endpoint("a", "A", "https://a.example.com/fhir")])
    with pytest.raises(ValueError, match="at least 1"):
        crosses(result, 0)


def test_a_threshold_is_crossed_only_at_or_above_it() -> None:
    endpoints = [
        _endpoint("a", "Payer A", "https://shared.example.com/a"),
        _endpoint("b", "Payer B", "https://shared.example.com/b"),
        _endpoint("c", "Payer C", "https://shared.example.com/c"),
    ]
    result = by_host(endpoints)
    assert crosses(result, 3) is not None
    assert crosses(result, 4) is None


# --------------------------------------------------------------------------------------------
# 2. "Nobody has read this" must not become "this declares nothing".
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "fingerprints"),
    [
        ("absent key", {}),
        ("explicit None", {"unread": None}),
    ],
)
def test_an_endpoint_nobody_has_read_is_unmeasured_and_joins_no_bucket(
    label: str, fingerprints: dict[str, object]
) -> None:
    """Two spellings of "no document", one outcome, and it is never :data:`DECLARED_NO_SOFTWARE`."""
    endpoints = [_endpoint("unread", "Unread Payer", "https://unread.example.com/fhir")]
    result = by_platform(endpoints, fingerprints)  # type: ignore[arg-type]

    assert result.unmeasured == ("unread",), label
    assert result.endpoints_measured == 0, label
    assert result.endpoints_in_registry == 1, label
    assert result.footprints == (), label
    assert DECLARED_NO_SOFTWARE not in {f.key for f in result.footprints}, label


def test_a_document_that_declared_no_software_is_measured_not_unmeasured() -> None:
    """The other half of the same distinction, so neither test passes by collapsing both."""
    endpoints = [_endpoint("quiet", "Quiet Payer", "https://quiet.example.com/fhir")]
    result = by_platform(endpoints, {"quiet": {"software_name": None, "fhir_version": "4.0.1"}})

    assert result.unmeasured == ()
    assert result.endpoints_measured == 1
    assert [f.key for f in result.footprints] == [DECLARED_NO_SOFTWARE]


def test_a_record_with_observations_and_no_fingerprint_is_left_out_of_the_mapping() -> None:
    """``fingerprints_from_history`` must not invent a fingerprint for a reachable endpoint.

    A run that reached an endpoint and retrieved no document writes observations and no
    fingerprint. Mapping that to ``{}`` would make :func:`by_platform` read it as "a document was
    read", which is exactly backwards.
    """
    history = {
        "_meta": {"mode": "live"},
        "read": {"fingerprint": {"software_name": "X"}, "observations": [{"date": "d", "up": 1}]},
        "reached-nothing-read": {"observations": [{"date": "d", "up": 1}]},
        "empty-fingerprint": {"fingerprint": {}},
    }
    mapping = fingerprints_from_history(history)
    assert set(mapping) == {"read"}


def test_history_metadata_keys_are_not_endpoints() -> None:
    assert fingerprints_from_history({"_meta": {"mode": "live"}}) == {}


# --------------------------------------------------------------------------------------------
# 3. Both population numbers travel together.
# --------------------------------------------------------------------------------------------


def test_every_report_states_measured_and_registry_size() -> None:
    endpoints = [
        _endpoint("a", "Payer A", "https://a.example.com/fhir"),
        _endpoint("b", "Payer B", "https://b.example.com/fhir"),
    ]
    text = render(by_platform(endpoints, {"a": {"software_name": "P"}}))
    assert "1 of 2 registry endpoints placed" in text
    assert "not placed (1, no document has been read): b" in text


def test_the_json_view_carries_both_numbers_and_the_intermediary_flag() -> None:
    endpoints = [
        _endpoint("a", "Payer A", "https://shared.example.com/a"),
        _endpoint("b", "Payer B", "https://shared.example.com/b"),
    ]
    payload = by_host(endpoints).as_dict()
    assert payload["endpoints_measured"] == 2
    assert payload["endpoints_in_registry"] == 2
    assert payload["axis"] == HOST
    footprints = payload["footprints"]
    assert isinstance(footprints, list)
    assert footprints[0]["could_be_an_intermediary"] is True
    assert footprints[0]["organizations"] == ["payer-a", "payer-b"]


def test_a_population_with_no_shared_domain_says_so_rather_than_naming_one() -> None:
    endpoints = [
        _endpoint("a", "Payer A", "https://a.example.com/one"),
        _endpoint("b", "Payer A", "https://a.example.com/two"),
    ]
    result = by_host(endpoints)
    assert result.largest_shared is None
    assert "could be an intermediary: none" in render(result)


def test_a_base_url_with_no_hostname_is_named_rather_than_dropped() -> None:
    """It cannot reach here through the loader, which is why it must be visible if it ever does."""
    result = by_host([_endpoint("broken", "Broken", "https:///fhir")])
    assert result.unmeasured == ("broken",)
    assert result.endpoints_measured == 0
    assert result.endpoints_in_registry == 1


def test_threshold_is_reported_only_when_one_was_given() -> None:
    result = by_host([_endpoint("a", "A", "https://a.example.com/fhir")])
    assert "threshold" not in render(result)
    assert "threshold 2: not crossed" in render(result, 2)


# --------------------------------------------------------------------------------------------
# registrable_domain
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("api.bcbsnefhir.com", "bcbsnefhir.com"),
        ("epicproxy.et1476.epichosted.com", "epichosted.com"),
        ("server.fire.ly", "fire.ly"),
        ("example.com", "example.com"),
        ("localhost", "localhost"),
        ("API.Example.COM.", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


def test_host_of_reads_the_hostname_without_the_port() -> None:
    assert host_of(_endpoint("a", "A", "https://api.example.com:8443/fhir")) == "api.example.com"


# --------------------------------------------------------------------------------------------
# Over the committed registry, because that is where these numbers get quoted from.
# --------------------------------------------------------------------------------------------


def _registry() -> list[Endpoint]:
    return [e for e in load_registry(REGISTRY) if e.enabled]


def test_the_host_axis_places_every_committed_endpoint() -> None:
    """No endpoint may fall out of the host denominator: every entry has an https base URL."""
    result = by_host(_registry())
    assert result.unmeasured == ()
    assert result.endpoints_measured == result.endpoints_in_registry == len(_registry())
    assert result.axis == HOST


def test_no_committed_endpoint_groups_under_a_bare_public_suffix() -> None:
    """The stated cost of not carrying the Public Suffix List, checked against the real data.

    A host under an unlisted two-label suffix would group one label too coarsely and merge
    unrelated organizations into one apparent footprint - the direction that *overstates*
    concentration. This asserts it has not happened, so the approximation stays checked rather
    than assumed. If it ever fires, the repair is to add the suffix to ``MULTI_LABEL_SUFFIXES``.
    """
    suspicious = [
        f.key
        for f in by_host(_registry()).footprints
        if f.key in MULTI_LABEL_SUFFIXES or f.key.count(".") == 0
    ]
    assert not suspicious, suspicious


def test_the_platform_axis_over_the_committed_history_reports_two_numbers() -> None:
    """The committed history is a seed rather than the record, and the output must say so.

    ``data/history.json`` on ``main`` covers a fraction of the registry; the record lives on the
    ``capability-history`` branch. So this does not assert a platform distribution - it asserts
    that the measured population is reported as smaller than the registry, which is the property
    that keeps a partial read from being quoted as a market fact.
    """
    endpoints = _registry()
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    result = by_platform(endpoints, fingerprints_from_history(history))

    assert result.axis == PLATFORM
    assert result.endpoints_in_registry == len(endpoints)
    assert result.endpoints_measured + len(result.unmeasured) == len(endpoints)
    assert result.endpoints_measured < result.endpoints_in_registry
    assert f"{result.endpoints_measured} of {result.endpoints_in_registry}" in render(result)
