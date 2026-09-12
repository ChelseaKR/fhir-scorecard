"""An endpoint nobody reached must not publish findings about what its publisher did not publish.

The measured case this pins (2026-08-05, `capital-bluecross`): R1 said the TLS failure was
"likely a vantage-local interception, not an endpoint fault", and the four findings under it said
the CapabilityStatement was unparseable, no interoperability profiles were declared, SMART
discovery was absent, and no OAuth security service was declared. Each carried a spec citation.
The same repository's `data/history.json` recorded 37 declared profiles, 28 resource types and
OAuth declared for that endpoint on that date, so all four were false, and they were false in the
one place where a zero is a reputational claim about a named health insurer.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cli import main
from fhir_scorecard.dataset import to_csv
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import NOT_OBSERVED, build_scorecard, grade_interop
from fhir_scorecard.registry import Endpoint
from fhir_scorecard.site import endpoint_page, status_badge

_CLAIMS_ABOUT_A_DOCUMENT = (
    "CapabilityStatement unparseable",
    "no recognized interoperability profiles declared",
    "SMART .well-known/smart-configuration absent",
    "no OAuth security service declared",
    "resource types declared",
)


def _unreachable() -> FetchResult:
    return FetchResult(
        url="https://payer.test/r4/metadata",
        ok=False,
        status=None,
        elapsed_ms=0,
        body=b"",
        error=(
            "TLS certificate verification failed (self-signed certificate in certificate "
            "chain); likely a vantage-local interception, not an endpoint fault"
        ),
    )


def test_unreachable_endpoint_publishes_no_findings_about_the_document() -> None:
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        _unreachable(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    messages = [f.message for dim in card.dimensions for f in dim.findings]
    for claim in _CLAIMS_ABOUT_A_DOCUMENT:
        assert not any(claim in m for m in messages), claim

    by_key = {dim.key: dim for dim in card.dimensions}
    # No score at all, rather than a zero, which is a measurement.
    assert by_key["transparency"].score is None
    assert by_key["interop"].score is None
    for key in ("transparency", "interop"):
        findings = by_key[key].findings
        assert len(findings) == 1
        assert findings[0].code == "NR"
        assert findings[0].max_points == 0
        assert findings[0].observed is False
        assert "no CapabilityStatement was retrieved" in findings[0].message
    # Reachability publishes no score either (#135). This assertion used to read
    # `score == 0`, under the comment "reachability is still measured and still fails" -- and
    # that is the thing it is not. R1 asks whether /metadata answers 2xx; every vantage failing
    # from one network establishes only that it did not answer *here*, which is what R1's own
    # message says, and `pages.yml` predicts the source-address rule that makes the difference.
    # Re-probed from a residential network on 2026-09-12, two of the 14 endpoints publishing
    # this zero answered 200 and 204.
    #
    # The endpoint still does not vanish: both findings are published, with their messages
    # intact, and the reason is carried in `failure_kinds`. What changes is that a reader is no
    # longer shown a number, or a ✗, where nothing was established.
    assert by_key["reachability"].score is None
    reachability = by_key["reachability"].findings
    assert [f.code for f in reachability] == ["R1", "R2"]
    assert all(f.observed is False for f in reachability)
    assert all(f.max_points == 0 for f in reachability)
    # The full scale is still recoverable, so a consumer can see that all 100 points went
    # unmeasured rather than inferring it from an absent number.
    assert by_key["reachability"].withheld_points == 100
    assert card.grade == NOT_OBSERVED
    assert card.reachable is False


def test_a_reachable_failure_is_not_labelled_unreachable() -> None:
    """`data/rejected.json` records exactly this body for Elevance Health TotalView: a 200 that
    is HTML, not a CapabilityStatement. The endpoint's network is fine; its document is not."""
    answered = FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=250,
        body=b"<html><body>Login</body></html>",
        error=None,
    )
    card = build_scorecard(
        "elevance-shaped",
        "Elevance-shaped Endpoint",
        answered,
        parse_capability(b"<html><body>Login</body></html>"),
        parse_smart(b""),
        kind="payer",
    )
    assert card.reachable is True
    assert card.grade == "F"

    page = endpoint_page(
        card, base_url="https://payer.test/r4", verified="live fetch", origin="https://example.test"
    )
    assert "could not be reached" not in page.body
    assert "falls short across the graded checks" in page.body


