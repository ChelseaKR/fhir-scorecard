"""Shared fixtures: synthetic FHIR documents. Tests never touch the network."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path) -> Iterator[None]:
    """Run every test from a throwaway directory.

    Several CLI options default to relative paths under ``data/``, and the suite runs from the
    repository root, so a test that forgets to override one of them silently reads or writes the
    real curation files instead of failing: an offline run against a fixture registry would load
    the shipped cohort, and a run without ``--history`` would append fixture observations to the
    live availability record. Cutting the repository out of the default entirely makes that
    impossible rather than remembered. No test addresses a repository file by relative path, and
    one that wants to should use an absolute one.
    """
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield
    finally:
        os.chdir(previous)


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
