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


@pytest.mark.parametrize("mutation, message", [
    ({"base_url": "http://insecure.test"}, "https"),
    ({"id": "Bad Slug!"}, "slug"),
    ({"kind": "vendor"}, "kind"),
    ({"verification": None}, "verification"),
    ({"verification": {"method": "x", "date": "August 4"}}, "YYYY-MM-DD"),
    ({"enabled": "yes"}, "boolean"),
])
def test_invalid_entries_refused(tmp_path: Path, mutation: dict[str, Any],
                                 message: str) -> None:
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
    def __init__(self, status: int = 200, body: bytes = b"{}",
                 raises: Exception | None = None) -> None:
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
    assert not result.ok and result.error == "TimeoutError"


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
