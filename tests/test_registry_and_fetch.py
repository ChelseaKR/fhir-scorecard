from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard.fetch import fetch_json
from fhir_scorecard.registry import load_registry


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "example-payer",
        "name": "Example Payer",
        "kind": "payer",
        "base_url": "https://fhir.example.test/r4",
        "verification": {"method": "live fetch", "date": "2026-08-04"},
    }
    entry.update(overrides)
    return entry


def _write(tmp_path: Path, endpoints: list[dict[str, Any]]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"endpoints": endpoints}))
    return path


def test_valid_registry_loads(tmp_path: Path) -> None:
    eps = load_registry(_write(tmp_path, [_entry()]))
    assert eps[0].endpoint_id == "example-payer"
    assert eps[0].enabled


def test_shipped_registry_is_valid() -> None:
    eps = load_registry(Path(__file__).parent.parent / "data" / "registry.json")
    assert len(eps) >= 2
    assert all(e.base_url.startswith("https://") for e in eps)


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"base_url": "http://insecure.test"}, "https"),
        ({"id": "Bad Slug!"}, "slug"),
        ({"kind": "vendor"}, "kind"),
        ({"verification": None}, "verification"),
        ({"verification": {"method": "x", "date": "August 4"}}, "YYYY-MM-DD"),
        ({"enabled": "yes"}, "boolean"),
    ],
)
def test_invalid_entries_refused(tmp_path: Path, mutation: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_registry(_write(tmp_path, [_entry(**mutation)]))


def test_duplicate_ids_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_registry(_write(tmp_path, [_entry(), _entry()]))


def test_registry_shape_refused(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([1, 2]))
    with pytest.raises(ValueError, match="endpoints"):
        load_registry(path)


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, n: int) -> bytes:
        return self._body.read(n)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeOpener(urllib.request.OpenerDirector):
    def __init__(
        self, status: int = 200, body: bytes = b"{}", raises: Exception | None = None
    ) -> None:
        super().__init__()
        self._status, self._body, self._raises = status, body, raises

    def open(self, fullurl: Any, data: Any = None, timeout: Any = None) -> Any:
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._status, self._body)


def test_fetch_refuses_http() -> None:
    result = fetch_json("http://insecure.test/metadata")
    assert not result.ok and result.error is not None and "https" in result.error


def test_fetch_success_via_injected_opener() -> None:
    result = fetch_json("https://x.test/metadata", opener=_FakeOpener(body=b'{"a":1}'))
    assert result.ok and result.status == 200 and result.body == b'{"a":1}'


def test_fetch_network_error_fails_closed() -> None:
    result = fetch_json("https://x.test/metadata", opener=_FakeOpener(raises=TimeoutError()))
    assert not result.ok and result.error == "connection timed out"


def test_expects_defaults_to_r4_and_validates(tmp_path: Path) -> None:
    """Endpoints are graded against the FHIR release they intend to serve."""
    from fhir_scorecard.registry import version_prefix

    assert load_registry(_write(tmp_path, [_entry()]))[0].expects == "r4"
    assert load_registry(_write(tmp_path, [_entry(expects="r5")]))[0].expects == "r5"
    with pytest.raises(ValueError, match="expects"):
        load_registry(_write(tmp_path, [_entry(expects="dstu2")]))
    assert version_prefix("stu3") == "3."
    assert version_prefix("r5") == "5."
    assert version_prefix("nonsense") == "4."  # falls back to the CMS-required release


def test_error_descriptions_distinguish_causes() -> None:
    """Bare 'URLError' conflates a host that does not exist with one this vantage cannot reach.
    On 2026-08-05 that ambiguity recorded a live payer endpoint as dead."""
    import socket
    import ssl
    import urllib.error

    from fhir_scorecard.fetch import describe_error

    cert = ssl.SSLCertVerificationError("bad chain")
    cert.verify_message = "self-signed certificate in certificate chain"
    tls = describe_error(urllib.error.URLError(cert))
    assert "TLS certificate verification failed" in tls
    assert "vantage-local" in tls

    gai = socket.gaierror(8, "nodename nor servname provided")
    dns = describe_error(urllib.error.URLError(gai))
    assert "DNS did not resolve" in dns

    assert "timed out" in describe_error(urllib.error.URLError(TimeoutError()))
    assert "refused" in describe_error(urllib.error.URLError(ConnectionRefusedError()))
    assert describe_error(ValueError()) == "ValueError"


def test_fetch_surfaces_tls_interception(monkeypatch) -> None:
    import ssl
    import urllib.error

    cert = ssl.SSLCertVerificationError("bad chain")
    cert.verify_message = "self-signed certificate in certificate chain"

    result = fetch_json(
        "https://x.test/metadata", opener=_FakeOpener(raises=urllib.error.URLError(cert))
    )
    assert not result.ok
    assert result.error is not None and "vantage-local" in result.error


