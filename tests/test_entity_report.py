"""The single-endpoint report: the page, not the object behind it.

Every assertion here reads rendered HTML. A sibling lane on another repository found four of
eighteen negative controls dead on their first run because every assertion read the JSON and
none read the page, so a renderer could have emitted anything and the suite would have agreed.
The properties this file holds are properties of what a named organization would actually see.

Two of them are the reason the feature exists at all:

* **A check that did not run produces no instruction.** The report's whole value is that it is
  actionable, and the fastest way to make it dishonest is to rank the checks an endpoint
  "failed" when the run never reached its documents. ``recoverable`` gates on ``observed`` and
  the page is asserted to carry no action sentence for an unobserved endpoint.
* **Absence is never rendered as a value.** No score, no bar, no zero, and a sentence saying
  which of the three states produced the silence.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import good_capability, good_smart

from fhir_scorecard.capability import (
    NO_CAPABILITY_RETRIEVED,
    NO_SMART_RETRIEVED,
    parse_capability,
    parse_smart,
)
from fhir_scorecard.cli import main
from fhir_scorecard.entity_report import (
    ACTIONS,
    NO_ACTION,
    notes,
    page_path,
    recoverable,
    report_page,
)
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import (
    NOT_OBSERVED,
    DimensionScore,
    Finding,
    Scorecard,
    build_scorecard,
)
from fhir_scorecard.site import _FINDING_DOCS, DEFAULT_ORIGIN, endpoint_page
from fhir_scorecard.vantage import VantageProbe, reconcile
from fhir_scorecard.weight import MAX_PAGE_BYTES

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_VERIFIED = "live CapabilityStatement fetch (recorded 2026-08-04)."
_OBSERVED_AT = "2026-09-12 14:27 UTC"


def render(card: Scorecard, base_url: str = "https://payer.test/r4") -> str:
    """One report's body, as the site would write it."""
    return report_page(
        card,
        base_url=base_url,
        verified=_VERIFIED,
        origin=DEFAULT_ORIGIN,
        generated_at=_OBSERVED_AT,
    ).body


def _ok_metadata() -> FetchResult:
    return FetchResult(
        url="https://payer.test/r4/metadata",
        ok=True,
        status=200,
        elapsed_ms=410,
        body=b"{}",
        error=None,
    )


def _refused(status: int = 403) -> FetchResult:
    return FetchResult(
        url="https://payer.test/r4/metadata",
        ok=False,
        status=status,
        elapsed_ms=0,
        body=b"",
        error=f"HTTP {status}",
        failure_kind="forbidden",
    )


def graded(**kwargs: object) -> Scorecard:
    """A card for an endpoint that answered and published a good document."""
    return build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _ok_metadata(),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind="payer",
        **kwargs,  # type: ignore[arg-type]
    )


def unreached() -> Scorecard:
    """A card for an endpoint every reporting vantage asked and none was answered by."""
    consensus = reconcile(
        [
            VantageProbe("ci/ubuntu", False, 0, "HTTP 403", status=403, failure_kind="forbidden"),
            VantageProbe("ci/macos", False, 0, "HTTP 403", status=403, failure_kind="forbidden"),
            VantageProbe("ci/windows", False, 0, "timed out", failure_kind="timeout"),
        ]
    )
    return build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _refused(),
        NO_CAPABILITY_RETRIEVED,
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
        as_of="2026-09-12",
        last_answered="2026-09-05",
    )


def reached_by_one_of_three() -> Scorecard:
    """Reachable, and only just: one vantage of three, all on one network."""
    document = json.dumps(good_capability())
    consensus = reconcile(
        [
            VantageProbe(
                "ci/ubuntu", True, 410, capability=document, status=200, smart_requested=True
            ),
            VantageProbe("ci/macos", False, 0, "HTTP 403", status=403, failure_kind="forbidden"),
            VantageProbe("ci/windows", False, 0, "timed out", failure_kind="timeout"),
        ]
    )
    return build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _ok_metadata(),
        parse_capability(document.encode()),
        # Every reached vantage asked for SMART discovery and none was served one, which is the
        # withheld-points state: I2 cannot be scored and its 35 points leave the denominator.
        NO_SMART_RETRIEVED,
        kind="payer",
        consensus=consensus,
        last_answered="2026-09-12",
    )


