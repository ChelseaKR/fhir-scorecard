"""Tests for bundle.py: the compliance report bundle's pure core.

Coverage mirrors gtfs-scorecard's own test_bundle.py in spirit (request validation, id
classification, the built archive's manifest), plus the one deliberate divergence this project's
module docstring calls out: a "not observed" endpoint is *included* in the archive, not dropped,
because "not observed" is itself a finding here (grading.py), not the absence of one the way an
unpublished GTFS scorecard is for gtfs-scorecard's agencies.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import zipfile
from pathlib import Path

import pytest

from fhir_scorecard import bundle
from fhir_scorecard.grading import NOT_OBSERVED, DimensionScore, Finding, Scorecard
from fhir_scorecard.registry import Endpoint

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_VALID_BUNDLE_ID = "a" * 32


def _base_request(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "bundle_id": _VALID_BUNDLE_ID,
        "program_name": "Acme Compliance Partners",
        "accent": "#123456",
        "logo": "",
        "endpoint_ids": "one-payer, two-payer",
        "deliver_to": "buyer@example.org",
        "cadence": "one_time",
    }
    raw.update(overrides)
    return raw


# ---------------------------------------------------------------------------
# parse_request
# ---------------------------------------------------------------------------


def test_parse_request_happy_path() -> None:
    request = bundle.parse_request(_base_request())
    assert request.bundle_id == _VALID_BUNDLE_ID
    assert request.endpoint_ids == ("one-payer", "two-payer")
    assert request.cadence == "one_time"
    assert request.as_dict()["endpoint_ids"] == ["one-payer", "two-payer"]


@pytest.mark.parametrize(
    "overrides,message_fragment",
    [
        ({"bundle_id": "not-hex"}, "32 lowercase hex"),
        ({"program_name": ""}, "program_name is required"),
        ({"program_name": "x" * 200}, "characters or fewer"),
        ({"accent": "notacolor"}, "accent must be"),
        ({"deliver_to": "not-an-email"}, "deliver_to must be"),
        ({"cadence": "monthly"}, "cadence must be"),
        ({"endpoint_ids": ""}, "at least one endpoint"),
        ({"endpoint_ids": "Has Spaces!"}, "not a registry id"),
    ],
)
def test_parse_request_refuses_each_bad_field(overrides: dict, message_fragment: str) -> None:
    with pytest.raises(bundle.BundleError, match=message_fragment):
        bundle.parse_request(_base_request(**overrides))


def test_parse_request_dedupes_and_lowercases_ids() -> None:
    request = bundle.parse_request(_base_request(endpoint_ids="Foo, foo, BAR\nbar"))
    assert request.endpoint_ids == ("foo", "bar")


def test_parse_request_accepts_a_list_of_ids() -> None:
    request = bundle.parse_request(_base_request(endpoint_ids=["one", "two"]))
    assert request.endpoint_ids == ("one", "two")


def test_parse_request_enforces_the_plans_cap_not_just_the_ceiling() -> None:
    ids = ",".join(f"endpoint-{i}" for i in range(20))
    with pytest.raises(bundle.BundleError, match="your plan covers at most 15"):
        bundle.parse_request(_base_request(endpoint_ids=ids), max_endpoints=15)


def test_parse_request_over_the_hard_ceiling_names_the_ceiling() -> None:
    ids = ",".join(f"endpoint-{i}" for i in range(80))
    with pytest.raises(bundle.BundleError, match="at most 70 endpoints"):
        bundle.parse_request(_base_request(endpoint_ids=ids))


def test_parse_request_max_endpoints_cannot_be_below_one() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        bundle.parse_request(_base_request(), max_endpoints=0)


def test_parse_request_accepts_a_valid_data_uri_logo() -> None:
    encoded = base64.b64encode(_TINY_PNG).decode()
    request = bundle.parse_request(_base_request(logo=f"data:image/png;base64,{encoded}"))
    assert request.logo == f"data:image/png;base64,{encoded}"


def test_parse_request_rejects_an_oversized_data_uri_logo() -> None:
    huge = base64.b64encode(b"x" * (bundle.MAX_LOGO_BYTES + 1)).decode()
    with pytest.raises(bundle.BundleError, match="KiB or smaller"):
        bundle.parse_request(_base_request(logo=f"data:image/png;base64,{huge}"))


def test_parse_request_rejects_a_non_https_logo() -> None:
    with pytest.raises(bundle.BundleError, match="https URL"):
        bundle.parse_request(_base_request(logo="http://example.test/logo.png"))


def test_endpoint_ids_rejects_a_type_that_is_neither_string_nor_list() -> None:
    with pytest.raises(bundle.BundleError, match="comma-separated string or a list"):
        bundle._endpoint_ids(12345)


def test_logo_rejects_a_data_uri_with_an_unsupported_media_type() -> None:
    with pytest.raises(bundle.BundleError, match="SVG, PNG, or JPEG data: URI"):
        bundle._logo("data:image/gif;base64,AAAA")


def test_logo_rejects_malformed_base64_inside_an_otherwise_shaped_data_uri() -> None:
    with pytest.raises(bundle.BundleError, match="not valid base64"):
        bundle._logo("data:image/png;base64,A")


def test_validate_public_https_url_rejects_credentials_in_the_netloc() -> None:
    with pytest.raises(bundle.BundleError, match="must not carry credentials"):
        bundle._validate_public_https_url("https://user:pass@cdn.example.test/logo.png")


def test_validate_public_https_url_rejects_a_url_with_no_host() -> None:
    with pytest.raises(bundle.BundleError, match="names no host"):
        bundle._validate_public_https_url("https:///logo.png")


def test_validate_public_https_url_rejects_an_unresolvable_host(monkeypatch) -> None:
    import fhir_scorecard.intake as intake

    def _fail(host: str) -> list[str]:
        raise OSError("name resolution failed")

    monkeypatch.setattr(intake, "default_resolver", _fail)
    with pytest.raises(bundle.BundleError, match="did not resolve"):
        bundle._validate_public_https_url("https://nowhere.example.test/logo.png")


def test_validate_public_https_url_accepts_a_literal_public_ip_host() -> None:
    # A literal IP in the URL never reaches the resolver at all.
    bundle._validate_public_https_url("https://93.184.216.34/logo.png")


def test_parse_request_rejects_a_logo_url_resolving_to_a_private_address(monkeypatch) -> None:
    import fhir_scorecard.intake as intake

    monkeypatch.setattr(intake, "default_resolver", lambda host: ["10.0.0.5"])
    with pytest.raises(bundle.BundleError, match="non-public address"):
        bundle.parse_request(_base_request(logo="https://internal.example.test/logo.svg"))


def test_parse_request_accepts_a_logo_url_resolving_to_a_public_address(monkeypatch) -> None:
    import fhir_scorecard.intake as intake

    monkeypatch.setattr(intake, "default_resolver", lambda host: ["93.184.216.34"])
    request = bundle.parse_request(_base_request(logo="https://cdn.example.test/logo.svg"))
    assert request.logo == "https://cdn.example.test/logo.svg"


# ---------------------------------------------------------------------------
# classify: the domain-specific divergence from gtfs-scorecard's bundle.py
# ---------------------------------------------------------------------------


def _registry() -> list[Endpoint]:
    return [
        Endpoint(
            endpoint_id="live-payer",
            name="Live Payer",
            kind="payer",
            base_url="https://live.test/fhir",
            verified_method="live_capability",
            verified_date="2026-08-01",
        ),
        Endpoint(
            endpoint_id="dark-payer",
            name="Dark Payer",
            kind="payer",
            base_url="https://dark.test/fhir",
            verified_method="live_capability",
            verified_date="2026-08-01",
            enabled=False,
        ),
        Endpoint(
            endpoint_id="unpublished-payer",
            name="Unpublished Payer",
            kind="payer",
            base_url="https://unpub.test/fhir",
            verified_method="live_capability",
            verified_date="2026-08-01",
        ),
    ]


def test_classify_unknown_id_is_excluded() -> None:
    statuses = bundle.classify(("ghost-id",), _registry(), {})
    assert statuses["ghost-id"] == bundle.STATUS_UNKNOWN


def test_classify_disabled_entry_is_excluded() -> None:
    statuses = bundle.classify(("dark-payer",), _registry(), {})
    assert statuses["dark-payer"] == bundle.STATUS_DISABLED


def test_classify_enabled_but_no_scorecard_is_not_published() -> None:
    statuses = bundle.classify(("unpublished-payer",), _registry(), {})
    assert statuses["unpublished-payer"] == bundle.STATUS_NOT_PUBLISHED


def test_classify_not_observed_scorecard_is_still_included() -> None:
    """The one rule this module's docstring exists to state: a graded-but-unreachable endpoint
    is a finding, not grounds for exclusion."""
    scorecards = {"live-payer": {"grade": NOT_OBSERVED, "reachable": False}}
    statuses = bundle.classify(("live-payer",), _registry(), scorecards)
    assert statuses["live-payer"] == bundle.STATUS_INCLUDED


def test_classify_lettergraded_scorecard_is_included() -> None:
    scorecards = {"live-payer": {"grade": "B", "reachable": True}}
    statuses = bundle.classify(("live-payer",), _registry(), scorecards)
    assert statuses["live-payer"] == bundle.STATUS_INCLUDED


def test_plan_splits_included_from_refused() -> None:
    request = bundle.parse_request(_base_request(endpoint_ids="live-payer, dark-payer, ghost-id"))
    scorecards = {"live-payer": {"grade": "A", "reachable": True}}
    built = bundle.plan(request, _registry(), scorecards)
    assert built["included"] == ["live-payer"]
    refused_ids = {row["id"] for row in built["refused"]}
    assert refused_ids == {"dark-payer", "ghost-id"}


# ---------------------------------------------------------------------------
# build_bundle: the archive, the manifest, and the "never silently dropped" rule
# ---------------------------------------------------------------------------


def _write_finding(code: str, ok: bool) -> dict:
    return {
        "code": code,
        "ok": ok,
        "points": 60 if ok else 0,
        "max_points": 60,
        "message": f"{code} {'passed' if ok else 'failed'}",
        "citation": "https://hl7.org/fhir/R4/http.html",
        "observed": True,
        "unanswered": False,
        "withheld_points": 0,
    }


def _write_scorecard_dict(endpoint_id: str, *, grade: str, reachable: bool) -> dict:
    return dataclasses.asdict(
        Scorecard(
            endpoint_id=endpoint_id,
            name=endpoint_id.replace("-", " ").title(),
            grade=grade,
            reachable=reachable,
            dimensions=(
                DimensionScore(
                    key="reachability",
                    title="Reachability",
                    score=100 if reachable else None,
                    findings=(Finding(**_write_finding("R1", reachable)),),
                ),
            ),
            kind="payer",
        )
    )


def _write_fixture(
    tmp_path: Path, *, registry: list[Endpoint], scorecards: dict[str, dict]
) -> tuple[Path, Path]:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": e.endpoint_id,
                        "name": e.name,
                        "kind": e.kind,
                        "base_url": e.base_url,
                        "enabled": e.enabled,
                        "verification": {
                            "method": e.verified_method,
                            "date": e.verified_date,
                        },
                    }
                    for e in registry
                ]
            }
        )
    )
    scorecards_path = tmp_path / "scorecards.json"
    scorecards_path.write_text(
        json.dumps(
            {
                "generator": "fhir-scorecard",
                "generated_at": "2026-09-15T00:00:00Z",
                "scorecards": list(scorecards.values()),
            }
        )
    )
    return registry_path, scorecards_path


def test_build_bundle_end_to_end(tmp_path: Path) -> None:
    registry_path, scorecards_path = _write_fixture(
        tmp_path,
        registry=_registry(),
        scorecards={
            "live-payer": _write_scorecard_dict("live-payer", grade="B", reachable=True),
            "unpublished-payer": _write_scorecard_dict(
                "unpublished-payer", grade=NOT_OBSERVED, reachable=False
            ),
        },
    )
    request = bundle.parse_request(
        _base_request(
            endpoint_ids="live-payer, dark-payer, unpublished-payer, ghost-id",
            program_name="Acme Compliance Partners",
        )
    )
    out_zip = tmp_path / "out" / "bundle.zip"
    manifest = bundle.build_bundle(
        request, out_zip, registry_path=registry_path, scorecards_path=scorecards_path
    )

    assert manifest["requested"] == 4
    # live-payer (graded) AND unpublished-payer (registered, and its own scorecard entry
    # happens to say "not observed") are both included; only the unknown id and the disabled
    # entry are refused. This is the divergence from gtfs-scorecard's equivalent test.
    assert manifest["included"] == 2
    by_id = {row["id"]: row for row in manifest["endpoints"]}
    assert by_id["dark-payer"]["status"] == bundle.STATUS_DISABLED
    assert by_id["ghost-id"]["status"] == bundle.STATUS_UNKNOWN
    assert by_id["live-payer"]["status"] == bundle.STATUS_INCLUDED
    assert by_id["unpublished-payer"]["status"] == bundle.STATUS_INCLUDED

    assert out_zip.exists()
    with zipfile.ZipFile(out_zip) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "README.txt" in names
        assert "reports/live-payer-report.html" in names
        assert "reports/unpublished-payer-report.html" in names
        assert "reports/dark-payer-report.html" not in names
        assert "reports/ghost-id-report.html" not in names
        readme = archive.read("README.txt").decode()
        assert "Acme Compliance Partners" in readme
        assert "dark-payer" in readme  # not silently dropped: named with its reason
        report_html = archive.read("reports/live-payer-report.html").decode()
        assert "Prepared by Acme Compliance Partners" in report_html


def test_build_bundle_never_silently_drops_a_requested_id(tmp_path: Path) -> None:
    """Negative control: every requested id appears in the manifest exactly once, whatever its
    outcome. A bundle that silently shrank the request would pass every other assertion here."""
    registry_path, scorecards_path = _write_fixture(
        tmp_path,
        registry=_registry(),
        scorecards={"live-payer": _write_scorecard_dict("live-payer", grade="A", reachable=True)},
    )
    requested_ids = ("live-payer", "dark-payer", "unpublished-payer", "ghost-id")
    request = bundle.parse_request(_base_request(endpoint_ids=",".join(requested_ids)))
    manifest = bundle.build_bundle(
        request,
        tmp_path / "bundle.zip",
        registry_path=registry_path,
        scorecards_path=scorecards_path,
    )
    manifest_ids = tuple(row["id"] for row in manifest["endpoints"])
    assert manifest_ids == requested_ids
    assert manifest["requested"] == len(requested_ids)


def test_build_bundle_report_content_tracks_the_source_grade(tmp_path: Path) -> None:
    """Negative control at the build layer: two builds against two different scorecards.json
    fixtures for the same endpoint id must produce two different report bodies. Guards against
    a build path that renders from a cached or stale in-memory copy instead of the file given."""
    registry = _registry()
    for grade, expect_present, expect_absent in (("A", "A", "F"), ("F", "F", "A")):
        registry_path, scorecards_path = _write_fixture(
            tmp_path,
            registry=registry,
            scorecards={
                "live-payer": _write_scorecard_dict("live-payer", grade=grade, reachable=True)
            },
        )
        request = bundle.parse_request(_base_request(endpoint_ids="live-payer"))
        out_zip = tmp_path / f"bundle-{grade}.zip"
        bundle.build_bundle(
            request, out_zip, registry_path=registry_path, scorecards_path=scorecards_path
        )
        with zipfile.ZipFile(out_zip) as archive:
            html = archive.read("reports/live-payer-report.html").decode()
        assert f'"grade grade-{expect_present.lower()}"' in html
        assert f'"grade grade-{expect_absent.lower()}"' not in html


# ---------------------------------------------------------------------------
# delivery_email / expires_on / archive_key / new_bundle_id
# ---------------------------------------------------------------------------


def test_archive_key_is_stable_and_namespaced() -> None:
    assert (
        bundle.archive_key(_VALID_BUNDLE_ID) == f"compliance-bundles/{_VALID_BUNDLE_ID}/bundle.zip"
    )


def test_new_bundle_id_is_32_lowercase_hex() -> None:
    generated = bundle.new_bundle_id()
    assert bundle.BUNDLE_ID_RE.match(generated)


def test_expires_on_adds_thirty_days() -> None:
    import datetime as dt

    when = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    assert bundle.expires_on(when) == "2026-01-31"


def test_delivery_email_lists_what_was_and_was_not_included() -> None:
    request = bundle.parse_request(_base_request(endpoint_ids="live-payer, dark-payer"))
    manifest = {
        "requested": 2,
        "included": 1,
        "endpoints": [
            {"id": "live-payer", "status": bundle.STATUS_INCLUDED, "detail": ""},
            {
                "id": "dark-payer",
                "status": bundle.STATUS_DISABLED,
                "detail": bundle._STATUS_DETAIL[bundle.STATUS_DISABLED],
            },
        ],
    }
    email = bundle.delivery_email(request, manifest, "https://x.test/download/abc", "2026-10-15")
    assert email.to == "buyer@example.org"
    assert "1 of 2 requested endpoints" in email.body
    assert "dark-payer" in email.body
    assert "https://x.test/download/abc" in email.body


def test_delivery_email_mentions_quarterly_refresh_only_when_that_is_the_cadence() -> None:
    one_time = bundle.parse_request(_base_request(cadence="one_time"))
    quarterly = bundle.parse_request(_base_request(cadence="quarterly"))
    manifest = {"requested": 1, "included": 1, "endpoints": []}
    assert (
        "refreshes quarterly"
        not in bundle.delivery_email(one_time, manifest, "https://x.test/d", "2026-10-15").body
    )
    assert (
        "refreshes quarterly"
        in bundle.delivery_email(quarterly, manifest, "https://x.test/d", "2026-10-15").body
    )


# ---------------------------------------------------------------------------
# resolve_logo
# ---------------------------------------------------------------------------


def test_resolve_logo_passes_through_a_data_uri() -> None:
    uri = "data:image/svg+xml;base64,PHN2Zy8+"
    assert bundle.resolve_logo(uri) == uri


def test_resolve_logo_fetches_and_sniffs_png() -> None:
    result = bundle.resolve_logo("https://cdn.example.test/logo.png", fetch=lambda url: _TINY_PNG)
    assert result == f"data:image/png;base64,{base64.b64encode(_TINY_PNG).decode()}"


def test_resolve_logo_rejects_an_unrecognized_image_type() -> None:
    with pytest.raises(bundle.BundleError, match="did not return"):
        bundle.resolve_logo("https://cdn.example.test/logo.bin", fetch=lambda url: b"not-an-image")


def test_resolve_logo_rejects_oversized_content() -> None:
    huge = b"x" * (bundle.MAX_LOGO_BYTES + 1)
    with pytest.raises(bundle.BundleError, match="KiB or smaller"):
        bundle.resolve_logo("https://cdn.example.test/logo.png", fetch=lambda url: huge)


def test_resolve_logo_wraps_a_fetch_failure() -> None:
    def _boom(url: str) -> bytes:
        raise OSError("connection reset")

    with pytest.raises(bundle.BundleError, match="could not be fetched"):
        bundle.resolve_logo("https://cdn.example.test/logo.png", fetch=_boom)


def test_sniff_media_type_recognizes_jpeg_and_svg() -> None:
    assert bundle._sniff_media_type(b"\xff\xd8\xff\xe0rest of jpeg") == "image/jpeg"
    assert bundle._sniff_media_type(b"  <svg xmlns='x'></svg>") == "image/svg+xml"


def test_default_fetch_delegates_to_the_one_guarded_fetcher(monkeypatch) -> None:
    """bundle.py must never open its own connection: tests/test_probe_contract.py enforces that
    fetch.py is the only module in the package allowed to call urlopen. This checks the
    delegation directly; fetch.fetch_bytes's own behavior (streaming, the byte cap, no
    redirects) is covered by tests/test_fetch_bytes.py."""
    calls: list[tuple[str, int]] = []

    def _fake_fetch_bytes(url: str, *, max_bytes: int, timeout: float = 15.0) -> bytes:
        calls.append((url, max_bytes))
        return _TINY_PNG

    import fhir_scorecard.fetch as fetch

    monkeypatch.setattr(fetch, "fetch_bytes", _fake_fetch_bytes)
    result = bundle._default_fetch("https://cdn.example.test/logo.png")
    assert result == _TINY_PNG
    assert calls == [("https://cdn.example.test/logo.png", bundle.MAX_LOGO_BYTES)]


def test_delivery_email_states_the_promised_by_date_when_given() -> None:
    request = bundle.parse_request(_base_request(endpoint_ids="live-payer"))
    manifest = {
        "requested": 1,
        "included": 1,
        "endpoints": [{"id": "live-payer", "status": bundle.STATUS_INCLUDED, "detail": ""}],
    }
    email = bundle.delivery_email(
        request, manifest, "https://x.test/d", "2026-10-15", promised_by="Tuesday 16 September"
    )
    assert "promised by Tuesday 16 September" in email.body