def test_verification_basis_defaults_to_live_capability(tmp_path: Path) -> None:
    endpoint = load_registry(_write(tmp_path, [_entry()]))[0]
    assert endpoint.verification_basis == "live_capability"
    assert endpoint.verification_source == "" and endpoint.verification_observed == ""


def _documented(**verification: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "method": "base URL printed on the plan's own interoperability page",
        "date": "2026-08-19",
        "basis": "publisher_documented",
        "source": "https://plan.test/interoperability",
        "observed": "DNS did not resolve (nodename nor servname provided)",
    }
    record.update(verification)
    return {"verification": record}


def test_a_documented_but_unretrievable_endpoint_is_listed_with_its_receipts(
    tmp_path: Path,
) -> None:
    """The point of the second basis: the endpoint stays in the published set, not out of it."""
    endpoint = load_registry(_write(tmp_path, [_entry(**_documented())]))[0]
    assert endpoint.verification_basis == "publisher_documented"
    assert endpoint.verification_source == "https://plan.test/interoperability"
    assert "DNS did not resolve" in endpoint.verification_observed


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"basis": "vibes"}, "basis"),
        ({"source": "   "}, "source is required"),
        ({"observed": ""}, "observed is required"),
    ],
)
def test_a_documented_entry_without_its_receipts_is_refused(
    tmp_path: Path, mutation: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_registry(_write(tmp_path, [_entry(**_documented(**mutation))]))


def test_source_and_observed_must_be_strings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be strings"):
        load_registry(_write(tmp_path, [_entry(**_documented(source=["a"]))]))


def test_a_recheck_is_its_own_dated_record_not_an_overwrite(tmp_path: Path) -> None:
    """Overwriting the date loses the curation date; leaving it alone lets stale read as fresh."""
    entry = _entry(
        verification={
            "method": "live fetch",
            "date": "2026-08-04",
            "reverified": {"date": "2026-08-19", "method": "live re-fetch, publisher unchanged"},
        }
    )
    endpoint = load_registry(_write(tmp_path, [entry]))[0]
    assert endpoint.verified_date == "2026-08-04"
    assert endpoint.reverified_date == "2026-08-19"
    assert endpoint.verified_as_of == "2026-08-19"


def test_an_entry_never_re_checked_reports_its_curation_date_as_the_latest(tmp_path: Path) -> None:
    endpoint = load_registry(_write(tmp_path, [_entry()]))[0]
    assert endpoint.reverified_date == "" and endpoint.verified_as_of == "2026-08-04"


@pytest.mark.parametrize(
    "reverified, message",
    [
        ("2026-08-19", "must be an object"),
        ({"date": "August 19", "method": "x"}, "reverified.date"),
        ({"date": "2026-08-19"}, "reverified.method"),
        ({"date": "2026-08-19", "method": "  "}, "reverified.method"),
    ],
)
def test_a_malformed_recheck_is_refused(tmp_path: Path, reverified: Any, message: str) -> None:
    entry = _entry(
        verification={"method": "live fetch", "date": "2026-08-04", "reverified": reverified}
    )
    with pytest.raises(ValueError, match=message):
        load_registry(_write(tmp_path, [entry]))


def _endpoint(**overrides: Any) -> Any:
    from fhir_scorecard.registry import Endpoint

    fields: dict[str, Any] = {
        "endpoint_id": "x",
        "name": "X",
        "kind": "payer",
        "base_url": "https://x.test/r4",
        "verified_method": "live CapabilityStatement fetch",
        "verified_date": "2026-08-04",
    }
    fields.update(overrides)
    return Endpoint(**fields)


def test_the_page_says_when_an_entry_was_last_checked_and_when_it_was_not() -> None:
    """A curation date printed alone reads as a current one. It is not one."""
    from fhir_scorecard.cli import _verification_sentence

    never = _verification_sentence(_endpoint())
    assert "recorded 2026-08-04" in never
    assert "No later re-check is recorded" in never
    assert "the last time anyone checked this entry" in never

    rechecked = _verification_sentence(
        _endpoint(reverified_date="2026-08-19", reverified_method="live re-fetch, publisher same")
    )
    assert "Re-checked 2026-08-19: live re-fetch, publisher same." in rechecked

    documented = _verification_sentence(
        _endpoint(
            verification_basis="publisher_documented",
            verification_source="https://plan.test/interop",
            verification_observed="HTTP 404",
        )
    )
    assert "not on a retrieved conformance document" in documented
    assert "Published at https://plan.test/interop" in documented
    assert "this probe observed: HTTP 404" in documented

    assert _verification_sentence(None) == "verification record unavailable"
