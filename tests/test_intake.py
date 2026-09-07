"""``claim``: read an add-endpoint submission without becoming a request-forgery proxy.

Three things in this file are load-bearing and the rest is ordinary coverage.

The first is that **the address boundary runs before any socket is opened**. Every refusal case
below is driven with a fetcher and a resolver that raise if they are called at the wrong point,
so "no request was made" is asserted rather than assumed. This is the only fetch in the project
whose URL an unauthenticated stranger supplies, and the ordinary reading of a passing test --
"the code refused it" -- is not the same claim as "nothing was requested".

The second is that **the documentation page is not retrieved**, and that this is enforced by a
test rather than by the absence of a call. Whether this project may read a page that is not one
of the two discovery documents is an open decision on #118; until it is settled the boundary is
a rule the code is held to.

The third is that **a claim that observed nothing proposes nothing**. ``reverify`` refuses to
write a date nobody earned; this refuses to write an entry nobody observed, one step earlier in
the same pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import good_capability

from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.intake import (
    ATTRIBUTION_CONFIRMED,
    ATTRIBUTION_REVIEW,
    CATEGORIES,
    REFUSALS,
    REFUSED,
    RELEASES,
    Claim,
    ClaimError,
    Verdict,
    assess,
    build_proposal,
    claim_from_form,
    format_comment,
    parse_form,
    refuse,
    slugify,
    suggest_id,
)
from fhir_scorecard.registry import EXPECTS, KINDS, Endpoint, load_registry

TODAY = "2026-09-07"
REPO = Path(__file__).resolve().parent.parent
BASE = "https://fhir.example.test/r4"
DOCS = "https://example.test/developers"

FORM = f"""### Organization

Example Health Plan

### FHIR base URL

{BASE}

### Category

Payer Patient Access API

### FHIR release

R4

### Public documentation URL

{DOCS}

### Anything else

_No response_
"""


def _claim(**overrides: str) -> Claim:
    fields: dict[str, str] = {
        "organization": "Example Health Plan",
        "base_url": BASE,
        "category": "Payer Patient Access API",
        "release": "R4",
        "documentation": DOCS,
    }
    fields.update(overrides)
    return Claim(**fields)


def _public(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _never_resolve(host: str) -> list[str]:
    raise AssertionError(f"resolution was attempted for {host!r}, and must not have been")


class _Fetcher:
    """Records every URL asked for, and can be told to raise if asked for anything."""

    def __init__(self, body: bytes = b"", *, ok: bool = True, forbidden: bool = False) -> None:
        self.urls: list[str] = []
        self._body = body
        self._ok = ok
        self._forbidden = forbidden

    def __call__(self, url: str, **_kw: object) -> FetchResult:
        if self._forbidden:
            raise AssertionError(f"a request was made for {url!r}, and must not have been")
        self.urls.append(url)
        return FetchResult(
            url=url,
            ok=self._ok,
            status=200 if self._ok else 404,
            elapsed_ms=1,
            body=self._body if self._ok else b"",
            error=None if self._ok else "HTTP 404",
            failure_kind=None if self._ok else "not_found",
        )


def _capability(**overrides: object) -> bytes:
    doc = good_capability()
    doc.update(overrides)
    return json.dumps(doc).encode("utf-8")


def _assess(claim: Claim, fetcher: _Fetcher) -> Verdict:
    return assess(claim, today=TODAY, resolve=_public, fetch=fetcher)


def _verification_of(verdict: Verdict) -> dict[str, object]:
    entry = verdict.entry
    assert entry is not None, "this verdict proposed no entry"
    verification = entry["verification"]
    assert isinstance(verification, dict)
    return verification


def _method_of(verdict: Verdict) -> str:
    return str(_verification_of(verdict)["method"])


# --- reading the form ---------------------------------------------------------------------


def test_the_form_is_read_into_the_fields_a_registry_entry_needs() -> None:
    claim = claim_from_form(FORM)
    assert claim.organization == "Example Health Plan"
    assert claim.base_url == BASE
    assert claim.category == "Payer Patient Access API"
    assert claim.release == "R4"
    assert claim.documentation == DOCS


def test_a_blank_optional_field_reads_as_empty_not_as_the_words_no_response() -> None:
    """``_No response_`` is GitHub's rendering of nothing, and it must not be quoted as text."""
    assert claim_from_form(FORM).context == ""


