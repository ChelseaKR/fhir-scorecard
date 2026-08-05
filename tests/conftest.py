"""Shared fixtures: synthetic FHIR documents. Tests never touch the network."""

from __future__ import annotations

import json

import pytest


def good_capability() -> dict[str, object]:
    return {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "software": {"name": "SyntheticServer", "version": "9.9.9"},
        "implementation": {"description": "Synthetic test fixture"},
        "rest": [{
            "mode": "server",
            "security": {"service": [{"coding": [{"code": "SMART-on-FHIR"}]}]},
            "resource": [
                {"type": t,
                 "interaction": [{"code": "read"}, {"code": "search-type"}],
                 "supportedProfile": [
                     f"http://hl7.org/fhir/us/core/StructureDefinition/us-core-{t.lower()}"]}
                for t in ["Patient", "Coverage", "ExplanationOfBenefit",
                          "Practitioner", "Organization", "Observation"]
            ],
        }],
    }


def good_smart() -> dict[str, object]:
    return {
        "authorization_endpoint": "https://example.test/authorize",
        "token_endpoint": "https://example.test/token",
        "capabilities": ["launch-standalone"],
    }


@pytest.fixture()
def good_capability_bytes() -> bytes:
    return json.dumps(good_capability()).encode()


@pytest.fixture()
def good_smart_bytes() -> bytes:
    return json.dumps(good_smart()).encode()
