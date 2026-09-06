"""``reverify``: re-check registry entries without ever refreshing a date nobody earned.

Most of this file is about the two ways the verb could quietly lie.

The first is the one the ``reverified`` block exists to prevent: an endpoint that did not
answer coming out of a re-check wearing today's date. Several tests below assert not that the
right date appears but that no date appears, and that ``--apply`` cannot be talked into writing
one.

The second is subtler and is about vocabulary. ``CONTRIBUTING.md`` says a vendor-hosted
multi-tenant platform usually describes the platform rather than the tenant, so a document that
does not repeat a plan's name is the ordinary case, not evidence against the entry. A verb that
called that a "mismatch" would put a false accusation in front of a person skimming eighty
rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import good_capability

from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.registry import Endpoint, load_registry
from fhir_scorecard.reverify import (
    MATCH,
    NOT_OBSERVED,
    UNCONFIRMED,
    ApplyError,
    Observed,
    accepted_blocks,
    apply_to_registry,
    build_proposal,
    format_report,
    load_proposal,
    names_the_entry,
    reverify_one,
    select,
)

TODAY = "2026-09-06"
REPO = Path(__file__).resolve().parent.parent


def _entry(
    endpoint_id: str = "example-payer",
    name: str = "Example Payer",
    *,
    verified: str = "2026-01-01",
    reverified: str = "",
    enabled: bool = True,
) -> Endpoint:
    return Endpoint(
        endpoint_id=endpoint_id,
        name=name,
        kind="payer",
        base_url="https://fhir.example.test/r4",
        verified_method="live CapabilityStatement fetch",
        verified_date=verified,
        reverified_date=reverified,
        reverified_method="live CapabilityStatement fetch" if reverified else "",
        enabled=enabled,
    )


def _serves(monkeypatch, document: dict | None, *, ok: bool = True, error: str = "") -> None:
    body = b"" if document is None else json.dumps(document).encode("utf-8")
    monkeypatch.setattr(
        "fhir_scorecard.reverify.fetch_json",
        lambda url, **kw: FetchResult(
            url=url,
            ok=ok,
            status=200 if ok else 404,
            elapsed_ms=1,
            body=body,
            error=error,
        ),
    )


def _registry_file(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"policy": "test", "endpoints": list(entries)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _entry_json(endpoint_id: str = "example-payer", name: str = "Example Payer") -> dict:
    return {
        "id": endpoint_id,
        "name": name,
        "kind": "payer",
        "base_url": "https://fhir.example.test/r4",
        "verification": {"method": "live CapabilityStatement fetch", "date": "2026-01-01"},
    }


# --- the document was not observed ----------------------------------------


def test_an_unreachable_entry_proposes_nothing(monkeypatch) -> None:
    _serves(monkeypatch, None, ok=False, error="HTTP 404")
    row = reverify_one(_entry(), today=TODAY)
    assert row.outcome == NOT_OBSERVED
    assert row.proposed is None
    assert TODAY not in json.dumps(row.to_payload())


def test_a_document_that_is_not_a_capability_statement_proposes_nothing(monkeypatch) -> None:
    _serves(monkeypatch, {"resourceType": "OperationOutcome", "issue": []})
    row = reverify_one(_entry(), today=TODAY)
    assert row.outcome == NOT_OBSERVED
    assert row.proposed is None
    assert "not a CapabilityStatement" in row.detail


def test_an_unparseable_body_proposes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        "fhir_scorecard.reverify.fetch_json",
        lambda url, **kw: FetchResult(
            url=url, ok=True, status=200, elapsed_ms=1, body=b"<html>nope", error=""
        ),
    )
    row = reverify_one(_entry(), today=TODAY)
    assert row.outcome == NOT_OBSERVED
    assert row.proposed is None


def test_a_not_observed_row_cannot_be_applied_even_when_accepted(tmp_path: Path) -> None:
    """The one thing this verb must never do, refused loudly rather than silently skipped."""
    proposal = {
        "schema": "fhir-scorecard/reverify-proposal/v1",
        "rows": [
            {
                "endpoint_id": "example-payer",
                "outcome": NOT_OBSERVED,
                "proposed": None,
                "accepted": True,
            }
        ],
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    with pytest.raises(ApplyError, match="observed no document"):
        accepted_blocks(load_proposal(path))


def test_apply_leaves_the_registry_byte_identical_when_nothing_is_accepted(
    tmp_path: Path,
) -> None:
    """Byte-identical, and deliberately tested against a file this writer would reformat.

    The shipped ``data/registry.json`` happens to round-trip through
    ``json.dumps(..., indent=2)`` unchanged, so a test using that formatting would pass even
    if the no-accept path re-serialized the whole file: the sabotage would be invisible.
    Measured, on removing the early return: with a two-space file, zero tests failed. This
    one writes four-space indentation, so anything that rewrites the file is visible.
    """
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"policy": "test", "endpoints": [_entry_json()]}, indent=4) + "\n",
        encoding="utf-8",
    )
    before = registry.read_bytes()
    assert apply_to_registry(registry, {}) == 0
    assert registry.read_bytes() == before


def test_the_cli_leaves_the_registry_untouched_when_nothing_is_accepted(
    tmp_path: Path, capsys
) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    before = registry.read_bytes()
    proposal = tmp_path / "p.json"
    proposal.write_text(
        json.dumps(
            {
                "schema": "fhir-scorecard/reverify-proposal/v1",
                "rows": [
                    {
                        "endpoint_id": "example-payer",
                        "outcome": MATCH,
                        "proposed": {"date": TODAY, "method": "m"},
                        "accepted": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["reverify", "--registry", str(registry), "--apply", str(proposal)]) == 0
    assert registry.read_bytes() == before
    assert "unchanged" in capsys.readouterr().out


# --- the document was observed --------------------------------------------


def test_a_document_carrying_the_registry_name_is_a_match(monkeypatch) -> None:
    document = good_capability() | {"publisher": "Example Payer, Inc."}
    _serves(monkeypatch, document)
    row = reverify_one(_entry(), today=TODAY)
    assert row.outcome == MATCH
    assert row.proposed is not None
    assert row.proposed["date"] == TODAY
    assert "publisher" in row.proposed["method"]


def test_a_vendor_platform_document_is_unconfirmed_and_never_called_a_mismatch(
    monkeypatch,
) -> None:
    """The ordinary shape for a multi-tenant platform, and not an accusation."""
    _serves(monkeypatch, good_capability())
    row = reverify_one(_entry(name="Example Payer"), today=TODAY)
    assert row.outcome == UNCONFIRMED
    payload = json.dumps(row.to_payload())
    assert "mismatch" not in payload.lower()
    assert "a person must confirm" in row.detail


def test_an_unconfirmed_row_still_records_what_was_seen(monkeypatch) -> None:
    _serves(monkeypatch, good_capability())
    row = reverify_one(_entry(), today=TODAY)
    assert row.observed.software_name == "SyntheticServer"
    assert row.observed.implementation_description == "Synthetic test fixture"
    assert row.proposed is not None
    assert "SyntheticServer" in row.proposed["method"]


def test_a_remote_string_is_bounded_and_stripped(monkeypatch) -> None:
    """A free-text field on a server this project does not trust, ending up in Markdown."""
    hostile = "Example Payer\n```\n# injected heading `x`" + "z" * 400
    _serves(monkeypatch, good_capability() | {"publisher": hostile})
    row = reverify_one(_entry(), today=TODAY)
    assert "\n" not in row.observed.publisher
    assert "`" not in row.observed.publisher
    assert len(row.observed.publisher) <= 124


# --- what counts as the name ----------------------------------------------


def test_the_match_needs_every_significant_word() -> None:
    assert names_the_entry("Example Payer", Observed(publisher="Example Payer Inc")) == "publisher"
    assert names_the_entry("Example Payer", Observed(publisher="Example Provider")) == ""


def test_two_unrelated_plans_do_not_match_on_shared_industry_words() -> None:
    """`Health`, `Plan` and `Care` are in half the names in the registry."""
    assert names_the_entry("Acme Health Plan", Observed(title="Zenith Health Plan")) == ""


def test_a_name_made_only_of_industry_words_matches_nothing() -> None:
    assert names_the_entry("Health Plan", Observed(publisher="Health Plan")) == ""


def test_a_name_of_only_industry_words_does_not_match_a_larger_name_either() -> None:
    """Without the stopword filter this is a match, and a wrong one."""
    assert (
        names_the_entry("Health Systems Inc", Observed(publisher="Global Health Systems Inc")) == ""
    )


def test_the_base_url_is_never_evidence(monkeypatch) -> None:
    """CONTRIBUTING.md: never attribute on a URL path segment."""
    _serves(monkeypatch, good_capability())
    entry = Endpoint(
        endpoint_id="lacare",
        name="Zenith Mutual",
        kind="payer",
        base_url="https://vendor.test/zenith/mutual/fhir/R4",
        verified_method="m",
        verified_date="2026-01-01",
    )
    assert reverify_one(entry, today=TODAY).outcome == UNCONFIRMED


# --- selection -------------------------------------------------------------


def test_an_entry_nobody_ever_rechecked_is_selected_by_its_curation_date() -> None:
    never = _entry("never", verified="2026-01-01")
    recent = _entry("recent", verified="2026-01-01", reverified="2026-09-01")
    chosen = select([never, recent], older_than="90d", today=TODAY)
    assert [entry.endpoint_id for entry in chosen] == ["never"]


def test_a_disabled_entry_is_never_re_checked() -> None:
    assert select([_entry("off", enabled=False)]) == []


def test_selecting_one_endpoint_that_is_not_there_is_an_error() -> None:
    with pytest.raises(ValueError, match="no enabled registry entry"):
        select([_entry()], endpoint_id="absent")


def test_a_malformed_older_than_is_refused() -> None:
    with pytest.raises(ValueError, match="number of days"):
        select([_entry()], older_than="three months")


def test_older_than_accepts_days_with_or_without_the_suffix() -> None:
    entries = [_entry("old", verified="2026-01-01")]
    assert select(entries, older_than="90d", today=TODAY)
    assert select(entries, older_than="90", today=TODAY)


# --- applying --------------------------------------------------------------


def test_an_accepted_row_writes_the_block_and_leaves_the_curation_date(tmp_path: Path) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    blocks = {"example-payer": {"date": TODAY, "method": "reverify: live fetch"}}
    assert apply_to_registry(registry, blocks) == 1
    raw = json.loads(registry.read_text(encoding="utf-8"))
    verification = raw["endpoints"][0]["verification"]
    assert verification["date"] == "2026-01-01"
    assert verification["reverified"] == blocks["example-payer"]
    loaded = load_registry(registry)
    assert loaded[0].reverified_date == TODAY
    assert loaded[0].verified_date == "2026-01-01"


def test_an_accepted_row_naming_an_absent_endpoint_is_refused(tmp_path: Path) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    with pytest.raises(ApplyError, match="not in the registry"):
        apply_to_registry(registry, {"ghost": {"date": TODAY, "method": "m"}})


def test_a_file_that_is_not_a_proposal_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"schema": "something-else", "rows": []}), encoding="utf-8")
    with pytest.raises(ApplyError, match="is not a"):
        load_proposal(path)


def test_a_proposal_without_rows_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"schema": "fhir-scorecard/reverify-proposal/v1"}), encoding="utf-8")
    with pytest.raises(ApplyError, match="must be a list"):
        load_proposal(path)


# --- the whole verb --------------------------------------------------------


def test_the_verb_writes_a_proposal_and_touches_no_registry_byte(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    before = registry.read_bytes()
    _serves(monkeypatch, good_capability() | {"publisher": "Example Payer"})
    out = tmp_path / "proposal.json"
    code = main(
        [
            "reverify",
            "--registry",
            str(registry),
            "--out",
            str(out),
            "--today",
            TODAY,
        ]
    )
    assert code == 0
    assert registry.read_bytes() == before
    proposal = json.loads(out.read_text(encoding="utf-8"))
    assert proposal["schema"] == "fhir-scorecard/reverify-proposal/v1"
    assert proposal["counts"] == {MATCH: 1, UNCONFIRMED: 0, NOT_OBSERVED: 0}
    assert proposal["rows"][0]["accepted"] is False
    printed = capsys.readouterr().out
    assert "nothing was written to" in printed


def test_the_round_trip_re_checks_then_applies(tmp_path: Path, monkeypatch) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    _serves(monkeypatch, good_capability() | {"publisher": "Example Payer"})
    out = tmp_path / "proposal.json"
    main(["reverify", "--registry", str(registry), "--out", str(out), "--today", TODAY])
    proposal = json.loads(out.read_text(encoding="utf-8"))
    proposal["rows"][0]["accepted"] = True
    out.write_text(json.dumps(proposal), encoding="utf-8")
    assert main(["reverify", "--registry", str(registry), "--apply", str(out)]) == 0
    assert load_registry(registry)[0].reverified_date == TODAY


def test_an_unreadable_registry_exits_two(tmp_path: Path, capsys) -> None:
    assert main(["reverify", "--registry", str(tmp_path / "absent.json")]) == 2
    assert "registry error" in capsys.readouterr().err


def test_an_unreadable_proposal_exits_two(tmp_path: Path, capsys) -> None:
    registry = _registry_file(tmp_path, _entry_json())
    assert (
        main(["reverify", "--registry", str(registry), "--apply", str(tmp_path / "no.json")]) == 2
    )
    assert "reverify error" in capsys.readouterr().err


def test_the_report_says_nothing_was_written(monkeypatch) -> None:
    _serves(monkeypatch, good_capability())
    rows = [reverify_one(_entry(), today=TODAY)]
    report = format_report(rows)
    assert "Nothing here has been written to the registry" in report
    assert "[unconfirmed] example-payer" in report


def test_the_proposal_explains_that_unconfirmed_is_not_an_accusation(monkeypatch) -> None:
    _serves(monkeypatch, good_capability())
    rows = [reverify_one(_entry(), today=TODAY)]
    proposal = build_proposal(rows, today=TODAY, registry_path=Path("data/registry.json"))
    how = proposal["how_to_use"]
    assert isinstance(how, str)
    assert "not an accusation" in how
    assert "can never be applied" in how


# --- against the shipped registry -----------------------------------------


def test_the_shipped_registry_selects_and_none_of_it_is_re_checked_here() -> None:
    """Selection is offline. Nothing in this test reaches a network or writes a file."""
    entries = load_registry(REPO / "data" / "registry.json")
    assert entries
    everything = select(entries)
    assert everything
    assert all(entry.enabled for entry in everything)
    stale = select(entries, older_than="1d", today="2099-01-01")
    assert len(stale) == len(everything)


# --- malformed inputs ------------------------------------------------------
#
# A proposal file and a registry file are both files a person edits by hand between two runs of
# this verb, so every reader here refuses rather than coerces.


def test_selecting_one_endpoint_that_is_there_returns_only_it() -> None:
    chosen = select([_entry("a"), _entry("b")], endpoint_id="b")
    assert [entry.endpoint_id for entry in chosen] == ["b"]


def test_a_proposal_whose_rows_became_a_mapping_is_refused() -> None:
    with pytest.raises(ApplyError, match="must be a list"):
        accepted_blocks({"rows": {"example-payer": True}})


def test_a_registry_whose_endpoints_are_not_a_list_is_refused(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"endpoints": {}}), encoding="utf-8")
    with pytest.raises(ApplyError, match="must be a list"):
        apply_to_registry(registry, {"example-payer": {"date": TODAY, "method": "m"}})


def test_an_entry_with_no_verification_record_cannot_be_re_checked(tmp_path: Path) -> None:
    """There is no re-check of an entry that was never verified in the first place."""
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"endpoints": [{"id": "example-payer", "name": "Example Payer"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ApplyError, match="no verification record"):
        apply_to_registry(registry, {"example-payer": {"date": TODAY, "method": "m"}})