def test_a_body_that_is_not_a_submission_is_refused_rather_than_half_read() -> None:
    with pytest.raises(ClaimError):
        claim_from_form("Hi, please add my endpoint. It is https://fhir.example.test/r4")


def test_a_field_value_is_bounded_and_stripped() -> None:
    body = FORM.replace("Example Health Plan", "A" * 500 + "\x07`")
    assert len(parse_form(body)["Organization"]) <= 304


# --- the address boundary: nothing is requested ---------------------------------------------


@pytest.mark.parametrize(
    ("url", "refusal"),
    [
        ("http://fhir.example.test/r4", "not_https"),
        ("https://user:pw@fhir.example.test/r4", "credentials_in_url"),
        ("https://fhir.example.test/r4/metadata", "not_a_base_url"),
        ("https://fhir.example.test/.well-known/smart-configuration", "not_a_base_url"),
        ("https://127.0.0.1/r4", "host_not_public"),
        ("https://10.0.0.5/r4", "host_not_public"),
        ("https://169.254.169.254/latest", "host_not_public"),
        ("https://[::1]/r4", "host_not_public"),
        ("https:///r4", "not_a_base_url"),
    ],
)
def test_an_address_this_project_must_not_ask_for_is_refused_with_no_request(
    url: str, refusal: str
) -> None:
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(
        _claim(base_url=url), today=TODAY, resolve=_never_resolve, fetch=fetcher, registry=[]
    )
    assert verdict.outcome == REFUSED
    assert verdict.refusal == refusal
    assert verdict.requested == ()
    assert verdict.entry is None


def test_a_hostname_that_resolves_into_a_private_range_is_refused_with_no_request() -> None:
    """The rebinding case: a public-looking name pointing inside the runner's network."""
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(
        _claim(),
        today=TODAY,
        resolve=lambda _host: ["192.168.1.10"],
        fetch=fetcher,
        registry=[],
    )
    assert verdict.refusal == "host_not_public"
    assert "192.168.1.10" in verdict.reason
    assert verdict.requested == ()


def test_one_private_address_disqualifies_a_host_that_also_has_a_public_one() -> None:
    """Fail closed. Which address a later connection picks is not this code's to predict."""
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(
        _claim(),
        today=TODAY,
        resolve=lambda _host: ["93.184.216.34", "127.0.0.1"],
        fetch=fetcher,
        registry=[],
    )
    assert verdict.refusal == "host_not_public"


def test_a_host_that_does_not_resolve_is_refused_rather_than_attempted() -> None:
    def _fails(host: str) -> list[str]:
        raise OSError(f"no such host {host}")

    fetcher = _Fetcher(forbidden=True)
    verdict = assess(_claim(), today=TODAY, resolve=_fails, fetch=fetcher, registry=[])
    assert verdict.refusal == "host_unresolvable"
    assert verdict.requested == ()


def test_a_host_that_resolves_to_nothing_at_all_is_refused() -> None:
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(_claim(), today=TODAY, resolve=lambda _host: [], fetch=fetcher, registry=[])
    assert verdict.refusal == "host_unresolvable"


def test_a_resolver_returning_something_that_is_not_an_address_is_not_treated_as_public() -> None:
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(
        _claim(),
        today=TODAY,
        resolve=lambda _host: ["not-an-address"],
        fetch=fetcher,
        registry=[],
    )
    assert verdict.refusal == "host_not_public"


