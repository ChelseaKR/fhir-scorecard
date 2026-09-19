"""The fulfillment workflow's commands over one stored order (``fhir_scorecard.bundle_order``).

The workflow runs in a public repository, so the property that matters most is the order of
operations in ``mask``: every buyer value is registered as a mask *before* the order is
validated, so an error that quotes one is already blank in the log. The rest hold the archive to
the cap that was paid for and the email to the configured API and sender.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard import bundle_order
from fhir_scorecard.bundle import MAX_ENDPOINTS

BUNDLE_ID = "0f" * 16
EMAIL = "reports@example-compliance.test"
ORG = "Example Compliance Partners"
API = "https://abc123.execute-api.us-west-2.amazonaws.com"


def _order(**overrides: Any) -> dict[str, Any]:
    order: dict[str, Any] = {
        "bundle_id": BUNDLE_ID,
        "program_name": ORG,
        "accent": "#162e51",
        "logo": "",
        "endpoint_ids": ["cms-blue-button-2", "humana-patient-access"],
        "deliver_to": EMAIL,
        "cadence": "one_time",
        "promised_by": "Tuesday, September 22, 2026",
        "max_endpoints": 15,
    }
    order.update(overrides)
    return order


@pytest.fixture
def stored(tmp_path: Path) -> Any:
    def _write(order: Any) -> Path:
        path = tmp_path / "request.json"
        path.write_text(json.dumps(order), encoding="utf-8")
        return path

    return _write


def test_mask_lines_cover_every_value_that_names_the_buyer_or_grants_access() -> None:
    lines = bundle_order.mask_lines(_order(logo="https://example-compliance.test/logo.svg"))
    masked = {line.removeprefix("::add-mask::") for line in lines}
    assert masked == {
        BUNDLE_ID,
        EMAIL,
        ORG,
        "https://example-compliance.test/logo.svg",
        "cms-blue-button-2",
        "humana-patient-access",
    }
    assert all(line.startswith("::add-mask::") for line in lines)
    # Longest first, so a value that contains another is masked whole.
    lengths = [len(line) for line in lines]
    assert lengths == sorted(lengths, reverse=True)


def test_mask_lines_leave_out_what_says_nothing_about_the_buyer() -> None:
    masked = "\n".join(bundle_order.mask_lines(_order(logo="data:image/png;base64,AAAA")))
    for value in ("#162e51", "one_time", "Tuesday", "data:image/png"):
        assert value not in masked


def test_mask_lines_read_a_comma_separated_list_and_skip_blank_or_multiline_values() -> None:
    lines = bundle_order.mask_lines(
        {"endpoint_ids": "one-payer,\ntwo-payer, ", "program_name": "a\nb", "deliver_to": 7}
    )
    assert lines == ["::add-mask::one-payer", "::add-mask::two-payer"]


def test_the_mask_command_masks_before_it_validates(
    stored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control on the ordering: an order that fails validation still has every value
    masked (the masks are flushed before validation starts), and then fails with the error."""
    path = stored(_order(deliver_to="not-an-email", endpoint_ids="Bad Id!"))
    assert bundle_order.main(["mask", str(path)]) == 2
    out = capsys.readouterr()
    assert "::add-mask::not-an-email" in out.out
    assert "::add-mask::Bad Id!" in out.out
    assert out.err.startswith("order error:")


def test_the_mask_command_accepts_a_well_formed_order(
    stored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bundle_order.main(["mask", str(stored(_order()))]) == 0
    assert f"::add-mask::{EMAIL}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("cap", "expected"),
    [
        (15, 15),
        (70, 70),
        (500, MAX_ENDPOINTS),
        (0, MAX_ENDPOINTS),
        (True, MAX_ENDPOINTS),
        ("15", MAX_ENDPOINTS),
        (None, MAX_ENDPOINTS),
    ],
)
def test_the_cap_is_the_one_sold_and_never_wider_than_the_widest_plan(
    cap: Any, expected: int
) -> None:
    assert bundle_order.order_cap({"max_endpoints": cap}) == expected


