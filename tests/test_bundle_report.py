"""Tests for bundle_report.py: the self-contained, brandable single-endpoint report.

The central property this module exists for is that the paid report is the *same* content the
free ``/endpoint/<id>/report/`` page publishes, reconstructed from the published dataset rather
than recomputed. Most tests here therefore build a scorecard as ``dataclasses.asdict`` would
publish it (the ``site/scorecards.json`` shape), round-trip it through
``scorecard_from_dict``, and check what ``render_report`` does with it -- including a negative
control proving the rendered text actually tracks the input rather than being fixed boilerplate.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt

import pytest

from fhir_scorecard import bundle_report
from fhir_scorecard.grading import NOT_OBSERVED, DimensionScore, Finding, Scorecard

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _finding(code: str, ok: bool, message: str = "", **kwargs: object) -> Finding:
    return Finding(
        code=code,
        ok=ok,
        points=40 if ok else 0,
        max_points=40,
        message=message or f"{code} message",
        citation="https://hl7.org/fhir/R4/http.html",
        **kwargs,
    )


def _scorecard(**overrides: object) -> Scorecard:
    base = Scorecard(
        endpoint_id="acme-payer",
        name="Acme Payer Patient Access",
        grade="B",
        reachable=True,
        dimensions=(
            DimensionScore(
                key="reachability",
                title="Reachability",
                score=100,
                findings=(_finding("R1", True), _finding("R2", True)),
            ),
            DimensionScore(
                key="interop",
                title="Interop readiness",
                score=40,
                findings=(_finding("I1", True), _finding("I3", False, "no OAuth declared")),
            ),
        ),
        kind="payer",
        availability="answered 1 of 1 checks so far",
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def _as_published(card: Scorecard) -> dict:
    """The exact shape ``fhir_scorecard.report.to_json`` publishes one scorecard as."""
    return dataclasses.asdict(card)


def test_scorecard_from_dict_round_trips_every_field() -> None:
    card = _scorecard(
        observed_since="2026-08-01",
        drift_events=("upgraded to fhirVersion 4.0.1",),
        drift_alternations=("returned to an earlier declaration",),
        last_answered="2026-09-10",
        failure_kinds=(),
    )
    restored = bundle_report.scorecard_from_dict(_as_published(card))
    assert restored == card


def test_scorecard_from_dict_reconstructs_vantage_reports() -> None:
    from fhir_scorecard.vantage import VantageReport

    card = _scorecard(
        vantage_reports=(
            VantageReport(vantage="ubuntu", network="github", reachable=True, elapsed_ms=120),
            VantageReport(
                vantage="residential", network="home", reachable=False, error="timed out"
            ),
        )
    )
    restored = bundle_report.scorecard_from_dict(_as_published(card))
    assert restored.vantage_reports == card.vantage_reports


def test_scorecard_from_dict_tolerates_missing_optional_fields() -> None:
    """A minimal dict (only the fields Scorecard requires) must not raise a KeyError.

    Guards against a future field this module forgot to default -- the fixture below omits every
    field that has a dataclass default, the same shape an older published dataset would have.
    """
    minimal = {
        "endpoint_id": "x",
        "name": "X",
        "grade": "A",
        "reachable": True,
        "dimensions": [
            {
                "key": "reachability",
                "title": "Reachability",
                "score": 100,
                "findings": [
                    {
                        "code": "R1",
                        "ok": True,
                        "points": 60,
                        "max_points": 60,
                        "message": "ok",
                        "citation": "https://hl7.org/fhir/R4/http.html",
                    }
                ],
            }
        ],
    }
    restored = bundle_report.scorecard_from_dict(minimal)
    assert restored.endpoint_id == "x"
    assert restored.vantage_reports == ()
    assert restored.last_answered is None


def test_render_report_includes_brand_cover_and_name() -> None:
    card = _scorecard()
    brand = bundle_report.Brand(name="Acme Compliance Partners", accent="#123456")
    html = bundle_report.render_report(card, brand=brand, base_url="https://x.test/fhir")
    assert "Prepared by Acme Compliance Partners" in html
    assert "#123456" in html


def test_render_report_with_no_brand_omits_prepared_by() -> None:
    html = bundle_report.render_report(_scorecard())
    assert "Prepared by" not in html
    assert "Produced by FHIR Scorecard" in html


def test_render_report_embeds_logo_as_data_uri() -> None:
    encoded = base64.b64encode(_TINY_PNG).decode()
    brand = bundle_report.Brand(name="Acme", logo_data_uri=f"data:image/png;base64,{encoded}")
    html = bundle_report.render_report(_scorecard(), brand=brand)
    assert f"data:image/png;base64,{encoded}" in html
    assert "<img" in html


def test_render_report_never_invents_a_letter_for_not_observed() -> None:
    """A not-observed endpoint must read as not-observed, never as a fabricated grade -- the
    same rule grading.py's own module docstring states for the free path."""
    card = _scorecard(grade=NOT_OBSERVED, reachable=False, dimensions=())
    html = bundle_report.render_report(card)
    assert NOT_OBSERVED in html
    # None of the five real letter grades appear as the endpoint's own grade badge.
    assert '"grade grade-a"' not in html
    assert '"grade grade-b"' not in html