def _unreadable_document_card() -> Scorecard:
    """Answered, and what came back is not a CapabilityStatement: T0 and I0, no SMART read."""
    return build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _ok_metadata(),
        parse_capability(b'{"resourceType": "OperationOutcome"}'),
        parse_smart(json.dumps(good_smart()).encode()),
        kind="payer",
    )


def text_of(body: str) -> str:
    """The words a reader sees: markup and structured data removed, entities resolved.

    The unescaping is load-bearing rather than cosmetic. Half the sentences in ``ACTIONS``
    contain an apostrophe, which ``html.escape`` writes as ``&#x27;``, so a
    ``assert sentence not in body`` over the raw markup passes for those sentences whether or
    not the page published them - a check that cannot fail, aimed at the property this file
    exists to hold. Every assertion about a rendered sentence reads this.

    The script strip is case-insensitive because CodeQL's ``py/bad-tag-filter`` was right about
    it: ``<script.*?</script>`` does not match ``<SCRIPT>``, and this is not a sanitiser but it
    is a *matcher*, which is worse to get wrong here. A page that emitted an upper-case tag
    would leak its JSON-LD into "the words a reader sees", and the block carries the endpoint's
    name and URL - so a test asserting that some sentence is absent from the prose could pass
    or fail on structured data instead. Same defect class as the unescaping above.
    """
    stripped = re.sub(r"<script.*?</script>", " ", body, flags=re.S | re.I)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)))


# --------------------------------------------------------------------------------------
# The action vocabulary
# --------------------------------------------------------------------------------------


def test_every_documented_finding_code_has_an_action_or_a_stated_reason_for_having_none() -> None:
    """The method page is the list of checks; neither half may drift from it.

    A new check shipping without a decision about what a publisher would do about it is how a
    report grows a check it can describe and cannot act on. Held in both directions, so an
    action for a code the site does not document fails here too.
    """
    documented = {code for code, _, _, _ in _FINDING_DOCS}
    assert len(documented) > 5, "the method page's code list has stopped being readable"
    assert documented == set(ACTIONS) | NO_ACTION
    assert not set(ACTIONS) & NO_ACTION


def test_no_action_sentence_is_empty_or_a_restatement_of_the_code() -> None:
    for code, sentence in ACTIONS.items():
        assert len(sentence) > 40, f"{code} has no usable action"
        assert sentence.rstrip().endswith("."), code


# --------------------------------------------------------------------------------------
# A check that did not run produces no instruction
# --------------------------------------------------------------------------------------


def test_an_endpoint_nothing_answered_is_offered_no_action_at_all() -> None:
    """The property the whole feature turns on, asserted against the page.

    Ranking the checks this endpoint "failed" would hand a named organization a to-do list
    derived from an absence: no document was retrieved, so nothing about what it declares was
    observed, and every sentence in ``ACTIONS`` is about a document.

    **This test cannot be made red by a one-line sabotage, and that is a fact about the code
    rather than about the test.** Measured 2026-09-12: deleting the ``observed`` refusal from
    ``recoverable`` left it green, because an unreached card's findings also carry
    ``max_points == 0`` and ``points == 0``, so the points refusal and the gap refusal each
    catch what the first one would have. Three overlapping refusals is the right arrangement
    for this property and the wrong one for a control, so the branch itself is proven one test
    down, against a finding built by hand, which *did* go red on that sabotage.
    """
    card = unreached()
    assert card.grade == NOT_OBSERVED
    words = text_of(render(card))
    assert recoverable(card) == []
    assert len(ACTIONS) >= 10, "the action vocabulary has shrunk past the point of proving anything"
    for code, sentence in ACTIONS.items():
        assert sentence not in words, f"{code}'s action was published for an unobserved endpoint"
    # A floor under the assertion above: the same sentences reach the page for an endpoint that
    # did answer, so "absent" here is a property of this card and not of the matcher.
    assert ACTIONS["I1"] in text_of(render(_failing_card()))
    assert "Nothing is listed here, because no check in this report ran." in words