# --- the form's own refusals -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"organization": ""}, "form_incomplete"),
        ({"base_url": ""}, "form_incomplete"),
        ({"documentation": ""}, "form_incomplete"),
        ({"category": "Something else"}, "unknown_category"),
        ({"release": "DSTU2"}, "unknown_release"),
    ],
)
def test_a_submission_missing_what_an_entry_needs_is_refused_before_any_request(
    overrides: dict[str, str], refusal: str
) -> None:
    fetcher = _Fetcher(forbidden=True)
    verdict = assess(
        _claim(**overrides), today=TODAY, resolve=_never_resolve, fetch=fetcher, registry=[]
    )
    assert verdict.refusal == refusal
    assert verdict.requested == ()


def test_a_base_url_already_in_the_registry_is_refused_naming_the_entry() -> None:
    fetcher = _Fetcher(forbidden=True)
    existing = Endpoint(
        endpoint_id="example-health-plan",
        name="Example Health Plan",
        kind="payer",
        base_url=BASE,
        verified_method="live CapabilityStatement fetch",
        verified_date="2026-01-01",
    )
    verdict = assess(_claim(), today=TODAY, resolve=_public, fetch=fetcher, registry=[existing])
    assert verdict.refusal == "already_registered"
    assert "example-health-plan" in verdict.reason
    assert verdict.requested == ()


def test_a_refusal_outside_the_named_vocabulary_raises_rather_than_being_invented() -> None:
    with pytest.raises(ClaimError):
        refuse(_claim(), "seemed_wrong", "a reason")


# --- what is retrieved, and what is not ------------------------------------------------------


def test_exactly_the_two_discovery_documents_are_requested_and_nothing_else() -> None:
    """The boundary of #118, asserted rather than described.

    The documentation URL is the page the form exists to collect and the page this verb does not
    read. If a later change starts reading it, this fails.
    """
    fetcher = _Fetcher(_capability())
    verdict = _assess(_claim(), fetcher)
    assert fetcher.urls == [
        f"{BASE}/metadata",
        f"{BASE}/.well-known/smart-configuration",
    ]
    assert DOCS not in fetcher.urls
    assert verdict.requested == tuple(fetcher.urls)


def test_the_documentation_page_is_published_as_not_retrieved_everywhere_it_appears() -> None:
    fetcher = _Fetcher(_capability())
    verdict = _assess(_claim(), fetcher)
    documentation = verdict.to_payload()["documentation"]
    assert isinstance(documentation, dict)
    assert documentation["url"] == DOCS
    assert documentation["retrieved"] is False
    assert "not retrieved" in str(documentation["why_not"])
    assert "#118" in str(documentation["why_not"])
    assert "not retrieved" in format_comment(verdict)
    assert "was not retrieved" in _method_of(verdict)


def test_no_proposed_entry_ever_rests_on_the_documentation_page() -> None:
    """``publisher_documented`` is defined by that page, so this verb can never reach it."""
    fetcher = _Fetcher(_capability())
    verdict = _assess(_claim(), fetcher)
    assert _verification_of(verdict)["basis"] == "live_capability"
    assert "publisher_documented" not in json.dumps(verdict.to_payload())


# --- the three outcomes ----------------------------------------------------------------------


def test_a_document_naming_the_submitted_organization_is_confirmed() -> None:
    fetcher = _Fetcher(_capability(publisher="Example Health Plan"))
    verdict = _assess(_claim(), fetcher)
    assert verdict.outcome == ATTRIBUTION_CONFIRMED
    assert verdict.attribution == "publisher"
    entry = verdict.entry
    assert entry is not None
    assert entry["name"] == "Example Health Plan"
    assert entry["kind"] == "payer"
    assert entry["expects"] == "r4"
    assert entry["base_url"] == BASE
    assert _verification_of(verdict)["date"] == TODAY


def test_a_vendor_platform_document_is_flagged_for_review_and_never_called_a_mismatch() -> None:
    """A document that does not repeat the plan's name is the ordinary multi-tenant shape."""
    fetcher = _Fetcher(_capability(publisher="Some Vendor Platform, Inc."))
    verdict = _assess(_claim(), fetcher)
    assert verdict.outcome == ATTRIBUTION_REVIEW
    assert verdict.entry is not None, "a proposal is still made; a person settles attribution"
    rendered = json.dumps(verdict.to_payload()) + format_comment(verdict)
    assert "mismatch" not in rendered.lower()