def test_render_report_has_no_leftover_root_relative_links() -> None:
    """Every internal href/src must be absolutized: this file is meant to open with no site
    behind it, and a root-relative link in a local file resolves against the local filesystem."""
    html = bundle_report.render_report(_scorecard(), origin="https://fhir.chelseakr.com")
    assert 'href="/' not in html
    assert 'src="/' not in html
    assert "https://fhir.chelseakr.com/how-we-grade/" in html


def test_render_report_is_grounded_in_the_input_not_hardcoded() -> None:
    """Negative control: change what the source scorecard says, and the rendered document must
    say the different thing. Proves this module renders its argument rather than a fixed
    template that happens to look plausible."""
    passing = _scorecard(
        dimensions=(
            DimensionScore(
                key="interop",
                title="Interop readiness",
                score=100,
                findings=(_finding("I1", True, "US Core profiles declared"),),
            ),
        )
    )
    failing = dataclasses.replace(
        passing,
        dimensions=(
            DimensionScore(
                key="interop",
                title="Interop readiness",
                score=0,
                findings=(_finding("I1", False, "no profile canonical declared anywhere"),),
            ),
        ),
    )
    html_pass = bundle_report.render_report(passing)
    html_fail = bundle_report.render_report(failing)
    assert "US Core profiles declared" in html_pass
    assert "US Core profiles declared" not in html_fail
    assert "no profile canonical declared anywhere" in html_fail
    assert "no profile canonical declared anywhere" not in html_pass


def test_render_report_generated_at_is_stated() -> None:
    when = dt.datetime(2026, 9, 15, 12, 30, tzinfo=dt.UTC)
    html = bundle_report.render_report(_scorecard(), generated_at=when)
    assert "2026-09-15 12:30 UTC" in html


def test_validate_accent_accepts_hex_and_rejects_junk() -> None:
    assert bundle_report._validate_accent("#ABCDEF") == "#abcdef"
    with pytest.raises(bundle_report.ReportError):
        bundle_report._validate_accent("blue")
    with pytest.raises(bundle_report.ReportError):
        bundle_report._validate_accent("#12345")


def test_logo_data_uri_from_file(tmp_path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(_TINY_PNG)
    uri = bundle_report._logo_data_uri(logo)
    assert uri.startswith("data:image/png;base64,")


def test_logo_data_uri_rejects_unreadable_file(tmp_path) -> None:
    # A directory has a .png-looking name but cannot be read as a file.
    logo = tmp_path / "logo.png"
    logo.mkdir()
    with pytest.raises(bundle_report.ReportError, match="not readable"):
        bundle_report._logo_data_uri(logo)


def test_logo_data_uri_rejects_unsupported_extension(tmp_path) -> None:
    logo = tmp_path / "logo.gif"
    logo.write_bytes(b"GIF89a")
    with pytest.raises(bundle_report.ReportError):
        bundle_report._logo_data_uri(logo)