def test_the_unreachable_page_says_what_happened_and_claims_nothing_else() -> None:
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        _unreachable(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    page = endpoint_page(
        card,
        base_url="https://payer.test/r4",
        verified="live fetch (recorded 2026-08-05)",
        origin="https://example.test",
    )
    assert "could not be reached from any vantage on this run" in page.body
    assert "Current status" in page.body and "Current grade" not in page.body
    for claim in _CLAIMS_ABOUT_A_DOCUMENT:
        assert claim not in page.body
    # A meter at zero is the same claim drawn instead of written -- and that was as true of the
    # reachability meter as of the other two. No dimension gets a bar or a number when no vantage
    # reached the endpoint (#135).
    assert page.body.count("dimension-meter-unscored") == 6  # three dimensions, rendered twice
    assert "FHIR endpoint not observed" in page.title

    # But the three are not the same fact, and the page says two different things about them.
    # Reachability's subject is the *attempt*, and the attempt is the finding: this run asked and
    # was answered by nobody, which is dated, sourced information about the endpoint. The content
    # dimensions' subject is a *document*, and there is no document, so there is nothing to say
    # about what it declares -- which is the distinction `grading`'s module docstring draws.
    assert "<span>Reachability</span><strong>no answer</strong>" in page.body
    for title in ("Capability transparency", "Interop readiness"):
        assert f"<span>{title}</span><strong>not observed</strong>" in page.body

    # No glyph on this page may read as a verdict against the endpoint. "✗ Needs attention"
    # beside "latency unmeasured: endpoint unreachable" told a named organization to fix a
    # measurement that was never taken, and beside R1's sentence it contradicted the sentence
    # itself, which says the failure is "likely a vantage-local interception, not an endpoint
    # fault".
    assert "Needs attention" not in page.body
    assert page.body.count('class="finding unanswered"') == 2  # R1 and R2: asked, not answered
    assert page.body.count('class="finding unobserved"') == 2  # the two NR findings
    # And the reader is told which of the two states each mark is.
    assert "No answer: " in page.body
    assert "Not observed: " in page.body


def test_reached_but_no_documents_says_that_and_not_that_it_was_unreachable() -> None:
    """A peer vantage reached the endpoint and carried no documents. It is up, and nothing about
    its documents was observed; the page has to say both."""
    from fhir_scorecard.vantage import VantageProbe, reconcile

    consensus = reconcile(
        [
            VantageProbe("local/test", False, 0, "connection timed out"),
            VantageProbe("peer/other", True, 400),
        ]
    )
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        _unreachable(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
    )
    assert card.reachable is True
    assert card.grade == NOT_OBSERVED
    page = endpoint_page(
        card, base_url="https://payer.test/r4", verified="live fetch", origin="https://example.test"
    )
    assert "no vantage retrieved its public documents" in page.body
    assert "could not be reached from any vantage" not in page.body


def test_the_badge_does_not_stamp_an_f_on_an_endpoint_nobody_reached() -> None:
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        _unreachable(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    svg = status_badge(card)
    assert "not observed" in svg
    assert ">F<" not in svg


def test_a_borrowed_capability_without_its_smart_document_claims_nothing_about_smart() -> None:
    """A peer vantage retrieved the CapabilityStatement but not the SMART document. "Absent"
    would be a claim about the endpoint; this run simply never asked from a vantage that could."""
    facts = parse_capability(json.dumps(good_capability()).encode())
    dim = grade_interop(facts, NO_SMART_RETRIEVED, kind="payer")
    i2 = next(f for f in dim.findings if f.code == "I2")
    assert i2.observed is False and i2.max_points == 0
    assert "absent" not in i2.message
    assert "no vantage retrieved .well-known/smart-configuration" in i2.message
    # I1 and I3 were still run, and their findings are in the dimension.
    assert {f.code for f in dim.findings} >= {"I1", "I3"}
    # But the dimension has no score. This assertion used to read `dim.score is not None`, and
    # it was wrong in the direction that matters: `_score` divided the earned points by the
    # points that happened to be on the table, so dropping I2 out of the denominator raised the
    # percentage. The same endpoint scored 40 with an unusable SMART document in hand and 62
    # with none retrieved -- better, for the absence of evidence. The withheld points are
    # recorded instead, and `letter` bounds the grade with them.
    assert dim.score is None
    assert dim.withheld_points == 35


def test_dataset_leaves_an_unobserved_score_empty_never_zero() -> None:
    card = build_scorecard(
        "payer",
        "Payer Health Plan",
        _unreachable(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
    )
    endpoint = Endpoint(
        endpoint_id="payer",
        name="Payer Health Plan",
        kind="payer",
        base_url="https://payer.test/r4",
        expects="R4",
        enabled=True,
        verified_method="live fetch",
        verified_date="2026-08-05",
    )
    rows = to_csv([card], [endpoint]).splitlines()
    header, row = rows[0].split(","), rows[1].split(",")
    cells = dict(zip(header, row, strict=True))
    assert cells["grade"] == "not observed"
    assert cells["transparency_score"] == ""
    assert cells["interop_score"] == ""


def test_end_to_end_an_endpoint_nobody_reached_publishes_one_finding(tmp_path: Path) -> None:
    """The whole path: registry to site. The card used to carry five findings, four of them
    describing documents that were never retrieved."""
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": "reached",
                        "name": "Reached Health Plan",
                        "kind": "payer",
                        "base_url": "https://reached.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-06"},
                    },
                    {
                        "id": "unreached",
                        "name": "Unreached Health Plan",
                        "kind": "payer",
                        "base_url": "https://unreached.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-06"},
                    },
                ]
            }
        )
    )
    fixtures = tmp_path / "fixtures" / "reached"
    fixtures.mkdir(parents=True)
    (fixtures / "metadata.json").write_text(json.dumps(good_capability()))
    (fixtures / "smart.json").write_text(json.dumps(good_smart()))

    out = tmp_path / "site"
    history = tmp_path / "history.json"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(registry),
                "--offline",
                "--fixtures",
                str(tmp_path / "fixtures"),
                "--out",
                str(out),
                "--history",
                str(history),
                "--vantage",
                "local/test",
            ]
        )
        == 0
    )

    detail = json.loads((out / "api" / "endpoint" / "unreached.json").read_text())
    findings = [f for d in detail["dimensions"] for f in d["findings"]]
    codes = [f["code"] for f in findings]
    assert codes == ["R1", "R2", "NR", "NR"]
    for claim in _CLAIMS_ABOUT_A_DOCUMENT:
        assert not any(claim in f["message"] for f in findings), claim
    assert detail["endpoint"]["grade"] == "not observed"
    assert detail["endpoint"]["transparency_score"] == ""

    page = (out / "endpoint" / "unreached" / "index.html").read_text()
    for claim in _CLAIMS_ABOUT_A_DOCUMENT:
        assert claim not in page

    # The observation is still recorded: not grading it must not lose the fact that it was down.
    recorded = json.loads(history.read_text())["unreached"]["observations"]
    assert recorded[-1]["up"] is False