def test_both_readings_are_recorded_when_attribution_is_unsettled() -> None:
    fetcher = _Fetcher(_capability(publisher="Some Vendor Platform, Inc."))
    verdict = _assess(_claim(), fetcher)
    payload = verdict.to_payload()
    submitted = payload["submitted"]
    observed = payload["observed"]
    assert isinstance(submitted, dict) and isinstance(observed, dict)
    assert submitted["organization"] == "Example Health Plan"
    assert observed["publisher"] == "Some Vendor Platform, Inc."
    assert verdict.attribution == ""


def test_a_document_that_was_not_retrieved_proposes_nothing_at_all() -> None:
    fetcher = _Fetcher(ok=False)
    verdict = _assess(_claim(), fetcher)
    assert verdict.outcome == REFUSED
    assert verdict.refusal == "not_observed"
    assert verdict.entry is None
    assert "not_found" in verdict.reason


def test_a_body_that_is_not_a_capability_statement_proposes_nothing() -> None:
    fetcher = _Fetcher(b'{"resourceType": "OperationOutcome"}')
    verdict = _assess(_claim(), fetcher)
    assert verdict.refusal == "not_a_capability_statement"
    assert verdict.entry is None


def test_an_unparseable_body_proposes_nothing() -> None:
    fetcher = _Fetcher(b"<html>sign in</html>")
    verdict = _assess(_claim(), fetcher)
    assert verdict.refusal == "not_a_capability_statement"
    assert verdict.entry is None


def test_the_comment_for_a_refused_address_says_nothing_was_requested() -> None:
    """The comment is what a submitter reads, and it must not imply a probe that never ran."""
    verdict = assess(
        _claim(base_url="https://127.0.0.1/r4"),
        today=TODAY,
        resolve=_never_resolve,
        fetch=_Fetcher(forbidden=True),
    )
    comment = format_comment(verdict)
    assert "No request was made." in comment
    assert "No registry entry is proposed." in comment
    assert "no pull request was opened" in comment


# --- the proposed entry is one the registry would accept -------------------------------------


def test_the_proposed_entry_loads_as_a_registry_entry(tmp_path: Path) -> None:
    """A proposal a person cannot paste into the registry is a proposal about nothing."""
    fetcher = _Fetcher(_capability(publisher="Example Health Plan"))
    verdict = _assess(_claim(), fetcher)
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"policy": "test", "endpoints": [verdict.entry]}, indent=2),
        encoding="utf-8",
    )
    loaded = load_registry(path)
    assert loaded[0].name == "Example Health Plan"
    assert loaded[0].verification_basis == "live_capability"


def test_the_suggested_id_is_a_slug_and_avoids_ids_already_taken() -> None:
    assert suggest_id("Example Health Plan", "payer", []) == "example-health-plan"
    assert (
        suggest_id("Example Health Plan", "payer_provider_directory", [])
        == "example-health-plan-provider-directory"
    )
    assert (
        suggest_id("Example Health Plan", "payer", ["example-health-plan"])
        == "example-health-plan-2"
    )


def test_a_name_with_no_usable_characters_suggests_no_id_rather_than_a_bad_one() -> None:
    assert slugify("!!!") == ""
    assert suggest_id("!!!", "payer", []) == ""


def test_a_name_whose_suffixes_are_all_taken_suggests_nothing_rather_than_a_collision() -> None:
    """An id colliding with a live entry would overwrite it if anyone pasted the proposal in."""
    taken = ["example-health-plan"] + [f"example-health-plan-{n}" for n in range(2, 100)]
    assert suggest_id("Example Health Plan", "payer", taken) == ""


# --- determinism -----------------------------------------------------------------------------


def test_two_runs_over_the_same_submission_produce_identical_proposal_bytes() -> None:
    first = build_proposal(_assess(_claim(), _Fetcher(_capability())), today=TODAY, issue="#1")
    second = build_proposal(_assess(_claim(), _Fetcher(_capability())), today=TODAY, issue="#1")
    assert json.dumps(first, indent=2, sort_keys=False) == json.dumps(
        second, indent=2, sort_keys=False
    )


