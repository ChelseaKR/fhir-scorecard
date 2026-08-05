from __future__ import annotations

import json

from fhir_scorecard.capability import parse_capability, parse_smart


def test_good_capability_parses(good_capability_bytes: bytes) -> None:
    facts = parse_capability(good_capability_bytes)
    assert facts.parsed and facts.resource_type_ok
    assert facts.fhir_version == "4.0.1"
    assert facts.software_name == "SyntheticServer"
    assert facts.resource_count == 6
    assert facts.resources_with_interactions == 6
    assert any("us-core" in p for p in facts.supported_profiles)
    assert facts.declares_oauth_security


def test_not_json_fails_closed() -> None:
    facts = parse_capability(b"<html>Not Found</html>")
    assert not facts.parsed
    assert facts.parse_error is not None


def test_wrong_resource_type_flagged() -> None:
    facts = parse_capability(json.dumps({"resourceType": "OperationOutcome"}).encode())
    assert facts.parsed and not facts.resource_type_ok


def test_non_object_json_fails_closed() -> None:
    assert not parse_capability(b"[1, 2]").parsed
    assert not parse_capability(b"").parsed


def test_empty_capability_yields_zero_facts() -> None:
    facts = parse_capability(json.dumps({"resourceType": "CapabilityStatement"}).encode())
    assert facts.parsed and facts.resource_type_ok
    assert facts.resource_count == 0
    assert facts.fhir_version is None
    assert not facts.declares_oauth_security


def test_smart_parses(good_smart_bytes: bytes) -> None:
    smart = parse_smart(good_smart_bytes)
    assert smart.parsed and smart.has_authorization_endpoint and smart.has_token_endpoint


def test_smart_incomplete() -> None:
    smart = parse_smart(json.dumps({"authorization_endpoint": "https://x"}).encode())
    assert smart.parsed and smart.has_authorization_endpoint and not smart.has_token_endpoint
    assert not parse_smart(b"nope").parsed