def test_the_grader_gives_every_unobserved_finding_a_zero_scale() -> None:
    """Measured, and recorded because it decides which of two guards is doing the work.

    ``recoverable`` refuses a finding twice: once because it was not observed, once because it
    carries no points. Today those are the same set - every ``observed=False`` finding the
    grader can produce has ``max_points == 0``, with its real scale in ``withheld_points`` - so
    the ``observed`` check is defence and the points check is what fires. That makes the
    ``observed`` branch unreachable from any card the grader builds, which is exactly the shape
    that turns a negative control green and reads as proof.

    So the invariant is pinned here, and the branch itself is tested one test down against a
    finding built by hand. If a future grader gives an unobserved check a non-zero
    ``max_points``, this fails and the guard below becomes the live one.
    """
    shapes = [unreached(), reached_by_one_of_three(), _unreadable_document_card()]
    unobserved = [
        (dimension.key, finding)
        for card in shapes
        for dimension in card.dimensions
        for finding in dimension.findings
        if not finding.observed
    ]
    assert len(unobserved) >= 4, "no shape in this set reaches an unobserved finding"
    for key, finding in unobserved:
        assert finding.max_points == 0, f"{key}/{finding.code} now carries a measurable scale"


def test_an_unobserved_finding_that_carried_points_would_still_produce_no_action() -> None:
    """The guard the test above says nothing can currently reach.

    Built by hand rather than graded, because the point is what happens if the grader changes.
    A check nobody was able to make must not become an instruction to a named organization,
    however many points it is nominally worth.
    """
    card = replace(
        graded(),
        dimensions=(
            DimensionScore(
                key="interop",
                title="Interop readiness",
                score=None,
                findings=(
                    Finding(
                        code="I2",
                        ok=False,
                        points=0,
                        max_points=35,
                        message="no vantage retrieved .well-known/smart-configuration",
                        citation="https://hl7.org/fhir/smart-app-launch/conformance.html",
                        observed=False,
                    ),
                ),
                withheld_points=35,
            ),
        ),
    )
    assert recoverable(card) == []
    assert ACTIONS["I2"] not in text_of(render(card))


def test_an_endpoint_nothing_answered_publishes_no_score_on_any_dimension() -> None:
    card = unreached()
    body = render(card)
    words = text_of(body)
    assert len(card.dimensions) == 3
    assert words.count("No score is published") == 3
    assert "out of 100" not in words, "a dimension published a number nobody measured"
    # The meter markup that carries a percentage must not appear either: the endpoint page's
    # zero-width bar beside a named insurer was the visual half of exactly this defect.
    assert "--score:" not in body


def test_the_page_says_which_kind_of_nothing_each_dimension_is() -> None:
    """Two silences, two sentences. Asked-and-unanswered is a dated observation about an
    endpoint; nothing-retrieved is the absence of one, and flattening them is how #135's
    successor would look."""
    words = text_of(render(unreached()))
    assert "asked from every reporting vantage on this run and answered by none" in words
    assert "no check here ran, because nothing was retrieved for it to read" in words


def test_the_three_states_are_counted_separately_and_all_three_are_named() -> None:
    card = unreached()
    words = text_of(render(card))
    assert "This report covers 4 checks." in words
    assert "0 ran." in words
    assert "2 were asked from every reporting vantage and answered by none." in words
    assert "2 were never asked, because nothing was retrieved for them to read." in words

    # One check in a state is still a count, and the sentence has to read as English: the
    # one-of-each case is where a published surface starts saying "1 were never asked".
    single = text_of(render(reached_by_one_of_three()))
    assert "1 was never asked, because nothing was retrieved for it to read." in single
    assert "0 was asked" not in single and "0 were asked" in single