def test_the_cap_command_prints_the_cap_and_holds_the_order_to_it(
    stored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bundle_order.main(["cap", str(stored(_order()))]) == 0
    assert capsys.readouterr().out.strip() == "15"
    over = _order(max_endpoints=1)
    assert bundle_order.main(["cap", str(stored(over))]) == 2
    assert "at most 1 endpoints" in capsys.readouterr().err


def test_the_archive_key_command_prints_where_the_archive_goes(
    stored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bundle_order.main(["archive-key", str(stored(_order()))]) == 0
    assert capsys.readouterr().out.strip() == f"compliance-bundles/{BUNDLE_ID}/bundle.zip"


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "included": 1,
                "requested": 2,
                "endpoints": [
                    {"id": "cms-blue-button-2", "status": "included", "detail": ""},
                    {
                        "id": "humana-patient-access",
                        "status": "unknown_id",
                        "detail": "not tracked",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_email_command_sends_the_link_under_the_configured_api(
    stored: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setenv("BUNDLE_API_BASE", API + "/")
    monkeypatch.setenv("SES_FROM", "reports@chelseakr.com")
    code = bundle_order.main(
        ["email", str(stored(_order())), str(_manifest(tmp_path))],
        send=lambda *args: sent.append(args),
    )
    assert code == 0
    [(source, to, subject, body)] = sent
    assert source == "reports@chelseakr.com"
    assert to == EMAIL
    assert "1 of 2 ready" in subject
    assert f"{API}/download/{BUNDLE_ID}" in body
    assert "promised by Tuesday, September 22, 2026" in body
    assert "humana-patient-access: not tracked" in body


def test_the_download_link_expires_thirty_days_after_the_email(stored: Any, tmp_path: Path) -> None:
    sent: list[tuple[str, ...]] = []
    bundle_order.send_delivery_email(
        stored(_order()),
        _manifest(tmp_path),
        api_base=API,
        source="reports@chelseakr.com",
        send=lambda *args: sent.append(args),
        now=dt.datetime(2026, 9, 18, tzinfo=dt.UTC),
    )
    assert "valid until 2026-10-18" in sent[0][3]


@pytest.mark.parametrize(
    ("api", "source", "error"),
    [
        ("http://abc.example", "reports@chelseakr.com", "https URL"),
        ("", "reports@chelseakr.com", "https URL"),
        (API, "", "SES_FROM"),
        (API, "not an address", "SES_FROM"),
    ],
)
def test_the_email_command_refuses_a_link_or_sender_it_cannot_stand_behind(
    stored: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: str,
    source: str,
    error: str,
) -> None:
    monkeypatch.setenv("BUNDLE_API_BASE", api)
    monkeypatch.setenv("SES_FROM", source)
    code = bundle_order.main(
        ["email", str(stored(_order())), str(_manifest(tmp_path))],
        send=lambda *args: pytest.fail("an email was sent"),
    )
    assert code == 2
    assert error in capsys.readouterr().err


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"a string"'])
def test_an_unreadable_order_fails_without_quoting_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    path = tmp_path / "request.json"
    path.write_text(content, encoding="utf-8")
    assert bundle_order.main(["archive-key", str(path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("order error:")
    assert content not in err


def test_a_missing_order_file_is_an_order_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bundle_order.main(["cap", str(tmp_path / "absent.json")]) == 2
    assert "could not be read" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["mask"], ["email", "only-one"], ["unknown", "x"]])
def test_anything_else_prints_usage(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert bundle_order.main(argv) == 2
    assert "usage:" in capsys.readouterr().err


def test_main_reads_sys_argv_when_given_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["bundle_order"])
    assert bundle_order.main() == 2
    assert "usage:" in capsys.readouterr().err