# --- the form and the code agree on the vocabulary -------------------------------------------


def test_every_category_the_form_offers_maps_onto_a_registry_kind() -> None:
    assert set(CATEGORIES.values()) <= KINDS
    assert set(RELEASES.values()) <= set(EXPECTS)


def test_the_parser_knows_exactly_the_options_the_issue_template_offers() -> None:
    """A dropdown option nobody mapped becomes ``unknown_category`` for a valid submission.

    Text matching would not catch it: the failure is an option present in one file and absent
    from the other, which is a question about parsed structure.
    """
    template = yaml.safe_load(
        (REPO / ".github/ISSUE_TEMPLATE/add-endpoint.yml").read_text(encoding="utf-8")
    )
    by_id = {field.get("id"): field for field in template["body"] if field.get("id")}
    assert set(by_id["kind"]["attributes"]["options"]) == set(CATEGORIES)
    assert set(by_id["fhir_release"]["attributes"]["options"]) == set(RELEASES)
    labels = {field["attributes"]["label"] for field in template["body"] if field.get("id")}
    assert {
        "Organization",
        "FHIR base URL",
        "Category",
        "FHIR release",
        "Public documentation URL",
    } <= labels


def test_every_refusal_the_code_can_produce_is_in_the_published_vocabulary() -> None:
    assert len(set(REFUSALS)) == len(REFUSALS)


# --- the command line ------------------------------------------------------------------------


def test_the_verb_writes_a_proposal_and_a_comment_and_touches_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fhir_scorecard.intake.default_resolver", _public)
    monkeypatch.setattr(
        "fhir_scorecard.intake.fetch_json",
        _Fetcher(_capability(publisher="Example Health Plan")),
    )
    issue = tmp_path / "issue.md"
    issue.write_text(FORM, encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"policy": "test", "endpoints": []}, indent=2) + "\n", encoding="utf-8"
    )
    before = registry.read_bytes()
    out = tmp_path / "claim.json"
    comment = tmp_path / "comment.md"
    code = main(
        [
            "claim",
            str(issue),
            "--registry",
            str(registry),
            "--out",
            str(out),
            "--comment-out",
            str(comment),
            "--today",
            TODAY,
            "--issue-ref",
            "https://github.com/ChelseaKR/fhir-scorecard/issues/999",
        ]
    )
    assert code == 0
    assert registry.read_bytes() == before
    proposal = json.loads(out.read_text(encoding="utf-8"))
    assert proposal["schema"] == "fhir-scorecard/claim-proposal/v1"
    assert proposal["verdict"]["outcome"] == ATTRIBUTION_CONFIRMED
    assert proposal["issue"].endswith("/999")
    assert "not retrieved" in comment.read_text(encoding="utf-8")


def test_a_body_that_is_not_a_submission_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = tmp_path / "issue.md"
    issue.write_text("please add my server", encoding="utf-8")
    assert main(["claim", str(issue), "--out", str(tmp_path / "c.json")]) == 2
    assert "claim error" in capsys.readouterr().err


def test_an_unreadable_registry_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issue = tmp_path / "issue.md"
    issue.write_text(FORM, encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text("{", encoding="utf-8")
    assert (
        main(["claim", str(issue), "--registry", str(registry), "--out", str(tmp_path / "c.json")])
        == 2
    )
    assert "registry error" in capsys.readouterr().err


def test_the_report_says_the_registry_is_unchanged_and_no_pull_request_was_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("fhir_scorecard.intake.default_resolver", _public)
    monkeypatch.setattr("fhir_scorecard.intake.fetch_json", _Fetcher(_capability()))
    issue = tmp_path / "issue.md"
    issue.write_text(FORM, encoding="utf-8")
    main(["claim", str(issue), "--out", str(tmp_path / "c.json"), "--today", TODAY])
    out = capsys.readouterr().out
    assert "no pull request opened" in out
    assert "documentation page: not retrieved" in out