def test_a_withheld_dimension_says_how_much_of_its_scale_went_unmeasured() -> None:
    """The third state inside an otherwise scored dimension.

    Interop's SMART check is worth 35 and this run never retrieved the document, so no
    percentage is published for the dimension - and the report says how much of the scale the
    absence covers rather than leaving a reader to infer it from a missing number.
    """
    card = reached_by_one_of_three()
    interop = next(d for d in card.dimensions if d.key == "interop")
    assert interop.score is None and interop.withheld_points == 35
    words = text_of(render(card))
    assert "35 of its 100 points went unmeasured" in words
    assert "A percentage over the remainder would be on a different scale" in words


# --------------------------------------------------------------------------------------
# Both vantage numbers travel
# --------------------------------------------------------------------------------------


def test_the_reach_sentence_carries_the_reached_count_the_vantage_count_and_the_networks() -> None:
    card = reached_by_one_of_three()
    assert len(card.vantage_reports) == 3
    words = text_of(render(card))
    assert "Reached from 1 of 3 reporting vantages, sitting on 1 network." in words
    assert "one network's view sampled several times" in words


def test_every_vantage_that_reported_gets_its_own_row_on_the_report() -> None:
    """The merged table travels with the report, not just with the endpoint page."""
    card = reached_by_one_of_three()
    body = render(card)
    for report in card.vantage_reports:
        assert f"<code>{report.vantage}</code>" in body
    assert "reached from 1 of 3 reporting vantages, on 1 network" in body


def test_an_unreached_report_names_the_vantage_and_network_counts_where_it_asks_to_be_corrected() -> (
    None
):
    words = text_of(render(unreached()))
    assert "not answered from any of 3 vantages on 1 network" in words
    assert "Correct or dispute this record" in words


def test_the_last_answered_date_travels_with_the_report() -> None:
    """An endpoint answering nowhere today reads differently depending on when it last did."""
    assert "2026-09-05" in text_of(render(unreached()))
    assert "not in the recorded window" in text_of(render(graded()))
    fresh = graded(last_answered="2026-09-12")
    assert "2026-09-12 (answered on this run)" in text_of(render(fresh))


# --------------------------------------------------------------------------------------
# What would change this
# --------------------------------------------------------------------------------------


def _failing_card() -> Scorecard:
    """Answered, with two observed failures of different weights: I1 (40) and T2 (20)."""
    document = good_capability()
    del document["software"]
    for resource in document["rest"][0]["resource"]:  # type: ignore[index]
        resource.pop("supportedProfile")
    return build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _ok_metadata(),
        parse_capability(json.dumps(document).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind="payer",
    )


def test_actions_are_ranked_by_the_points_they_would_recover() -> None:
    card = _failing_card()
    ranked = [(finding.code, gap) for _, finding, gap in recoverable(card)]
    assert ranked == [("I1", 40), ("T2", 20)], ranked
    words = text_of(render(card))
    assert words.index(ACTIONS["I1"]) < words.index(ACTIONS["T2"]), "the page reordered the ranking"


def test_each_action_states_the_points_it_recovers_and_the_dimension_they_belong_to() -> None:
    words = text_of(render(_failing_card()))
    assert "Worth up to 40 of the 100 points in Interop readiness." in words
    assert "Worth up to 20 of the 100 points in Capability transparency." in words
    assert "recovering points does not guarantee a different letter" in words


def test_each_action_shows_the_observation_it_came_from() -> None:
    """An instruction with no evidence beside it is a scolding. Every item quotes the finding."""
    card = _failing_card()
    words = text_of(render(card))
    for _, finding, _ in recoverable(card):
        assert f"This run observed: {finding.message}" in words


def test_an_endpoint_that_passed_everything_that_ran_is_told_so_rather_than_given_a_list() -> None:
    card = graded()
    assert recoverable(card) == []
    words = text_of(render(card))
    assert "every check that ran passed" in words
    assert "Checks that did not run are in the section above, and they are not failures." in words


def test_a_note_worth_no_points_is_kept_out_of_the_ranked_list_and_still_published() -> None:
    """I4: the document names an implementation guide in prose and declares none.

    It scores nothing in either direction, so ranking it beside checks that carry points would
    misstate it - and dropping it would lose the most actionable sentence on the report.
    """
    document = good_capability()
    document["implementation"] = {"description": "CARIN PatientAccess implementation"}
    for resource in document["rest"][0]["resource"]:  # type: ignore[index]
        resource.pop("supportedProfile")
    card = build_scorecard(
        "payer",
        "Example Health Plan Patient Access API",
        _ok_metadata(),
        parse_capability(json.dumps(document).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind="payer",
    )
    codes = [f.code for d in card.dimensions for f in d.findings]
    assert "I4" in codes, "the fixture no longer reaches the prose-only note"
    assert "I4" not in [f.code for _, f, _ in recoverable(card)]

    # The exact set, not membership. Measured 2026-09-12 by negative control: widening the
    # note rule from `max_points == 0` to `>= 0` swept every pointed failure into the notes
    # list as well, and a membership assertion could not see it - the note was still there,
    # its sentence was still there, and the page was now publishing I1 twice, once ranked with
    # its points and once as "worth no points in either direction".
    assert [f.code for _, f in notes(card)] == ["I4"]
    assert all(f.max_points == 0 for _, f in notes(card))

    words = text_of(render(card))
    assert "Worth no points in either direction, in Interop readiness." in words
    assert words.count("Worth no points in either direction") == 1
    assert ACTIONS["I4"] in words
    # I1 failed and carries points, so it belongs in the ranked list and nowhere else.
    assert words.count(ACTIONS["I1"]) == 1


# --------------------------------------------------------------------------------------
# Free, and asking for nothing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("card_name", ["graded", "unreached", "reached_by_one_of_three"])
def test_the_report_collects_nothing_and_sells_nothing(card_name: str) -> None:
    """No paywall, no email gate, no sign-up, no third party, no tracker.

    Asserted on the page rather than on the intent, because the intent is not what ships.
    """
    body = render(
        {
            "graded": graded,
            "unreached": unreached,
            "reached_by_one_of_three": reached_by_one_of_three,
        }[card_name]()
    )
    lowered = body.lower()
    for forbidden in ("<form", "<input", "sign in", "sign up", "subscribe", "checkout", "price"):
        assert forbidden not in lowered, forbidden
    assert "http://" not in body
    external = set(re.findall(r'src="(https?://[^"]+)"', body))
    assert external == set(), external
    assert "no sign-in, no fee, and nothing to buy" in text_of(body)


# --------------------------------------------------------------------------------------
# The built site
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site")
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(FIXTURES),
                "--registry",
                str(FIXTURES / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(tmp_path_factory.mktemp("history") / "history.json"),
            ]
        )
        == 0
    )
    return out


def _endpoint_ids() -> list[str]:
    registry = json.loads((FIXTURES / "registry.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in registry["endpoints"] if e.get("enabled", True)]
    assert len(ids) >= 3, "the fixture registry has shrunk past the point of proving anything"
    return ids


def test_the_report_lives_where_the_endpoint_page_says_it_does() -> None:
    """One literal, checked against the generator, in one place.

    Measured 2026-09-12 by negative control: renaming ``page_path``'s output to ``reports``
    left three tests green, because they addressed the file through ``page_path`` itself and
    followed the rename. A fixture derived from the constant it tests cannot catch a wrong
    constant. So the path is written out here, once, and every build assertion below uses the
    literal - while this test is what holds ``page_path`` and the endpoint page's ``href`` to
    the same string.
    """
    assert page_path("example-id") == "endpoint/example-id/report"
    card = graded()
    body = endpoint_page(
        card, base_url="https://payer.test/r4", verified=_VERIFIED, origin=DEFAULT_ORIGIN
    ).body
    assert f'href="/{page_path(card.endpoint_id)}/"' in body


def test_every_graded_endpoint_gets_a_report_page(built: Path) -> None:
    for endpoint_id in _endpoint_ids():
        page = built / "endpoint" / endpoint_id / "report" / "index.html"
        assert page.is_file(), f"no report was written for {endpoint_id}"
        assert "<h1>" in page.read_text(encoding="utf-8")


def test_each_endpoint_page_links_its_report_and_the_link_resolves(built: Path) -> None:
    """The orphan rule the site audit enforces, asserted here by name.

    A report nothing links is reachable only from the sitemap, which is how twelve organization
    pages came to be published with no path to them.
    """
    for endpoint_id in _endpoint_ids():
        page = (built / "endpoint" / endpoint_id / "index.html").read_text(encoding="utf-8")
        assert f'href="/endpoint/{endpoint_id}/report/"' in page
        # The literal the link names, not the path the generator computes: the point is that
        # the two agree, and reading both from one function cannot show that.
        assert (built / "endpoint" / endpoint_id / "report" / "index.html").is_file()


def test_every_report_is_listed_in_the_sitemap(built: Path) -> None:
    sitemap = (built / "sitemap.xml").read_text(encoding="utf-8")
    for endpoint_id in _endpoint_ids():
        assert f"<loc>{DEFAULT_ORIGIN}/endpoint/{endpoint_id}/report/</loc>" in sitemap


def test_a_report_carries_its_own_canonical_and_does_not_reuse_the_endpoint_page_title(
    built: Path,
) -> None:
    for endpoint_id in _endpoint_ids():
        report = (built / "endpoint" / endpoint_id / "report" / "index.html").read_text(
            encoding="utf-8"
        )
        endpoint = (built / "endpoint" / endpoint_id / "index.html").read_text(encoding="utf-8")
        assert (
            f'<link rel="canonical" href="{DEFAULT_ORIGIN}/endpoint/{endpoint_id}/report/">'
            in report
        )
        report_title = re.search(r"<title>(.*?)</title>", report, re.S)
        endpoint_title = re.search(r"<title>(.*?)</title>", endpoint, re.S)
        assert report_title and endpoint_title
        assert report_title.group(1) != endpoint_title.group(1)


def test_no_report_page_exceeds_the_page_weight_budget(built: Path) -> None:
    sizes = {
        endpoint_id: (built / "endpoint" / endpoint_id / "report" / "index.html").stat().st_size
        for endpoint_id in _endpoint_ids()
    }
    assert sizes, "no report pages were measured"
    assert max(sizes.values()) < MAX_PAGE_BYTES, sizes


def test_the_built_report_for_an_unreached_endpoint_publishes_no_zero(built: Path) -> None:
    """The published bytes, not the object. Read from disk, on a real offline build.

    ``bcbs-arizona-patient-access`` and ``aspirus-patient-access`` are the fixtures whose
    documents nothing retrieved. Their endpoint pages once carried ``Reachability 0``; their
    reports must carry no dimension number at all.
    """
    unreachable = [
        endpoint_id
        for endpoint_id in _endpoint_ids()
        if "not observed"
        in (built / "endpoint" / endpoint_id / "index.html").read_text(encoding="utf-8")
    ]
    assert unreachable, "no fixture in this build reaches the unobserved state"
    for endpoint_id in unreachable:
        report = (built / "endpoint" / endpoint_id / "report" / "index.html").read_text(
            encoding="utf-8"
        )
        assert "--score:" not in report
        assert "out of 100" not in text_of(report)
        assert "No score is published" in text_of(report)


def test_an_endpoint_page_rendered_without_a_report_still_names_one(built: Path) -> None:
    """The link is unconditional, so nothing can render the endpoint page without it.

    ``declared`` is a flag and this is not, deliberately: ``entity_report.pages_for`` builds a
    report for every card in the build, so there is no state in which the link is wrong. If
    that ever stops being true, ``audit_site`` fails the build on the dangling link and this
    test is the note saying why the flag was not added.
    """
    card = unreached()
    assert (
        f'href="/endpoint/{card.endpoint_id}/report/"'
        in endpoint_page(
            card, base_url="https://payer.test/r4", verified=_VERIFIED, origin=DEFAULT_ORIGIN
        ).body
    )
