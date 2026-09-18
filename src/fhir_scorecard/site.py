"""Static site generation: one indexable page per endpoint, per organization, and per kind.

Deterministic and dependency-free, same discipline as the grader. Every page is real HTML with
its own title, description, canonical URL, and structured data, because a single-page report is
not something a person can find from a search or link a colleague to.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fhir_scorecard import analytics
from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.conditions import CONDITION_HEADINGS, CONDITIONS, condition_of
from fhir_scorecard.grading import (
    NOT_OBSERVED,
    WEIGHTED_DIMENSIONS,
    DimensionScore,
    Finding,
    Scorecard,
)

DEFAULT_ORIGIN = "https://fhir.chelseakr.com"

#: Media type of an Atom document, used for the ``alternate`` links the pages carry and for the
#: feed files ``feeds.render`` writes. Declared here rather than in ``feeds`` because ``archive``
#: imports this module and ``feeds`` imports ``archive``, so the constant has to live at the
#: bottom of that chain for both to read the one spelling.
ATOM_MEDIA_TYPE = "application/atom+xml"

_PROGRAM_LABELS = {
    "medi-cal": "Medi-Cal managed care",
    "covered-ca": "Covered California",
    "tx-marketplace": "Texas individual marketplace (HealthCare.gov)",
    "fl-marketplace": "Florida individual marketplace (HealthCare.gov)",
    "oh-marketplace": "Ohio individual marketplace (HealthCare.gov)",
    "wi-marketplace": "Wisconsin individual marketplace (HealthCare.gov)",
    "az-marketplace": "Arizona individual marketplace (HealthCare.gov)",
    "mi-marketplace": "Michigan individual marketplace (HealthCare.gov)",
    "mo-marketplace": "Missouri individual marketplace (HealthCare.gov)",
    "ok-marketplace": "Oklahoma individual marketplace (HealthCare.gov)",
    "ia-marketplace": "Iowa individual marketplace (HealthCare.gov)",
    "ks-marketplace": "Kansas individual marketplace (HealthCare.gov)",
    "la-marketplace": "Louisiana individual marketplace (HealthCare.gov)",
    "nc-marketplace": "North Carolina individual marketplace (HealthCare.gov)",
}

#: Human-readable name per registry kind. Public because grades and availability are only
#: ever compared within a kind, so every surface that groups by kind uses this one map.
KIND_LABELS = {
    "payer": "Payer Patient Access APIs",
    "payer_provider_directory": "Payer Provider Directory APIs",
    "provider": "Provider and health system APIs",
    "ehr": "EHR vendor sandboxes",
    "reference": "Reference and test servers",
}
_KIND_SLUGS = {
    "payer": "payers",
    "payer_provider_directory": "provider-directories",
    "provider": "providers",
    "ehr": "ehr-vendors",
    "reference": "reference-servers",
}
_KIND_BLURBS = {
    "payer": (
        "Patient Access APIs let a member pull their own claims and coverage data into an "
        "app they choose. These grades describe what each endpoint publicly declares."
    ),
    "payer_provider_directory": (
        "Provider Directory APIs are meant to be readable by anyone - required to be, for "
        "Medicare Advantage organizations under 42 CFR 422.120, with parallel provisions for "
        "Medicaid and CHIP - so they are not graded on an authorization surface they should "
        "not have."
    ),
    "provider": "APIs published by health systems and provider organizations.",
    "ehr": (
        "Sandboxes published by EHR vendors for developers evaluating their platforms. "
        "Graded separately from payer APIs, which answer to different expectations."
    ),
    "reference": (
        "Open test servers used by the FHIR community. Included as a baseline, not as "
        "a judgment about anyone's production systems."
    ),
}

_GRADE_WORDS = {
    "A": "declares a complete, interoperable public surface",
    "B": "declares a solid public surface with minor gaps",
    "C": "answers publicly but declares little about itself",
    "D": "answers publicly with substantial gaps",
    # F used to be rendered "could not be reached from this vantage point", which was the wrong
    # sentence in both directions: it described a network when the endpoint had answered, and it
    # sat above four findings about documents nobody had retrieved.
    "F": "answers publicly, and what it declares falls short across the graded checks",
    NOT_OBSERVED: "was not observed on this run",
}

_GRADE_COLORS = {
    "A": "#19734b",
    "B": "#00666a",
    "C": "#a35d00",
    "D": "#a43b2a",
    "F": "#8f2430",
    NOT_OBSERVED: "#435c68",
}


def _grade_slug(grade: str) -> str:
    """CSS- and URL-safe form of a grade or status: "A" -> "a", "not observed" -> "not-observed"."""
    return re.sub(r"[^a-z0-9]+", "-", grade.lower()).strip("-") or "unknown"


def _status_words(card: Scorecard) -> str:
    """The sentence under an endpoint's heading, keyed on what actually happened.

    The two failure modes are different facts and get different sentences: one is about this
    project's reach, the other is about the endpoint's documents.
    """
    if card.grade != NOT_OBSERVED:
        return _GRADE_WORDS.get(card.grade, "")
    if card.reachable:
        # Two different things reach here, and one sentence used to cover both. `letter` returns
        # NOT_OBSERVED when nothing was retrieved *and* when the weighted score's bounds land in
        # two different bands because some check could not be made -- and in the second case the
        # documents very much were retrieved. On 2026-09-12 that sentence sat on 18 live pages
        # above the resource-by-resource table drawn from the CapabilityStatement it said nobody
        # had: hapi-fhir-r4's read "no vantage retrieved its public documents" and then listed
        # 146 resource types and a declared fhirVersion of 4.0.1.
        #
        # So ask what was actually retrieved rather than inferring it from the letter -- and ask
        # it of the *content* dimensions only. Reachability's own findings are observed whenever
        # the endpoint answered, which is true in both of these states, so reading them here
        # would report "some of what it publishes was read" for an endpoint whose documents
        # nobody carried.
        if any(
            f.observed
            for dimension in card.dimensions
            if dimension.key != "reachability"
            for f in dimension.findings
        ):
            return (
                "answered on this run, and some of what it publishes was read; one check could "
                "not be made, so this run cannot pin a single letter"
            )
        return (
            "answered on this run, but no vantage retrieved its public documents, so nothing "
            "here describes what it declares"
        )
    return (
        "could not be reached from any vantage on this run, so nothing about what it "
        "publishes was observed"
    )


@dataclass(frozen=True)
class Page:
    path: str  # site-relative directory, e.g. "endpoint/humana"
    title: str
    description: str
    body: str
    changefreq: str = "daily"
    priority: str = "0.5"
    #: Site-relative path of the Atom feed this page is the alternate of, or ``None`` where
    #: this build wrote no feed for it. Set by ``cli._write_site`` from the paths
    #: ``feeds.write_feeds`` reports having written, never from an assumption that a feed
    #: exists: a page advertising a feed the build did not write is the same defect as a
    #: sitemap entry no file answers.
    feed: str | None = None
    #: True for a page that says nothing to a reader who did not just arrive from a specific
    #: place (the post-checkout setup form, reachable only from a Stripe redirect) and that must
    #: never collect search traffic it can only turn away. Kept out of the sitemap by the same
    #: flag, in ``sitemap()`` below, so the two can never disagree about one page.
    noindex: bool = False


def org_slug(name: str) -> str:
    """Stable slug for an organization name, used for /org/<slug>/ pages."""
    cleaned = re.sub(r"\(.*?\)", " ", name.lower())
    cleaned = re.sub(
        r"\b(api|apis|patient access|provider directory|public|sandbox|preview|"
        r"production|open|test server|server|inc|llc)\b",
        " ",
        cleaned,
    )
    return re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-") or "unknown"


#: Names of the *surfaces* a payer publishes, as opposed to names of the payer. A trailing run of
#: these is what separates "CommunityCare Provider Directory API" from "CommunityCare".
#:
#: Deliberately narrower than the vocabulary ``org_slug`` strips. "public test server", "sandbox"
#: and "open" are not on this list, because "HAPI FHIR public test server", "Firely public test
#: server" and "SMART Health IT open sandbox" are what those projects call themselves; cutting
#: them back to "HAPI FHIR" would be inventing a name rather than repairing one. What is listed
#: here is the API-product vocabulary a payer appends to its own name, which is never part of it.
_SURFACE_TAIL = re.compile(
    r"\s*\b(patient[- ]access|provider[- ]directory|member[- ]access|drug[- ]formulary|"
    r"formulary|apis?)\b\s*$",
    re.IGNORECASE,
)


def strip_surface_tail(name: str) -> str:
    """``name`` with any trailing run of surface vocabulary removed.

    Applied repeatedly, so "Provider Directory API" comes off in one call rather than leaving
    "CommunityCare Provider Directory" behind.
    """
    previous = None
    while previous != name:
        previous = name
        name = _SURFACE_TAIL.sub("", name).strip()
    return name


def org_display_name(names: Sequence[str]) -> str:
    """An organization's name, taken as the leading words all of its endpoints share.

    An org page groups several endpoints, and naming it after whichever one happened to come first
    produced "Cigna Patient Access API" as the heading of a page that also lists Cigna's provider
    directory. The shared prefix is the part that is actually about the organization rather than
    about one of its surfaces: Cigna, Sharp Health Plan, HAPI FHIR public test server.

    Parenthetical qualifiers are dropped first, so "(R4)" and "(R5)" do not stop two releases of
    the same server from sharing a name.

    The prefix alone is only about the organization when the group's endpoints are *different*
    surfaces. When every endpoint in the group is the *same* surface and differs only inside
    parentheses, the shared prefix is the whole name, surface words included, and the heuristic
    returns an API name: ``communitycare`` -- two provider-directory endpoints distinguished by
    "(marketplace)" and "(commercial)" -- published "CommunityCare Provider Directory API" as an
    ``h1``, a ``<title>``, every breadcrumb, and a schema.org ``Organization``, asserting to a
    search engine that a named third party's API is an organization. So a trailing run of surface
    vocabulary comes off the prefix, and if that leaves nothing the unstripped prefix is kept
    rather than emitting an empty heading.

    Measured over all 81 registry names: of the 24 groups that get an org page, this changes
    exactly one -- ``communitycare``, to "CommunityCare", which is the name
    ``data/cohorts/oklahoma-marketplace.json`` already cites for those two endpoint ids from the
    CMS QHP landscape roster.
    """
    stripped = [re.sub(r"\(.*?\)", " ", name).split() for name in names]
    if not stripped:
        return ""
    common: list[str] = []
    for words in zip(*stripped, strict=False):
        if len({word.casefold() for word in words}) != 1:
            break
        common.append(words[0])
    # A group shares a slug, so it almost always shares a leading word; fall back rather than
    # render an empty heading if it somehow does not.
    prefix = " ".join(common) or " ".join(stripped[0])
    # Never return nothing: a name that is *entirely* surface words is a defect worth surfacing,
    # but a blank heading is worse, and `audit_site` fails the build on it either way.
    return strip_surface_tail(prefix) or prefix


def json_ld(payload: dict[str, object]) -> str:
    """A structured-data block. Public so pages built outside this module emit the same shape,
    which is what the site contract checks."""
    """Serialize JSON-LD safely inside a <script> block.

    ``json.dumps`` will happily emit a literal ``</script>`` from any string it is given, which
    ends the block early and turns registry data into markup. Escaping the three characters that
    can start a tag or a comment keeps the JSON valid while making that impossible.
    """
    encoded = json.dumps(payload)
    for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        encoded = encoded.replace(char, escape)
    return f'<script type="application/ld+json">{encoded}</script>'


def grade_badge(grade: str) -> str:
    """The grade or status, as one accessible badge.

    Public because the endpoint page and the single-endpoint report (``entity_report``)
    both render this, and two readers of one card would agree today and disagree the day
    one of them was tightened, with nothing to say so.
    """
    word = _GRADE_WORDS.get(grade, "grade unavailable")
    noun = "Status" if grade == NOT_OBSERVED else "Grade"
    return (
        f'<span class="grade grade-{_grade_slug(grade)}" '
        f'aria-label="{noun} {html.escape(grade)}: {html.escape(word)}">'
        f"{html.escape(grade)}</span>"
    )


def _grade_counts(cards: Sequence[Scorecard]) -> str:
    """Compact, accessible distribution used beside category links.

    Endpoints that were not observed are counted separately and never folded into F: a reader
    comparing distributions must not read "nothing was retrieved" as "graded and failed".
    """
    grades = ["A", "B", "C", "D", "F", NOT_OBSERVED]
    counts = {grade: sum(card.grade == grade for card in cards) for grade in grades}
    return "".join(
        f'<span class="grade-count grade-count-{_grade_slug(grade)}">'
        f"<strong>{count}</strong><span>{html.escape(grade)}</span></span>"
        for grade, count in counts.items()
        if count
    )


def _signal_status(card: Scorecard) -> str:
    return "not observed on this run" if card.grade == NOT_OBSERVED else f"grade {card.grade}"


def _signal_map(cards: Sequence[Scorecard]) -> str:
    """Render every real endpoint as one labeled signal on the landing page."""
    rows: list[str] = []
    for kind in _KIND_SLUGS:
        group = [card for card in cards if card.kind == kind]
        if not group:
            continue
        signals = "".join(
            f'<a class="signal signal-{_grade_slug(card.grade)}" '
            f'href="/endpoint/{html.escape(card.endpoint_id)}/" '
            f'title="{html.escape(card.name)}: {html.escape(_signal_status(card))}">'
            f'<span class="sr-only">{html.escape(card.name)}: '
            f"{html.escape(_signal_status(card))}</span></a>"
            for card in sorted(group, key=lambda card: card.name)
        )
        rows.append(
            '<div class="signal-row">'
            f'<a class="signal-label" href="/{_KIND_SLUGS[kind]}/">'
            f"{html.escape(KIND_LABELS[kind])}</a>"
            f'<span class="signal-count">{len(group):02d}</span>'
            f'<div class="signal-track">{signals}</div></div>'
        )
    return "".join(rows)


def last_answered_words(card: Scorecard) -> str:
    """When this endpoint last answered, said so a reader cannot mistake the window for eternity.

    Public for the reason ``grade_badge`` is: the report renders the same values.

    An endpoint answering nowhere today is a different story if it answered last week, and until
    #139 nothing published the difference: the only dates on the page were the run\u2019s and the
    curation record\u2019s. ``availability`` gives a rate, and a rate is not a date -- at 94% a
    reader cannot tell whether the last success was yesterday or three weeks ago, which is
    exactly the band where it decides whether the listing is worth acting on.

    The record is a bounded rolling window, so the absence of a success in it is not a claim that
    the endpoint has never answered, and the sentence says which.
    """
    if card.last_answered:
        if card.reachable:
            return f"{html.escape(card.last_answered)} (answered on this run)"
        return html.escape(card.last_answered)
    return "not in the recorded window"


def vantage_rows(card: Scorecard) -> str:
    """Every reporting vantage's own result, as a table, never resolved to a winner.

    Public for the reason ``grade_badge`` is: the report renders the same values.

    The endpoint-level claim above this table is unchanged and stays correct: one vantage
    reaching an endpoint settles that it is up, and one failing settles nothing. That asymmetry
    is a property of the claims, not a preference for a vantage -- "it is reachable" needs one
    witness, "it is unreachable" is a universal statement and needs every vantage this run had,
    bounded to the networks they sit on.

    What the table adds is the breadth of the agreement, which no single verdict can carry.
    Measured 2026-09-12 over all 81 endpoints, three GitHub vantages against one residential
    vantage: three disagreed, one in each direction plus a third, so no vantage here is the
    reliable one. Publishing the rows is the honest alternative to electing one.
    """
    if not card.vantage_reports:
        return ""
    reached = sum(1 for r in card.vantage_reports if r.reachable)
    total = len(card.vantage_reports)
    networks = len({r.network for r in card.vantage_reports})
    rows = ""
    for report in sorted(card.vantage_reports, key=lambda r: r.vantage):
        if report.reachable:
            saw = (
                f"answered in {report.elapsed_ms} ms"
                if report.elapsed_ms is not None
                else "answered"
            )
        else:
            saw = report.error or "no answer, and no condition was reported"
        detail = ""
        if report.status is not None:
            detail = f"HTTP {report.status}"
        if report.failure_kind:
            detail = f"{detail}, {report.failure_kind}" if detail else report.failure_kind
        rows += (
            "<tr>"
            f'<th scope="row"><code>{html.escape(report.vantage)}</code></th>'
            f"<td>{'reached' if report.reachable else 'not reached'}</td>"
            f"<td>{html.escape(saw)}</td>"
            f"<td>{html.escape(detail) or '&mdash;'}</td>"
            "</tr>"
        )
    # Both numbers, and what they are numbers *of*. Several hosts on one provider's network share
    # its address space and any rule a payer edge applies to it, so the vantage count on its own
    # would overstate how independent the agreement is.
    caption = (
        f"reached from {reached} of {total} reporting "
        f"{'vantage' if total == 1 else 'vantages'}, on "
        f"{networks} {'network' if networks == 1 else 'networks'}"
    )
    return (
        '<section class="evidence-card vantage-reports">'
        '<p class="eyebrow">What each vantage saw</p>'
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        'aria-label="Per-vantage results">'
        '<table class="usa-table usa-table--striped vantage-table">'
        f"<caption>{html.escape(caption)}</caption>"
        '<thead><tr><th scope="col">Vantage</th><th scope="col">Result</th>'
        '<th scope="col">What it saw</th><th scope="col">Condition</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        '<p class="vantage-note">Vantages on one network are one network\u2019s view sampled '
        "several times. A rule applied to that network\u2019s address space reaches every one of "
        "them at once and reads exactly like agreement.</p></section>"
    )


def dimension_unanswered(dimension: DimensionScore) -> bool:
    """Whether this whole dimension is the "asked everywhere, answered nowhere" state.

    Public for the reason ``grade_badge`` is: the report renders the same values.

    Every finding, not any: a dimension with one unanswered check beside checks that did run is
    a partial measurement, and calling the whole thing "no answer" would overstate it in the
    other direction.
    """
    return bool(dimension.findings) and all(f.unanswered for f in dimension.findings)


def _dimension_meter(title: str, score: int | None, *, unanswered: bool = False) -> str:
    """A dimension's score, or the absence of one, or the fact that nothing answered.

    An unobserved dimension gets no bar and no number. Rendering it as 0 was the visual half of
    the same error: a bar at zero next to a named organization reads as a measurement.

    ``unanswered`` gets no bar and no number either -- the score is exactly as absent -- and a
    different word, because "not observed" over a dimension where three networks asked and none
    was answered understates a real finding.
    """
    if score is None:
        label = "no answer" if unanswered else "not observed"
        described = (
            "no vantage was answered on this run" if unanswered else "not observed on this run"
        )
        return (
            '<div class="dimension-meter dimension-meter-unscored">'
            f"<div><span>{html.escape(title)}</span><strong>{label}</strong></div>"
            f'<span class="meter meter-unscored" '
            f'aria-label="{html.escape(title)}: {described}"></span></div>'
        )
    return (
        '<div class="dimension-meter">'
        f"<div><span>{html.escape(title)}</span><strong>{score}</strong></div>"
        f'<span class="meter" aria-label="{html.escape(title)}: {score} out of 100">'
        f'<span style="--score:{score}%"></span></span></div>'
    )


def finding_mark(finding: Finding) -> tuple[str, str, str]:
    """Class, glyph, and screen-reader prefix for one finding.

    Public for the reason ``grade_badge`` is: the report renders the same values.

    Three states, not two. A check that never ran is neither a pass nor a failure, and a ✗ beside
    it would publish the thing this project exists not to publish. A finding worth no points is
    not a verdict either: "not applicable to a Provider Directory API" and "this document names
    CARIN in prose" are notes, and a ✓ or a ✗ would both misread them.
    """
    if finding.unanswered:
        # A fourth mark for the third state. "Not observed" is what a run says when nobody
        # looked; this is what it says when it looked from every vantage it had and was answered
        # by none of them. Both withhold the score and only one of them is silence.
        return "unanswered", "⊘", "No answer"
    if not finding.observed:
        return "unobserved", "○", "Not observed"
    if finding.max_points == 0:
        return "note", "○", "Note"
    return ("ok", "✓", "Pass") if finding.ok else ("no", "✗", "Needs attention")


def _findings_html(card: Scorecard) -> str:
    out: list[str] = []
    for dim in card.dimensions:
        items = ""
        for f in dim.findings:
            state, glyph, prefix = finding_mark(f)
            items += (
                f'<li class="finding {state}">'
                f'<span class="mark" aria-hidden="true">{glyph}</span>'
                '<span class="finding-copy">'
                f'<span class="sr-only">{prefix}: </span>'
                f"{html.escape(f.message)}</span>"
                '<span class="finding-links">'
                f'<a href="/how-we-grade/#{html.escape(f.code)}">{f.code}</a>'
                f'<a href="{html.escape(f.citation)}" rel="nofollow">Spec ↗</a></span></li>'
            )
        out.append(
            '<section class="finding-group">'
            f"{_dimension_meter(dim.title, dim.score, unanswered=dimension_unanswered(dim))}"
            f'<ul class="findings">{items}</ul></section>'
        )
    return "".join(out)


def endpoint_page(
    card: Scorecard,
    base_url: str,
    verified: str,
    origin: str,
    organization: tuple[str, str] | None = None,
    declared: bool = False,
    app_to_server: str = "",
) -> Page:
    """One endpoint's page.

    ``app_to_server`` is the declared SMART Backend Services and Bulk Data block (#97),
    already rendered by ``backend.block_html``; observed, never graded.

    ``declared`` is whether this build wrote the endpoint's declared-capability pages (#102),
    and it decides whether the page links to them. Passed in rather than assumed, for the same
    reason a page advertises a feed only when one was written: a link to a page the build did
    not write is a finding the site audit exists to raise.

    ``organization`` is ``(display name, slug)`` when this endpoint is one of several surfaces
    the same organization publishes, and ``None`` when it is the only one. It is what puts the
    /org/ page in the breadcrumb, and it is not decoration: organization pages were built and
    listed in the sitemap while no page on the site linked to one, so twelve published pages
    were reachable only by reading the sitemap. ``tests/test_site_audit.py`` now fails on an
    orphan, which is how that would be caught next time rather than by inspection.
    """
    kind_label = KIND_LABELS.get(card.kind, card.kind)
    summary = _status_words(card)
    declared_link = (
        f'<p><a class="usa-link" href="/endpoint/{html.escape(card.endpoint_id)}/capabilities/">'
        "What its CapabilityStatement declares, resource by resource →</a></p>"
        if declared
        else ""
    )
    # Unconditional, because `entity_report.pages_for` builds one report for every card in the
    # build -- including the cards nothing answered, which are the ones whose publishers have
    # most reason to read the page that says so. A link at a page the build did not write is
    # what `audit_site` exists to catch, and it would catch this one.
    report_link = (
        f'<p><a class="usa-link" href="/endpoint/{html.escape(card.endpoint_id)}/report/">'
        "This endpoint's full report: what was observed, what was not, and what would change "
        "it →</a></p>"
    )
    unobserved = card.grade == NOT_OBSERVED
    dimensions = "".join(
        _dimension_meter(dim.title, dim.score, unanswered=dimension_unanswered(dim))
        for dim in card.dimensions
    )
    record_link = (
        f'<p><a href="/history/{html.escape(card.endpoint_id)}/">'
        "Every observation on record for this endpoint</a>, with the dates it answered and the "
        "dates it did not.</p>"
    )
    drift = ""
    if card.drift_events:
        events = "".join(f"<li>{html.escape(e)}</li>" for e in card.drift_events)
        drift = f"<h3>Declared capability changes</h3><ul>{events}</ul>"
    elif card.observed_since:
        drift = (
            f"<p>Observed since {html.escape(card.observed_since)}; no changes to declared "
            "capability recorded.</p>"
        )
    if card.drift_alternations:
        returns = "".join(f"<li>{html.escape(a)}</li>" for a in card.drift_alternations)
        drift += (
            "<h3>Declarations this endpoint returns to</h3>"
            "<p>This address has served a declaration, moved away from it, and served it again. "
            "That usually means one hostname in front of more than one backend rather than a "
            "publisher changing anything, so each return is counted here once instead of being "
            "reported as a fresh capability change every time a probe lands on the other "
            "backend.</p>"
            f"<ul>{returns}</ul>"
        )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "WebAPI",
        "name": card.name,
        "url": base_url,
        "documentation": f"{origin}/endpoint/{card.endpoint_id}/",
        "provider": {"@type": "Organization", "name": card.name},
        "isAccessibleForFree": True,
    }
    org_crumb = ""
    if organization is not None:
        org_name, org_path = organization
        org_crumb = (
            '<li class="usa-breadcrumb__list-item">'
            f'<a href="/org/{org_path}/" class="usa-breadcrumb__link">'
            f"<span>{html.escape(org_name)}</span></a></li>"
        )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item">
<a href="/{_KIND_SLUGS.get(card.kind, "reference-servers")}/" class="usa-breadcrumb__link">
<span>{html.escape(kind_label)}</span></a></li>
{org_crumb}
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>{html.escape(card.name)}</span></li>
</ol></nav>
<header class="endpoint-hero">
<div class="endpoint-heading">
<p class="eyebrow">Public surface / {html.escape(kind_label)}</p>
<h1>{html.escape(card.name)}</h1>
<p class="lede">This endpoint {html.escape(summary)}.</p>
</div>
<div class="hero-grade"><span>{"Current status" if unobserved else "Current grade"}</span>
{grade_badge(card.grade)}</div>
</header>
<section class="score-overview" aria-label="Dimension scores">{dimensions}</section>
<div class="evidence-grid">
<section class="evidence-card">
<p class="eyebrow">Observed surface</p>
<dl class="facts">
  <dt>Base URL</dt><dd><code>{html.escape(base_url)}</code></dd>
  <dt>Category</dt><dd>{html.escape(kind_label)}</dd>
  <dt>Availability</dt><dd>{html.escape(card.availability or "not yet recorded")}</dd>
  <dt>Last answered</dt><dd>{last_answered_words(card)}</dd>
  {
        f"<dt>Vantage agreement</dt><dd>{html.escape(card.vantage_note)}</dd>"
        if card.vantage_note
        else ""
    }
</dl>
</section>
{vantage_rows(card)}
<section class="evidence-card evidence-card-accent">
<p class="eyebrow">Interpretation</p>
<p>A grade describes two public discovery documents at one point in time. It does not inspect
patient data, authenticated behavior, or clinical quality.</p>
<a class="usa-link" href="/how-we-grade/">Read the scoring method →</a>
{report_link}
{declared_link}
</section>
</div>
<h2>Findings</h2>
{_findings_html(card)}
{drift}
{app_to_server}
<section class="verification">
<p class="eyebrow">Registry provenance</p>
<h2>How this entry was verified</h2>
<p>{html.escape(verified)}</p>
</section>
<details class="badge-embed">
<summary>Share this endpoint's {"status" if unobserved else "grade"}</summary>
<div><img src="/badge/{html.escape(card.endpoint_id)}.svg"
alt="FHIR Scorecard: {html.escape(card.name)} {html.escape(_badge_alt(card))}" width="{
        _badge_width(card.grade)
    }" height="28">
<p>Link the badge back to this evidence page so readers can inspect the current findings.</p>
<code>&lt;a href="{html.escape(origin)}/endpoint/{html.escape(card.endpoint_id)}/"&gt;
&lt;img src="{html.escape(origin)}/badge/{html.escape(card.endpoint_id)}.svg"
alt="FHIR Scorecard: {html.escape(_badge_alt(card))}"&gt;&lt;/a&gt;</code></div>
</details>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">This is an observational snapshot of a public, unauthenticated surface. It is
not an audit, a ranking of care quality, or a statement about anyone's regulatory compliance.
See <a href="/how-we-grade/">how we grade</a>.</p>
{record_link}
</div></div>
{json_ld(jsonld)}
"""
    return Page(
        path=f"endpoint/{card.endpoint_id}",
        title=(
            f"{card.name}: FHIR endpoint not observed"
            if unobserved
            else f"{card.name}: FHIR API grade {card.grade}"
        ),
        description=(
            f"{card.name} {summary}."
            + (
                ""
                if unobserved
                else " Public FHIR CapabilityStatement graded on "
                "reachability, transparency, and interoperability readiness."
            )
        ),
        body=body,
        priority="0.8",
    )


def org_page(name: str, cards: list[Scorecard], origin: str) -> Page:
    # `audit.REQUIRED_JSONLD_FIELDS` has promised an Organization contract since the
    # site contract was written, and no page had ever emitted one, so the promise
    # went unkept and unchecked. This is the page that has an organization on it.
    # Two properties, both already visible above: the name the registry records and
    # the address this page answers on. Nothing about the organization is asserted
    # that the page does not already say, and in particular no grade, rating or
    # compliance statement appears here -- a grade is an observation of a surface,
    # not a property of a company.
    organization = json_ld(
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": name,
            "url": f"{origin}/org/{org_slug(name)}/",
        }
    )
    rows = "".join(
        '<li class="surface-card">'
        f'<div>{grade_badge(c.grade)}<span class="eyebrow">'
        f"{html.escape(KIND_LABELS.get(c.kind, c.kind))}</span></div>"
        f'<a href="/endpoint/{c.endpoint_id}/">{html.escape(c.name)}</a>'
        f"<p>{html.escape(_status_words(c))}.</p></li>"
        for c in sorted(cards, key=lambda c: c.name)
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list"><li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li></ol></nav>
<p class="eyebrow">Organization record</p>
<h1>{html.escape(name)}: public FHIR endpoints</h1>
<p class="lede">{len(cards)} publicly observable FHIR surfaces from this organization.</p>
<ul class="surface-grid">{rows}</ul>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Observational snapshots of public surfaces, not audits or compliance
determinations.</p>
</div></div>
{organization}
"""
    return Page(
        path=f"org/{org_slug(name)}",
        title=f"{name}: public FHIR API grades",
        description=f"Grades for {len(cards)} publicly observable FHIR endpoints from {name}.",
        body=body,
        priority="0.7",
    )


def kind_page(kind: str, cards: list[Scorecard], origin: str) -> Page:
    label = KIND_LABELS.get(kind, kind)
    blurb = _KIND_BLURBS.get(kind, "")
    rows = "".join(
        f'<tr><td><a href="/endpoint/{c.endpoint_id}/">'
        f"{html.escape(c.name)}</a></td><td>{grade_badge(c.grade)}</td>"
        f"<td>{html.escape(c.availability or 'not yet recorded')}</td></tr>"
        for c in sorted(cards, key=lambda c: (c.grade, c.name))
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list"><li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li></ol></nav>
<p class="eyebrow">Endpoint registry / {len(cards)} surfaces</p>
<h1>{html.escape(label)}</h1>
<p class="lede">{html.escape(blurb)}</p>
<div class="category-summary">
<p><strong>{sum(c.reachable for c in cards)}</strong><span>answered on this run</span></p>
<div class="grade-distribution" aria-label="Grade distribution">{_grade_counts(cards)}</div>
</div>
<div class="usa-table-container--scrollable" tabindex="0" role="region" aria-label="Graded endpoints">
<table class="usa-table usa-table--striped registry-table"><caption>{len(cards)} graded {html.escape(label.lower())}</caption>
<thead><tr><th scope="col">Endpoint</th><th scope="col">Grade</th>
<th scope="col">Availability</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Grades are comparable within this category only. A payer Patient Access API and
an EHR vendor sandbox answer to different implementation guides, so they are never ranked
against each other.</p>
</div></div>
"""
    return Page(
        path=_KIND_SLUGS.get(kind, kind),
        title=f"{label}: public FHIR endpoint grades",
        description=f"{len(cards)} {label.lower()} graded on reachability, capability "
        "transparency, and interoperability readiness.",
        body=body,
        priority="0.9",
    )


def _programs_text(member: CohortMember) -> str:
    return ", ".join(_PROGRAM_LABELS.get(p, p) for p in member.programs)


def _cohort_included_rows(cohort: Cohort, cards: dict[str, Scorecard]) -> str:
    """One row per listed endpoint, or a sentence when nothing in the cohort is listable.

    An empty table body would read as "no member has an endpoint", which is a claim; the
    sentence states the actual situation, which is that none of the reviewed members published
    a base URL this project could verify.
    """
    rows = "".join(
        f"<tr><td>{html.escape(member.name)}</td>"
        f"<td>{html.escape(_programs_text(member))}</td>"
        f'<td><a href="/endpoint/{card.endpoint_id}/">'
        f"{html.escape(card.name)}</a></td>"
        f"<td>{html.escape(KIND_LABELS.get(card.kind, card.kind))}</td>"
        f"<td>{grade_badge(card.grade)}</td></tr>"
        for member in cohort.included
        for card in (cards[eid] for eid in member.endpoint_ids if eid in cards)
    )
    if not rows:
        return (
            '<tr><td colspan="5">No member of this cohort currently publishes a base URL '
            "this project could verify. That gap is the finding.</td></tr>"
        )
    return rows


def _cohort_conditions_html(listed: list[Scorecard]) -> str:
    """Why the listed endpoints that did not answer did not answer, split by kind (#117).

    The cohort page publishes "N answered on this run" over the listed endpoints, and until now
    the other endpoints were one number. An endpoint answering HTTP 401 and an endpoint whose
    hostname does not resolve both landed in it, which is the merge #117 exists to remove one
    level up on ``/coverage/``; a cohort page is where a reader meets a named plan, so it is the
    place the merge costs most.

    Split **within kind**, because a Patient Access API and a Provider Directory API are never
    compared on this site and a condition table that pooled them would be the first place they
    were. The counts are never added across conditions: each row is a count of endpoints in one
    condition, and the only total on the page is the one already published beside "answered".

    Absent, not empty, when every listed endpoint answered. A table of zeroes under a heading
    about conditions would read as a measurement of conditions that did not occur.
    """
    unanswered = [card for card in listed if not card.reachable]
    if not unanswered:
        return ""
    by_kind: dict[str, list[Scorecard]] = {}
    for card in unanswered:
        by_kind.setdefault(card.kind, []).append(card)
    sections = ""
    for kind in sorted(by_kind):
        rows = "".join(
            # A row header, not a cell: the endpoint names the row, and `test_headline_counts`
            # counts `<td><a href="/endpoint/` to check the listed-endpoints table is one row
            # per member. A second table using that markup would be counted as listings.
            f'<tr><th scope="row"><a href="/endpoint/{card.endpoint_id}/">'
            f"{html.escape(card.name)}</a></th>"
            f"<td>{html.escape(CONDITION_HEADINGS[condition])}</td>"
            f"<td>{html.escape(CONDITIONS[condition])}</td></tr>"
            for card in sorted(by_kind[kind], key=lambda c: c.name.lower())
            for condition in (condition_of(card.failure_kinds),)
        )
        label = KIND_LABELS.get(kind, kind)
        sections += (
            '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
            f'aria-label="Conditions observed for {html.escape(label)}">'
            '<table class="usa-table usa-table--striped">'
            f"<caption>{html.escape(label)}: "
            f"{len(by_kind[kind])} listed "
            f"{'endpoint' if len(by_kind[kind]) == 1 else 'endpoints'} did not answer</caption>"
            '<thead><tr><th scope="col">Endpoint</th><th scope="col">Condition</th>'
            '<th scope="col">What it means</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    return f"""<h2>What "did not answer" was</h2>
<p>The condition each listed endpoint that did not answer was observed in on this run, kept
apart by category and never added together. An endpoint that answered and declined this request
is running; an endpoint that produced no document is a different fact about a different thing.
This project reports the condition and reads neither of them as a choice or as a defect.</p>
{sections}"""


def _cohort_excluded_rows(cohort: Cohort) -> str:
    basis_words = {
        "portal_reviewed": "the plan's own documentation was reviewed",
        "not_located": "public search only; the plan's documentation was not located",
    }
    rows = ""
    for member in cohort.excluded:
        exclusion = member.exclusion
        if exclusion is None:  # pragma: no cover - excluded members always carry one
            continue
        rows += (
            f"<tr><td>{html.escape(member.name)}</td>"
            f"<td>{html.escape(_programs_text(member))}</td>"
            f"<td>{html.escape(exclusion.reason)} "
            f"({html.escape(basis_words.get(exclusion.basis, exclusion.basis))}, "
            f"{html.escape(exclusion.date)}; "
            f'<a href="{html.escape(exclusion.source)}" rel="nofollow">source</a>)</td></tr>'
        )
    return rows


def _declared_kinds_html(cohort: Cohort, declared_kinds: tuple[str, ...]) -> str:
    """Links to this cohort's declaration census pages, or nothing where there are none."""
    if not declared_kinds:
        return ""
    links = "".join(
        f'<li><a href="/{html.escape(cohort.cohort_id)}/capabilities/{html.escape(kind)}/">'
        f"What its {html.escape(KIND_LABELS.get(kind, kind))} endpoints declare</a></li>"
        for kind in declared_kinds
    )
    return (
        "<h2>What the listed endpoints declare</h2>"
        "<p>How many of the listed endpoints with a readable CapabilityStatement declare each "
        "resource and each interaction on it, counted within a category and never across one. "
        "Declared, not tested.</p>"
        f'<ul class="usa-list">{links}</ul>'
    )


def cohort_page(
    cohort: Cohort,
    cards: dict[str, Scorecard],
    origin: str,
    declared_kinds: tuple[str, ...] = (),
) -> Page:
    """A curated cohort: who is in it, who could be listed, and who could not, with reasons.

    ``declared_kinds`` names the kinds this build wrote a declaration census page for
    (#102). The page links exactly those, and none it was not told about.

    The exclusions table is not an appendix. For a cohort whose membership is public and finite,
    "this plan publishes no base URL an unregistered visitor can see" is as much a result as any
    grade, and omitting it would make the included list read as the whole cohort.
    """
    # Counted from the cards this run actually produced, not from the ids in the curation file:
    # a listed endpoint is a row somebody wrote down, and the number beside "answered" has to
    # come from a probe. They are usually the same number, and when they are not, the difference
    # is the interesting part.
    #
    # Counted per endpoint, not per (member, endpoint) row, and the difference is not
    # hypothetical: `florida-marketplace` lists Cigna Healthcare and Cigna Healthcare of Florida
    # as two member organizations pointing at one published surface, and Florida Blue and
    # Florida Blue HMO likewise, so on 2026-09-10 the page said "17 endpoints listed" over
    # thirteen endpoints and counted four of them twice in "answered on this run" -- still
    # served on 2026-09-12. `michigan-marketplace` has one such pair. The table below is right to keep a row per member - the row is about the plan,
    # and a plan that publishes through another entity's server is still that plan's answer to
    # the rule - but a count labeled "endpoints" has to be a count of endpoints.
    #
    # The label was the open question, and it is settled here rather than left to the reader:
    # "endpoints listed" counts endpoints. Three things decide it. The word is "endpoints", on a
    # page whose table is headed "Listed endpoints" and whose subject is endpoints. The number
    # beside it, "answered on this run", is a count of probes, and one server answering once is
    # one answer - so reading the first as listings and the second as endpoints would publish a
    # ratio ("11 of 17") whose halves count different things, and the page's own description
    # prints exactly that ratio. And a reader who wants the listings can have them under their
    # own name: `listings_stat` publishes that number as its own labeled figure rather than
    # reusing this one, on the cohorts where the two differ.
    rows = [cards[eid] for m in cohort.included for eid in m.endpoint_ids if eid in cards]
    listed = list({card.endpoint_id: card for card in rows}.values())
    included_endpoints = len(listed)
    listed_rows = len(rows)
    answered = sum(card.reachable for card in listed)
    shared = listed_rows - included_endpoints
    # Printed only where it says something. On the eleven cohorts where every member has its own
    # surface the two numbers are equal, and a second tile repeating the first is noise that
    # teaches a reader to skip the row where it is load-bearing.
    listings_stat = (
        "" if not shared else f"<p><strong>{listed_rows}</strong><span>plan listings</span></p>\n"
    )
    shared_note = (
        ""
        if not shared
        else (
            f" The table below carries {listed_rows} plan listings over those "
            f"{included_endpoints} {'endpoint' if included_endpoints == 1 else 'endpoints'}, "
            f"because {shared} of the listings "
            f"{'names' if shared == 1 else 'name'} a surface another member organization has "
            "already listed; each plan appears under its own name, and neither the endpoint "
            "count nor the answered count counts a shared surface twice."
        )
    )
    notes = "".join(f"<p>{html.escape(note)}</p>" for note in cohort.notes)
    sources = "".join(
        f'<li><a href="{html.escape(s.url)}" rel="nofollow">{html.escape(s.label)}</a> '
        f"(retrieved {html.escape(s.date)})</li>"
        for s in cohort.sources
    )
    sources_html = f'<h2>Membership sources</h2><ul class="cards">{sources}</ul>' if sources else ""
    excluded_rows = _cohort_excluded_rows(cohort)
    excluded_html = ""
    if excluded_rows:
        excluded_html = f"""
<h2>Members reviewed and not listed</h2>
<p>Each exclusion records how far the review went, on what date, and where to check it. A review
that found nothing is not proof that nothing exists: if one of these plans publishes a base URL
we missed, please <a href="/claim/">tell us</a>.</p>
<table class="usa-table usa-table--striped"><caption>Cohort members with no verifiable public endpoint</caption>
<thead><tr><th scope="col">Plan</th><th scope="col">Programs</th>
<th scope="col">Why it is not listed</th></tr></thead>
<tbody>{excluded_rows}</tbody></table>
"""
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list"><li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li></ol></nav>
<p class="eyebrow">Curated cohort / fixed public roster</p>
<h1>{html.escape(cohort.name)}</h1>
<p class="lede">{html.escape(cohort.description)}</p>
<div class="cohort-stats" aria-label="Cohort coverage">
<p><strong>{len(cohort.members)}</strong><span>organizations reviewed</span></p>
<p><strong>{len(cohort.included)}</strong><span>published a base URL we could verify</span></p>
{listings_stat}<p><strong>{included_endpoints}</strong><span>endpoints listed</span></p>
<p><strong>{answered}</strong><span>answered on this run</span></p>
</div>
<p>{len(cohort.included)} of {len(cohort.members)} member organizations publish a FHIR base URL
this project could verify from public documentation, which is a curation record with a date on
it, not a live figure; {included_endpoints} verified
{"endpoint is" if included_endpoints == 1 else "endpoints are"} listed below. Of those,
<strong>{answered} answered when this page was generated</strong>, which is the measured number:
it comes from this run's probes, and it moves when the endpoints do.{shared_note} The rest of
the roster is recorded with the reason it could not be listed, because for a cohort whose
membership is public and finite, the gap is itself a finding.</p>
{notes}
{sources_html}
<h2>Listed endpoints</h2>
<div class="usa-table-container--scrollable" tabindex="0" role="region" aria-label="Verified cohort endpoints">
<table class="usa-table usa-table--striped"><caption>Verified public FHIR endpoints of cohort members</caption>
<thead><tr><th scope="col">Plan</th><th scope="col">Programs</th>
<th scope="col">Endpoint</th><th scope="col">Category</th><th scope="col">Grade</th></tr></thead>
<tbody>{_cohort_included_rows(cohort, cards)}</tbody></table></div>
<p>Grades are comparable within a category only: a Patient Access API and a Provider Directory
API answer to different expectations and are never ranked against each other.</p>
{_cohort_conditions_html(listed)}
{_declared_kinds_html(cohort, declared_kinds)}
{excluded_html}
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Observational snapshots of public discovery surfaces. Not audits, not
compliance determinations, and not statements about care quality. Publishing a base URL to
unregistered visitors is not required by any rule this project reads, and a plan that does not
is not violating anything; it is only not independently checkable from outside.</p>
</div></div>
"""
    return Page(
        path=cohort.cohort_id,
        title=f"{cohort.name}: public FHIR endpoint grades",
        description=(
            f"{cohort.description} {len(cohort.included)} of {len(cohort.members)} "
            f"member organizations publish a verifiable public FHIR endpoint; "
            f"{answered} of {included_endpoints} listed endpoints answered on the "
            "latest run."
        ),
        body=body,
        priority="0.9",
    )


def sitemap(pages: list[Page], origin: str, feeds: Sequence[str] = ()) -> str:
    """Every URL this build wrote, pages and feeds alike.

    ``feeds`` carries site-relative *file* paths (``endpoint/humana/feed.xml``) rather than the
    directory URLs a page gets, which is why they cannot be folded into ``pages``: a ``Page``
    addresses a directory and this addresses a file. Widened rather than bypassed on purpose -
    ``audit._check_sitemap`` checks the sitemap in both directions, so a feed the sitemap does
    not list is a finding and a listed feed no file answers is a different one, and leaving
    feeds out of the sitemap would have made both unreachable.
    """
    entries = "".join(
        f"<url><loc>{origin}/{p.path + '/' if p.path else ''}</loc>"
        f"<changefreq>{p.changefreq}</changefreq>"
        f"<priority>{p.priority}</priority></url>"
        for p in pages
        if not p.noindex
    ) + "".join(
        f"<url><loc>{origin}/{path}</loc><changefreq>daily</changefreq>"
        "<priority>0.3</priority></url>"
        for path in feeds
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


def robots(origin: str) -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n"


def _badge_alt(card: Scorecard) -> str:
    return "not observed on the latest run" if card.grade == NOT_OBSERVED else f"grade {card.grade}"


_BADGE_LEFT = 98


def _badge_width(grade: str) -> int:
    """Badge width, widened for a status that is words rather than one letter."""
    return _BADGE_LEFT + (28 if grade != NOT_OBSERVED else 88)


def status_badge(card: Scorecard) -> str:
    """A small, dependency-free SVG owners can embed while linking to the evidence page.

    A not-observed endpoint gets a wider, neutral badge that says so. Stamping it with an F
    would put a failing letter on an organization whose documents this run never retrieved.
    """
    unobserved = card.grade == NOT_OBSERVED
    value = "not observed" if unobserved else card.grade
    width = _badge_width(card.grade)
    right = width - _BADGE_LEFT
    color = _GRADE_COLORS.get(card.grade, "#435c68")
    left_label = "FHIR endpoint" if unobserved else "FHIR grade"
    title = f"{card.name}: {left_label} {value}"
    desc = (
        "This endpoint was not observed on the latest run"
        if unobserved
        else "Current observational grade for this endpoint"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="28"
role="img" aria-labelledby="title desc">
<title id="title">{html.escape(title)}</title>
<desc id="desc">{html.escape(desc)}</desc>
<rect width="{width}" height="28" rx="3" fill="#102b3f"/>
<rect x="{_BADGE_LEFT}" width="{right}" height="28" rx="3" fill="{color}"/>
<path fill="{color}" d="M{_BADGE_LEFT} 0h3v28h-3z"/>
<text x="10" y="18" fill="#fff" font-family="Arial,sans-serif" font-size="11">{
        html.escape(left_label)
    }</text>
<text x="{_BADGE_LEFT + right // 2}" y="19" fill="#fff" text-anchor="middle"
font-family="Arial,sans-serif" font-size="{11 if unobserved else 14}"
font-weight="700">{html.escape(value)}</text>
</svg>
"""


def write_page(out_dir: Path, page: Page, origin: str, generated_at: str) -> None:
    target = out_dir / page.path if page.path else out_dir
    target.mkdir(parents=True, exist_ok=True)
    canonical = f"{origin}/{page.path + '/' if page.path else ''}"
    (target / "index.html").write_text(
        _shell(page, canonical=canonical, origin=origin, generated_at=generated_at),
        encoding="utf-8",
    )


def write_assets(out_dir: Path) -> None:
    """Copy the vendored stylesheet, script, font, and icon files into the site output.

    Every page links /assets/uswds/css/uswds.min.css and /assets/site.css: the design system is
    served from the same origin as the pages, at the version pinned in assets/uswds/VERSION.txt,
    never from a CDN. The one third-party script is Google Analytics, loaded by the guarded
    inline loader in ``fhir_scorecard.analytics`` and only on the production host (ADR 0006).
    The files ship inside the package so an installed copy builds the same site a checkout does.
    """
    from importlib import resources

    with resources.as_file(resources.files("fhir_scorecard") / "assets") as assets_root:
        shutil.copytree(assets_root, out_dir / "assets", dirs_exist_ok=True)


def _site_path_prefix(origin: str) -> str:
    """The path component of the origin, so internal links follow the hosting shape.

    Internal links are written site-root-relative (``/endpoint/...``), which is correct
    when the site is served at a domain root, as the canonical
    ``https://fhir.chelseakr.com`` is. An origin that carries a path - the project-page
    shape this site was served under until 2026-08-19, where root-relative links would
    escape the site - gets that path prepended to every internal href and src at render
    time. Hardcoding the project path was a live bug: the day the custom domain started
    serving, every internal link on it pointed at ``/fhir-scorecard/...``, a path that
    exists only on the old host.
    """
    return urlsplit(origin).path.rstrip("/")


_INTERNAL_LINK = re.compile(r'\b(href|src)="/(?!/)')


#: The social card `write_assets` copies into every build, relative to the site root.
SOCIAL_CARD = "assets/social-card.png"
SOCIAL_CARD_SIZE = (1200, 630)
SOCIAL_CARD_ALT = (
    "FHIR Scorecard: a plain-language operational scorecard for publicly observable "
    "FHIR endpoints. Rescored daily; every finding cites the spec."
)


def social_card_url(origin: str) -> str:
    """The absolute address of the card, which is the only kind og:image may carry.

    A crawler reads this head from somewhere that is not this origin, so the
    root-relative form every other asset on the page uses would resolve against
    the wrong site or against nothing. It is built from ``origin`` rather than
    hardcoded for the same reason internal links are: this site was served under
    a project path until 2026-08-19, and a hardcoded host is a broken preview the
    day the hosting shape changes.
    """
    return f"{origin.rstrip('/')}/{SOCIAL_CARD}"


def _feed_link(page: Page) -> str:
    """The ``alternate`` link to this page's Atom feed, or nothing where there is no feed.

    Titled, because a page may one day carry more than one alternate and an untitled set of
    them is what a reader's feed autodiscovery cannot tell apart.
    """
    if page.feed is None:
        return ""
    return (
        f'\n<link rel="alternate" type="{ATOM_MEDIA_TYPE}" '
        f'title="{html.escape(page.title)}: recorded changes" href="/{html.escape(page.feed)}">'
    )


def _analytics_head() -> str:
    """The GA4 loader for ``<head>``, on its own line, or nothing when no ID is configured."""
    snippet = analytics.head_snippet()
    return f"\n{snippet}" if snippet else ""


def _shell(page: Page, *, canonical: str, origin: str, generated_at: str) -> str:
    prefix = _site_path_prefix(origin)
    card = social_card_url(origin)
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page.title)} | FHIR Scorecard</title>
<meta name="description" content="{html.escape(page.description)}">
{'<meta name="robots" content="noindex,follow">' if page.noindex else ""}
<link rel="canonical" href="{html.escape(canonical)}">{_feed_link(page)}
<meta property="og:title" content="{html.escape(page.title)}">
<meta property="og:description" content="{html.escape(page.description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{html.escape(canonical)}">
<meta property="og:site_name" content="FHIR Scorecard">
<meta property="og:locale" content="en_US">
<meta property="og:image" content="{html.escape(card)}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{SOCIAL_CARD_SIZE[0]}">
<meta property="og:image:height" content="{SOCIAL_CARD_SIZE[1]}">
<meta property="og:image:alt" content="{html.escape(SOCIAL_CARD_ALT)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html.escape(page.title)}">
<meta name="twitter:description" content="{html.escape(page.description)}">
<meta name="twitter:image" content="{html.escape(card)}">
<meta name="twitter:image:alt" content="{html.escape(SOCIAL_CARD_ALT)}">
<meta name="theme-color" content="#162e51">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/uswds/css/uswds.min.css">
<link rel="stylesheet" href="/assets/site.css">
<script src="/assets/uswds/js/uswds-init.min.js"></script>{_analytics_head()}
</head>
<body>
<a class="usa-skipnav" href="#content">Skip to main content</a>
<div class="usa-overlay"></div>
<header class="usa-header usa-header--basic">
<div class="usa-nav-container">
<div class="usa-navbar">
<div class="usa-logo"><em class="usa-logo__text">
<a href="/" title="FHIR Scorecard"><svg class="site-logo-mark" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" focusable="false"><rect width="28" height="28" rx="6" fill="#162e51"/><circle cx="8" cy="19.5" r="3.1" fill="#70e17b"/><circle cx="14" cy="13.5" r="3.1" fill="#73b3e7"/><circle cx="20" cy="7.5" r="3.1" fill="#ffbe2e"/></svg><span>FHIR Scorecard</span></a></em></div>
<button type="button" class="usa-menu-btn">Menu</button>
</div>
<nav aria-label="Primary navigation" class="usa-nav">
<button type="button" class="usa-nav__close">
<img src="/assets/uswds/img/usa-icons/close.svg" role="img" alt="Close"></button>
<ul class="usa-nav__primary usa-accordion">
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/#registry"><span>Registry</span></a></li>
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/how-we-grade/"><span>Method</span></a></li>
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/availability/"><span>Availability</span></a></li>
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/history/"><span>Record</span></a></li>
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/over-time/"><span>Over time</span></a></li>
<li class="usa-nav__primary-item"><a class="usa-nav-link" href="/dataset.csv"><span>Data</span></a></li>
<li class="usa-nav__primary-item">
<a class="usa-nav-link" href="/claim/"><span>Correct a record</span></a></li>
<li class="usa-nav__primary-item">
<a class="usa-nav-link" href="/bundle/"><span>Compliance bundle</span></a></li>
</ul>
</nav>
</div>
</header>
<main id="content" class="site-main">
{page.body}
</main>
<footer class="usa-footer usa-footer--slim">
<div class="grid-container usa-footer__return-to-top"><a href="#">Return to top</a></div>
<div class="usa-footer__primary-section">
<div class="usa-footer__primary-container grid-row">
<div class="mobile-lg:grid-col-8">
<nav class="usa-footer__nav" aria-label="Footer navigation">
<ul class="grid-row grid-gap">
<li class="mobile-lg:grid-col-auto usa-footer__primary-content">
<a class="usa-footer__primary-link" href="/how-we-grade/">Method</a></li>
<li class="mobile-lg:grid-col-auto usa-footer__primary-content">
<a class="usa-footer__primary-link" href="/scorecards.json">JSON</a></li>
<li class="mobile-lg:grid-col-auto usa-footer__primary-content">
<a class="usa-footer__primary-link" href="/dataset.csv">CSV</a></li>
<li class="mobile-lg:grid-col-auto usa-footer__primary-content">
<a class="usa-footer__primary-link" href="/privacy/">Privacy</a></li>
<li class="mobile-lg:grid-col-auto usa-footer__primary-content">
<a class="usa-footer__primary-link" href="https://github.com/ChelseaKR/fhir-scorecard">Source ↗</a></li>
</ul>
</nav>
</div>
</div>
</div>
<div class="usa-footer__secondary-section">
<div class="grid-container">
<p class="footer-tagline">Public evidence, plainly stated.</p>
<p>Generated {html.escape(generated_at)}. Only public <code>/metadata</code> and SMART discovery
documents are read; no patient data is ever accessed. An independent open-source project; not a
government website, and affiliated with no government agency.</p>
{analytics.footer_note()}
</div>
</div>
</footer>
<script src="/assets/uswds/js/uswds.min.js"></script>
</body>
</html>
"""
    if prefix:
        document = _INTERNAL_LINK.sub(rf'\1="{prefix}/', document)
    return document


def home_page(
    cards: list[Scorecard],
    origin: str,
    cohorts: tuple[Cohort, ...] = (),
    coverage_link: bool = False,
) -> Page:
    """Landing page: what this is, what it found, where to go next.

    ``cohorts`` is empty when no cohort curation was loaded, and the section is then omitted
    entirely rather than rendered as an empty list or a dead link. ``coverage_link`` is the
    same rule for the coverage tracker: the caller says whether that page was built, because a
    build without a frame does not have one and a link to it would be dead. It is not inferred
    from ``cohorts`` being non-empty, since the tracker also needs the frame CSV.
    """
    by_kind: dict[str, list[Scorecard]] = {}
    for c in cards:
        by_kind.setdefault(c.kind, []).append(c)
    sections = "".join(
        '<li class="category-card">'
        f'<div class="category-card-top"><span class="eyebrow">{len(v)} endpoints</span>'
        f'<span class="category-arrow" aria-hidden="true">↗</span></div>'
        f'<a href="/{_KIND_SLUGS.get(k, k)}/">'
        f"{html.escape(KIND_LABELS.get(k, k))}</a>"
        f"<p>{html.escape(_KIND_BLURBS.get(k, 'Publicly observable FHIR surfaces.'))}</p>"
        f'<div class="grade-distribution" aria-label="Grade distribution">'
        f"{_grade_counts(v)}</div></li>"
        for k, v in sorted(
            by_kind.items(),
            key=lambda kv: list(_KIND_SLUGS).index(kv[0]) if kv[0] in _KIND_SLUGS else 99,
        )
    )
    reachable = sum(card.reachable for card in cards)
    orgs = len({org_slug(card.name) for card in cards})
    # The legend only claims a state the snapshot actually contains.
    unobserved = sum(card.grade == NOT_OBSERVED for card in cards)
    unobserved_legend = (
        '<span><i class="signal-not-observed"></i>not observed</span>' if unobserved else ""
    )
    # "Not observed" and "did not answer" are nearly the same set and are not the same set. An
    # endpoint that answers /metadata with an empty body, or one another vantage reached without
    # retrieving the document, is reachable and still has nothing to grade. Counting all of the
    # ungraded as non-answering made the page assert, of that endpoint, both that it answered and
    # that it was "not counted as answering", in one sentence.
    silent = sum(1 for card in cards if card.grade == NOT_OBSERVED and not card.reachable)
    answered_ungraded = sum(1 for card in cards if card.grade == NOT_OBSERVED and card.reachable)
    # Both numbers above are said out loud, because one is a count of rows in the registry and
    # the other is a count of endpoints that answered a probe, and a reader takes the headline
    # away without reading the method page.
    registry_note = (
        f"{len(cards)} is how many endpoints the registry lists and this run graded; "
        f"{reachable} is how many answered /metadata during the run that generated this page, "
        "from at least one vantage."
        + (
            f" {silent} "
            + ("was" if silent == 1 else "were")
            + " not observed on this run and "
            + ("is" if silent == 1 else "are")
            + " not counted as answering."
            if silent
            else ""
        )
        + (
            f" {answered_ungraded} answered but returned nothing this run could grade, so "
            + ("it is" if answered_ungraded == 1 else "they are")
            + " counted as answering and still carr"
            + ("ies" if answered_ungraded == 1 else "y")
            + " no grade."
            if answered_ungraded
            else ""
        )
    )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "FHIR Scorecard",
        "description": (
            "Grades for publicly observable FHIR endpoint discovery surfaces across "
            "payers, providers, EHR vendors, and reference servers."
        ),
        "url": f"{origin}/",
        "license": "https://www.apache.org/licenses/LICENSE-2.0",
        "creator": {"@type": "Person", "name": "Chelsea Kelly-Reif"},
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"{origin}/scorecards.json",
            }
        ],
        "isAccessibleForFree": True,
    }
    cohort_section = ""
    coverage_note = (
        '<p><a href="/coverage/">How much of the federal marketplace frame has a publicly '
        "checkable endpoint</a>, with the organizations nobody has reviewed yet counted "
        "separately from the ones that publish nothing.</p>"
        if coverage_link
        else ""
    )
    if cohorts:
        items = "".join(
            f'<li><a href="/{c.cohort_id}/">{html.escape(c.name)}</a>: '
            f"{len(c.included)} of {len(c.members)} member organizations listed, the rest "
            "recorded with the reason they could not be</li>"
            for c in cohorts
        )
        cohort_section = f"""
<section class="home-section ruled-section">
<div class="section-heading"><div><p class="eyebrow">Coverage with a denominator</p>
<h2>Curated cohorts</h2></div>
<p>Fixed public rosters make missing endpoints visible instead of silently dropping them.</p></div>
<ul class="cards cohort-list">{items}</ul>
{coverage_note}
</section>
"""
    body = f"""
<header class="home-hero">
<div class="hero-copy">
<p class="eyebrow">Independent public-interest infrastructure</p>
<h1>What does a health API reveal <em>before</em> you log in?</h1>
<p class="lede">FHIR Scorecard reads the public discovery surface and turns it into evidence a
person can check: reachable or not, clearly documented or not, ready to interoperate or not.</p>
<div class="hero-actions">
<a class="usa-button" href="#registry">Explore the registry</a>
<a class="usa-link" href="/how-we-grade/">See exactly how grades work →</a>
</div>
</div>
<aside class="usa-summary-box scope-note" role="region" aria-label="Scope of measurement">
<div class="usa-summary-box__body">
<p class="eyebrow">The entire probe surface</p>
<div class="usa-summary-box__text">
<code>GET /metadata</code>
<code>GET /.well-known/smart-configuration</code>
<p>No login. No patient data. Two public documents.</p>
</div></div>
</aside>
</header>
<section class="signal-panel" aria-labelledby="signal-title">
<div class="signal-panel-heading"><div><p class="eyebrow">Latest registry snapshot</p>
<h2 id="signal-title">Every dot is a public endpoint.</h2></div>
<div class="signal-totals"><p><strong>{len(cards)}</strong> endpoints listed</p>
<p><strong>{reachable}</strong> answered on this run</p>
<p><strong>{orgs}</strong> organizations</p></div></div>
<p class="signal-note">{registry_note}</p>
<div class="signal-map">{_signal_map(cards)}</div>
<div class="signal-legend"><span>Grade</span>
<span><i class="signal-a"></i>A</span><span><i class="signal-b"></i>B</span>
<span><i class="signal-c"></i>C</span><span><i class="signal-d"></i>D</span>
<span><i class="signal-f"></i>F</span>{unobserved_legend}</div>
</section>
<section class="home-section" id="registry">
<div class="section-heading"><div><p class="eyebrow">Browse the evidence</p>
<h2>Different surfaces, different expectations</h2></div>
<p>Grades are only comparable within a category. Each surface is evaluated against the
implementation guides and public behavior that apply to it.</p></div>
<ul class="category-grid">{sections}</ul>
</section>
{cohort_section}
<section class="home-section evidence-callout">
<div><p class="eyebrow">What the curation found</p>
<h2>The URL is often the first barrier.</h2></div>
<div><p>Most payers with a base URL on a public developer portal expose a readable
CapabilityStatement, and most grade well. The difficult part is locating that URL at all: payer
base URLs are not predictable, so the registry is verified one portal at a time.</p>
<a class="usa-link" href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/payer-verifiability.md">Read
the research note and its corrections →</a></div>
</section>
<section class="home-section data-section">
<div class="section-heading"><div><p class="eyebrow">Open by construction</p>
<h2>Inspect the result—or the machinery.</h2></div></div>
<ul class="data-links">
<li><a href="/dataset.csv"><span>CSV</span>Flat dataset <b>↓</b></a></li>
<li><a href="/scorecards.json"><span>JSON</span>Full scorecards <b>↓</b></a></li>
<li><a href="/how-we-grade/"><span>METHOD</span>Finding codes <b>→</b></a></li>
<li><a href="https://github.com/ChelseaKR/fhir-scorecard"><span>SOURCE</span>Code and registry
<b>↗</b></a></li>
<li><a href="/claim/"><span>CORRECT</span>Add or dispute an endpoint
<b>→</b></a></li>
</ul>
</section>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Observational snapshots of public surfaces. Not audits, not rankings of care
quality, not statements about anyone's regulatory compliance.</p>
</div></div>
{json_ld(jsonld)}
"""
    return Page(
        path="",
        title="Public FHIR API grades for payers, providers, and EHR vendors",
        description=(
            "Independent grades for publicly observable FHIR endpoints. "
            "Reachability, capability transparency, and interoperability "
            "readiness, with spec citations and daily availability tracking."
        ),
        body=body,
        priority="1.0",
    )


_FINDING_DOCS = [
    (
        "R1",
        "Reachability",
        "Does /metadata answer with HTTP 2xx over HTTPS?",
        "An endpoint that cannot be reached is published with the reason stated, rather than "
        "dropping out of the dataset, and it is not graded. Causes are distinguished: DNS "
        "non-resolution, TLS failure, timeout, and refusal are different facts, and only some of "
        "them are about the endpoint. Reaching an endpoint from any vantage settles that it is up; "
        "failing from every vantage we have is reported as not reached from those vantages, which "
        "is a weaker statement than down.",
    ),
    (
        "NR",
        "Not observed",
        "What happens to the checks that could not run?",
        "When no vantage retrieved a document, the checks that read it do not run, score nothing, "
        "and publish nothing about the endpoint. The dimension shows no number, because zero is a "
        "measurement and this is the absence of one. An unreachable endpoint used to publish four "
        "findings describing what the payer had not declared, each with a spec citation, from a "
        "run that had received no document at all; the project's own history file disproved every "
        "one of them for the endpoint it happened to.",
    ),
    (
        "R2",
        "Response time",
        "How long did /metadata take?",
        "The median across the vantages that answered, which today share one network, so bands are "
        "deliberately coarse: full credit under 3s, partial under 8s. The raw milliseconds and the "
        "vantages are always shown. A network path difference must never flip a grade.",
    ),
    (
        "T0",
        "Not a CapabilityStatement",
        "The server answered, and what came back is not a CapabilityStatement. Now what?",
        "A server that answers /metadata with an OperationOutcome, a sign-in page, or a search "
        "Bundle has answered, so it is reachable, and it has not published the document FHIR R4 "
        "requires at that path. That is a finding about the endpoint and carries the whole "
        "transparency weight. It is deliberately one finding rather than four: the checks below "
        "read fields inside a CapabilityStatement, and reporting each of them as missing would "
        "describe a document nobody received.",
    ),
    (
        "T1",
        "FHIR version",
        "Does the server declare the release it intends to serve?",
        "Checked against the endpoint's registered intent, not against R4 unconditionally. An R5 "
        "server declaring 5.0.0 is correct. R4 is the default because the CMS interoperability "
        "rules require it of payer APIs.",
    ),
    (
        "T2",
        "Software identity",
        "Are software name and version declared?",
        "Knowing what is running is part of what a CapabilityStatement is for.",
    ),
    (
        "T3",
        "Declared breadth",
        "How many resource types are declared?",
        "Five or more earns full credit, and so does narrow-but-complete: two to four resource "
        "types with every one documenting its interactions. CMS Blue Button 2.0 is deliberately "
        "scoped to three, which is a design decision, not a deficiency.",
    ),
    (
        "T4",
        "Interaction coverage",
        "Do declared resources document their interactions?",
        "A resource listed with no interactions tells a client nothing it can act on.",
    ),
    (
        "I0",
        "No document to read profiles from",
        "What replaces I1 and I3 when the CapabilityStatement is unreadable?",
        "The interop counterpart of T0, and it carries exactly the points I1 and I3 would have "
        "carried, so an unreadable document can never move a letter in either direction. It "
        "exists because those two checks used to run against an empty parse result and publish "
        '"no profile canonical declared in rest.resource.supportedProfile, rest.resource.profile, '
        'instantiates, imports, or meta.profile" and "no OAuth security service declared" about a '
        "document that was never a CapabilityStatement. I1 names five elements as checked; none "
        "of them had been. SMART discovery is a separate retrieval and is still graded on its own "
        "evidence.",
    ),
    (
        "I1",
        "Interoperability profiles",
        "Are US Core, CARIN, or Da Vinci canonical URLs declared in any conformance element?",
        "Declared profiles are how a client knows which implementation guide the server follows. "
        "Five elements are read before anything is concluded: rest.resource.supportedProfile, "
        "rest.resource.profile, instantiates, imports, and meta.profile. The finding names the "
        "element the declaration was found in, or names all five when none carries one, because "
        '"no recognized interoperability profiles declared" used to be asserted after reading '
        "exactly one of them.",
    ),
    (
        "I4",
        "Named in prose only",
        "Does the document name a guide it does not declare?",
        "Worth zero points in either direction, and shown only when I1 found no declaration. Prose "
        'is not a conformance claim: a title reading "CARIN PatientAccess Implementation" tells a '
        "client nothing it can act on, which is exactly what supportedProfile is for. But a flat "
        "denial next to a document that says CARIN three times invites a reader to conclude "
        "something the document contradicts, so the note says what is actually the case and which "
        "element would fix it.",
    ),
    (
        "I2",
        "SMART discovery",
        "Is .well-known/smart-configuration present and complete?",
        "Not applicable to Provider Directory APIs, which are meant to be readable without "
        "authentication - required to be, for Medicare Advantage organizations under 42 CFR "
        "422.120 - and are not scored on an authorization surface they should not have.",
    ),
    (
        "I3",
        "Declared security",
        "Does the CapabilityStatement declare an OAuth/SMART service?",
        "Not applicable to Provider Directory APIs, for the same reason as I2.",
    ),
]


def privacy_page(origin: str) -> Page:
    """What reading this site sends anywhere, true for whether or not GA4 is configured."""
    return Page(
        path="privacy",
        title="Privacy: what this site measures",
        description=(
            "What Google Analytics records when you read these pages, what is switched off, "
            "and how to turn it off."
            if analytics.enabled()
            else "This site runs no analytics and sets no cookies."
        ),
        body=analytics.privacy_body(),
        changefreq="monthly",
        priority="0.3",
    )


def claim_page(origin: str) -> Page:
    body = """
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list"><li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li></ol></nav>
<p class="eyebrow">Participation and correction</p>
<h1>Add, correct, or remove an endpoint</h1>
<p class="lede">If we got something wrong about your organization, we would rather be corrected
than counted right.</p>
<div class="action-grid">
<section><span class="action-number">01</span><h2>We are missing your endpoint</h2>
<p>Payer FHIR base URLs are not predictable from company names, so this registry is built one
developer portal at a time and is certainly incomplete. Absence from this list means no public
base URL was found, not that no API exists.</p>
<p>We need the base URL and a link to where it is published, because
confirming the publisher is who the entry claims is what verification means here. Nothing is
added on an unverified submission.</p>
<p><a class="usa-button" href="https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=add-endpoint.yml">Tell
us about an endpoint</a></p></section>
<section><span class="action-number">02</span><h2>Something here is wrong</h2>
<p>This has happened. A live payer endpoint was recorded as dead because a middlebox on the
probing network intercepted TLS and the error surfaced as one uninformative word. Probing now
runs from more than one vantage, and reaching an endpoint from any of them settles that it is up.
<strong>That did not solve the problem above, and we should not imply it did.</strong> It removed
one shape of it &mdash; a fault local to a single host &mdash; and left the shape that matters to
you untouched.</p>
<p>What those vantages are, exactly: three GitHub-hosted runner images (Ubuntu, macOS, Windows).
They are three hosts on one provider's network, not three independent networks. They cannot catch
a source-address rule, bot filter, geo rule, rate limit, or TLS interception applied to that
provider's address space, because that hits all three at once and looks exactly like agreement.
So when all three fail, the page says the endpoint was not reached from that network on that day.
It does not say the endpoint is down, and it publishes no grade and no score &mdash; not a zero,
which would be a measurement we did not make.</p>
<p>This is not hypothetical, and it is not rare. On 12 September 2026, of the 14 endpoints here
that no vantage reached, re-probing by hand from an ordinary residential network found
<strong>4 that answered</strong> &mdash; two of them with an HTTP 2xx and a certificate that
verified, which is exactly the criterion all three runners had just failed. If your endpoint is
listed as not reached and you believe it is serving, you are very likely right.</p>
<p>You do not need to prove anything before asking us to look again.</p>
<p><a class="usa-button usa-button--outline" href="https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=remove-or-dispute.yml">Dispute
or remove an entry</a></p></section>
</div>
<section class="probe-contract"><div><p class="eyebrow">Our probe contract</p>
<h2>What we do to your servers</h2></div>
<p>We ask for two documents per endpoint per probing run: <code>/metadata</code> and
<code>/.well-known/smart-configuration</code>. Two documents is not always two requests, and the
honest bound is the one worth publishing: if your server answers with a redirect, following it
costs another GET. We follow at most three hops per document, so the worst case is four requests
per document and <strong>eight per endpoint per probing run</strong>. Three probing runs a day,
one per runner image, so the ceiling for a scheduled day is <strong>24 requests to any one
endpoint</strong>. Two per document, four per endpoint, is the normal case and the only one we
ask for; reaching 24 needs your own server to redirect three times on both paths. The run that
publishes this site adds none: it grades the documents those runs already retrieved.</p>
<p>Requests carry an identifying User-Agent with a contact address. We never authenticate, never
register for API access, never request patient data, and never probe beyond those two paths.
That scope is what is enforced on a redirect, on every hop: we follow a <code>Location</code>
only when it still names one of those two paths over HTTPS. The host may change &mdash; a payer
moving its FHIR service behind a CDN or a versioned path is ordinary, and refusing that would
break honest servers &mdash; but a redirect to anything else, or to plain HTTP, is refused, the
run records that it retrieved nothing, and your endpoint is published as <strong>not
observed</strong> rather than graded on a document we were pointed at.
Publishing is triggered on a schedule and by hand, not by commits, because a commit says nothing
about your endpoint and a commit-triggered rebuild once turned an ordinary working day into
dozens of requests to every endpoint here.</p></section>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Grades describe observable properties of public documents. They are not
audits, not compliance determinations, and not statements about care quality.</p>
</div></div>
"""
    return Page(
        path="claim",
        title="Add, correct, or remove a FHIR endpoint listing",
        description=(
            "Submit a public FHIR endpoint, correct a mistake, or ask to be "
            "removed. What this project does and does not do to your servers."
        ),
        body=body,
        changefreq="monthly",
        priority="0.6",
    )


def how_we_grade_page(origin: str) -> Page:
    rows = "".join(
        f'<section class="method-card" id="{code}"><span>{code}</span>'
        f"<div><h3>{html.escape(title)}</h3><p><strong>{html.escape(question)}</strong></p>"
        f"<p>{html.escape(detail)}</p></div></section>"
        for code, title, question, detail in _FINDING_DOCS
    )
    # Rendered from the weights `letter()` applies, never restated. See WEIGHTED_DIMENSIONS.
    bars = "\n".join(
        f"<p><span>{html.escape(title)}</span><strong>{round(weight * 100)}%</strong>"
        f'<i style="--weight:{round(weight * 100)}%"></i></p>'
        for _, title, weight in WEIGHTED_DIMENSIONS
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list"><li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li></ol></nav>
<p class="eyebrow">Transparent by design</p>
<h1>How we grade</h1>
<p class="lede">Every finding is deterministic, cites a spec clause, and can be explained in one
sentence. There is no model anywhere in the grading path.</p>
<section class="weight-panel"><div><p class="eyebrow">Weighted score</p><h2>Dimensions</h2>
<p>An endpoint no vantage could reach is not graded at all. It is published as <strong>not
observed</strong>, with the reason and the vantages that tried, because nothing else could be
observed and grading a document nobody retrieved would be an accusation this project cannot
support. <strong>F</strong> means the opposite: the endpoint answered, and what it declares falls
short across the checks below.</p></div><div class="weight-bars">
{bars}
</div></section>
<h2>Findings</h2>
<div class="method-list">{rows}</div>
<h2>Where the measurement comes from</h2>
<p>Every published grade reconciles probes from more than one vantage, on the rule that reaching
an endpoint from anywhere settles that it is up, while failing from one place settles nothing.
That rule exists because a live payer endpoint was once recorded as dead when a middlebox on the
probing network intercepted TLS.</p>
<p>What the vantages are, precisely: three GitHub-hosted runner images, Ubuntu, macOS and
Windows. They are three hosts on one provider's network. They are not three independent
networks, and nothing here calls them that. Three hosts catch a fault local to one host or one
trust store; they cannot catch a source-address rule, bot filter, geo rule, or rate limit
applied to that provider's address space, because such a rule reaches all three at once. So a
run where every vantage failed publishes that the endpoint was not reached from that network on
that day, and says why it cannot separate that from an endpoint being down. A genuinely
independent vantage is an open item, and until one exists this page will keep saying one
network.</p>
<p>Each vantage counts once. The publishing run makes no probe of its own; it grades the
documents the probing runs retrieved, which is why a scheduled day normally costs an endpoint six
requests &mdash; two documents from each of three probing runs. The published ceiling is higher,
because a redirect the server itself sends costs another GET: at most 8 per endpoint per run
and 24 per scheduled day. <a href="/claim/">Our probe contract</a> states the bound in full.</p>
<h2>Capability changes, and what is not one</h2>
<p>Each endpoint's declared capability is fingerprinted every run, and a difference is recorded
and shown but never scored: an upgrade is not a defect. One kind of difference is deliberately
not called a change. Where a single hostname sits in front of more than one backend, a daily
probe lands on whichever answers, and the declaration appears to move back and forth between two
values that were both already on record. Returning to a declaration this endpoint has served
before is counted and dated once as an alternation; only advancing to one never served before is
published as a capability change. Without that rule one such address produced eight "changed its
declared capability" entries in nine days and would have pushed every genuine change out of the
log it shares.</p>
<h2>What a grade is not</h2>
<p>It is not an audit, a compliance determination, or a statement about care quality. It
describes what a public document declared on a given day, from a handful of hosts on one
network. Grades are comparable within a category only.</p>
<h2>Corrections</h2>
<p>This project has made and published several measurement errors, including grading narrow APIs
as deficient, penalizing a public-by-design API for having no authorization surface, and
recording a live endpoint as dead because of TLS interception on the probing network. Each is
documented in the
<a href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/payer-verifiability.md">write-up</a>.
If something here is wrong, please
<a href="https://github.com/ChelseaKR/fhir-scorecard/issues">open an issue</a>.</p>
"""
    return Page(
        path="how-we-grade",
        title="How the FHIR endpoint grades are calculated",
        description=(
            "Every finding code, what it checks, the spec clause it cites, and "
            "the calibration decisions behind it."
        ),
        body=body,
        changefreq="monthly",
        priority="0.6",
    )


# ---------------------------------------------------------------------------
# Compliance report bundle: /bundle/, /bundle/setup/, /bundle/trust/
# ---------------------------------------------------------------------------

#: The plans sold today, in page order, and the only keys a card, an Offer, or a checkout link is
#: ever rendered for. The quarterly refresh plans are not here: nothing re-dispatches a
#: subscription's later quarters yet (docs/compliance-bundle-plan.md), and a plan.json entry for
#: one is ignored rather than sold.
BUNDLE_PLANS: tuple[str, ...] = ("bundle_15", "bundle_70")

#: Every Payment Link Stripe issues lives under this prefix. A checkout_url anywhere else is not
#: rendered as a link: a typo in plan.json must not send a buyer's card to an address nobody
#: checked.
CHECKOUT_URL_PREFIX = "https://buy.stripe.com/"

_SETUP_API_RE = re.compile(r"https://[a-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*/?")
_CURRENCY_RE = re.compile(r"[A-Z]{3}")
_CHECKOUT_PATH_RE = re.compile(r"[A-Za-z0-9_-]+")

_BUNDLE_FAQ: tuple[tuple[str, str], ...] = (
    (
        "What arrives?",
        "One self-contained evidence report per endpoint id you name, in a single archive, "
        "with a manifest that accounts for every id you asked for.",
    ),
    (
        "What branding does it carry?",
        "Your organization's name, logo, and accent color on every cover.",
    ),
    (
        "When does it arrive?",
        "Normally within the hour, always within two business days of payment. If it has not "
        "arrived by then, the purchase is refunded in full.",
    ),
    (
        "How long does the download link live?",
        "30 days. Each purchase is one archive, built from the scorecards published on the day "
        "you send the setup form.",
    ),
    (
        "What does a purchase change about a grade?",
        "Nothing. Grades, methodology, and which endpoints are listed are not for sale, and an "
        "endpoint's own evidence page stays free.",
    ),
    (
        "What does a purchase not buy?",
        "No influence over the scores. Grades, methodology, weights, and which endpoints are "
        "listed are never for sale, and the bundle's numbers are the same ones on the public "
        "site. Every report keeps its attribution to the open-source scorecard and cites the "
        "spec passage behind each finding.",
    ),
    (
        "Does a report ever touch patient data?",
        "No. Every report, free or paid, is built from two public discovery documents "
        "(CapabilityStatement and SMART configuration). No authenticated request is ever made, "
        "and no report this project produces has ever contained anything but publicly "
        "observable metadata.",
    ),
)


@dataclass(frozen=True)
class BundleOffer:
    """One plan a reader can buy right now, as :func:`bundle_offers` decided it."""

    key: str
    label: str
    price: int | float
    checkout_url: str


def bundle_setup_api(plan: Mapping[str, Any]) -> str | None:
    """The compliance-bundle API base the setup form posts to, or None.

    ``setup_api_base`` in plan.json is the ``api_base`` output of infra/compliance-bundle. It is
    rendered only when it is a plain https origin (optionally with a path), because the form
    sends a buyer's details to it.
    """
    value = plan.get("setup_api_base")
    if not isinstance(value, str) or not _SETUP_API_RE.fullmatch(value):
        return None
    return value.rstrip("/")


def _bundle_currency(plan: Mapping[str, Any]) -> str | None:
    value = plan.get("currency", "USD")
    return value if isinstance(value, str) and _CURRENCY_RE.fullmatch(value) else None


def _checkout_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(CHECKOUT_URL_PREFIX):
        return None
    rest = value[len(CHECKOUT_URL_PREFIX) :]
    return value if _CHECKOUT_PATH_RE.fullmatch(rest) else None


def _bundle_price(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if value > 0 else None


def bundle_offers(plan: Mapping[str, Any]) -> tuple[BundleOffer, ...]:
    """The plans a reader can buy right now: every price, Buy link, and Offer on /bundle/ comes
    from this and nothing else.

    It fails closed. Nothing is on sale unless *all* of these hold, and a plan is on sale only
    if its own two also hold:

    - ``paymentsAvailable`` is exactly ``true``: the owner's switch, flipped only after the
      checklist in docs/compliance-bundle-owner-steps.md;
    - ``setup_api_base`` names the deployed setup API: without it a buyer who pays lands on a
      form that cannot be sent, which is a charge for nothing;
    - ``currency`` is three capital letters;
    - the plan is one of :data:`BUNDLE_PLANS`, with a positive numeric ``price`` and a
      ``checkout_url`` that is a Stripe Payment Link.

    So no key means no checkout: until the owner's Stripe and AWS steps have produced a Payment
    Link and a setup API, the page shows no price and no link, whatever else plan.json says.
    """
    if plan.get("paymentsAvailable") is not True:
        return ()
    if bundle_setup_api(plan) is None or _bundle_currency(plan) is None:
        return ()
    products = plan.get("products")
    if not isinstance(products, Mapping):
        return ()
    offers: list[BundleOffer] = []
    for key in BUNDLE_PLANS:
        product = products.get(key)
        if not isinstance(product, Mapping):
            continue
        price = _bundle_price(product.get("price"))
        url = _checkout_url(product.get("checkout_url"))
        if price is None or url is None:
            continue
        label = product.get("label")
        offers.append(
            BundleOffer(
                key=key,
                label=label if isinstance(label, str) and label.strip() else key,
                price=price,
                checkout_url=url,
            )
        )
    return tuple(offers)


def _price_text(price: int | float) -> str:
    """``$249`` for a whole-dollar price, ``$249.50`` otherwise. Never ``$249.0``."""
    return f"${price:,.0f}" if float(price).is_integer() else f"${price:,.2f}"


def _bundle_offers_jsonld(
    origin: str, offers: Sequence[BundleOffer], currency: str
) -> dict[str, object] | None:
    """The AggregateOffer node, or None when nothing is on sale.

    A page published ahead of the payment rail must describe nothing it cannot do: an Offer for a
    checkout link that does not exist would tell a crawler, and a reader's browser extension
    that reads structured data, that a purchase can be made here today.
    """
    if not offers:
        return None
    # Kept as the plan file's own int or float, never routed through float(): str(float(249))
    # prints "249.0", which is not a price anyone charges.
    return {
        "@context": "https://schema.org",
        "@type": "Service",
        "@id": f"{origin}/bundle/#service",
        "url": f"{origin}/bundle/",
        "offers": {
            "@type": "AggregateOffer",
            "priceCurrency": currency,
            "lowPrice": str(min(o.price for o in offers)),
            "highPrice": str(max(o.price for o in offers)),
            "offerCount": len(offers),
            "offers": [
                {
                    "@type": "Offer",
                    "name": offer.label,
                    "price": str(offer.price),
                    "priceCurrency": currency,
                    "availability": "https://schema.org/InStock",
                    "url": offer.checkout_url,
                    "category": "one-time",
                }
                for offer in offers
            ],
        },
    }


def _bundle_plan_cards(plan: Mapping[str, Any], offers: Sequence[BundleOffer]) -> str:
    """One card per plan in :data:`BUNDLE_PLANS`. A plan on sale shows its price and a Buy link
    carrying the plan id and price the conversion events read (``assets/bundle.js``); any other
    shows its label and "Not yet available", with no price."""
    products = plan.get("products")
    products = products if isinstance(products, Mapping) else {}
    on_sale = {offer.key: offer for offer in offers}
    cards = []
    for key in BUNDLE_PLANS:
        offer = on_sale.get(key)
        product = products.get(key)
        if offer is None and not isinstance(product, Mapping):
            continue
        if offer is not None:
            label = offer.label
            body = (
                f'<p class="plan-price">{html.escape(_price_text(offer.price))}, paid once</p>'
                f'<a class="usa-button" href="{html.escape(offer.checkout_url)}" '
                f'data-bundle-plan="{html.escape(key)}" '
                f'data-bundle-price="{html.escape(str(offer.price))}">'
                f"Buy through Stripe</a>"
            )
        else:
            raw_label = product.get("label") if isinstance(product, Mapping) else None
            label = raw_label if isinstance(raw_label, str) and raw_label.strip() else key
            body = '<span class="usa-tag">Not yet available</span>'
        cards.append(
            '<section class="support-path">'
            '<p class="support-path-kicker">One time</p>'
            f"<h3>{html.escape(label)}</h3>{body}</section>"
        )
    return "".join(cards)


def bundle_page(origin: str, plan: Mapping[str, Any]) -> Page:
    """/bundle/: the compliance report bundle purchase page.

    Every price, Buy link, and Offer renders from ``data/bundle/plan.json`` at build time,
    through :func:`bundle_offers`, so opening the tier or changing an amount is a data change
    rather than a copy change. Until that function returns a plan, the page states that the
    tier is not open, shows no price, links to no checkout, emits no Offer structured data, and
    loads no conversion script.
    """
    offers = bundle_offers(plan)
    currency = _bundle_currency(plan) or "USD"
    max_endpoints = plan.get("max_endpoints", 70)
    notice = (
        ""
        if offers
        else '<div class="usa-alert usa-alert--info usa-alert--slim"><div class="usa-alert__body">'
        '<p class="usa-alert__text">This tier is not open yet. The page describes what it '
        "will do once it is; nothing on this page can charge anyone today.</p>"
        "</div></div>"
    )
    faq_items = "".join(
        f'<div class="method-card"><h3>{html.escape(q)}</h3><p>{html.escape(a)}</p></div>'
        for q, a in _BUNDLE_FAQ
    )
    faq_jsonld: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "@id": f"{origin}/bundle/#faq",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in _BUNDLE_FAQ
        ],
    }
    breadcrumb_jsonld: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{origin}/"},
            {"@type": "ListItem", "position": 2, "name": "Compliance report bundle"},
        ],
    }
    service_jsonld: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Service",
        "@id": f"{origin}/bundle/#service",
        "name": "Compliance report bundle",
        "url": f"{origin}/bundle/",
        "serviceType": "FHIR endpoint compliance evidence reports",
        "description": (
            "Branded, self-contained compliance evidence reports for a cohort of FHIR "
            "endpoints, built from the published scorecards and delivered as one archive."
        ),
        "provider": {"@type": "Organization", "name": "FHIR Scorecard", "url": f"{origin}/"},
        "audience": {
            "@type": "Audience",
            "audienceType": (
                "Compliance consultancies, health IT vendors, and state Medicaid or CHIP "
                "managed-care oversight teams tracking several payer FHIR endpoints"
            ),
        },
        "isRelatedTo": {
            "@id": f"{origin}/#dataset",
            "@type": "Dataset",
            "name": "FHIR endpoint evidence, free",
            "url": f"{origin}/",
        },
    }
    offers_jsonld = _bundle_offers_jsonld(origin, offers, currency)
    jsonld_blocks = json_ld(service_jsonld) + json_ld(breadcrumb_jsonld) + json_ld(faq_jsonld)
    if offers_jsonld is not None:
        jsonld_blocks += json_ld(offers_jsonld)
    # The conversion script ships only with something to buy: it announces the plans on sale and
    # a followed checkout link, and there is neither while the tier is closed.
    measure = '<script src="/assets/bundle.js" defer></script>' if offers else ""
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page"><span>Compliance report bundle</span></li>
</ol></nav>
<p class="eyebrow">For compliance teams tracking several endpoints</p>
<h1>Compliance evidence reports for every endpoint you track</h1>
<p class="lede">A compliance consultancy tracking several payer clients, a health IT vendor
benchmarking a client base, or a state Medicaid or CHIP office overseeing several managed-care
organizations can get every endpoint's compliance evidence report in one archive, with your
organization's own name, logo, and accent on each cover. Each file is the same self-contained
evidence document the site already ships for one endpoint; the bundle is the packaging.</p>
{notice}
<ul class="buy-terms">
<li><strong>What arrives?</strong> One self-contained evidence report per endpoint id you name,
up to {html.escape(str(max_endpoints))}, in a single archive, with a manifest that accounts for
every id you asked for.</li>
<li><strong>What branding does it carry?</strong> Your organization's name, logo, and accent
color on every cover.</li>
<li><strong>When does it arrive?</strong> Normally within the hour, always within two business
days of payment. If it has not arrived by then, the purchase is refunded in full.</li>
<li><strong>What does a purchase change about a grade?</strong> Nothing. See
<a href="/bundle/trust/">independence, data handling, and refunds</a>.</li>
</ul>
<h2>Plans and prices</h2>
<div class="support-paths" id="bundle-offers" data-currency="{html.escape(currency)}">
{_bundle_plan_cards(plan, offers)}</div>
<h2>What stays free</h2>
<p>Every endpoint's evidence page, the dataset, the API, and the CI action. Open any
<a href="/#registry">endpoint in the registry</a> and read its full findings for free, or run
the open-source <code>fhir-scorecard check</code> command yourself. Nothing is subtracted from
the free tier to create this bundle.</p>
<h2>How it works</h2>
<ol>
<li>Choose a plan above and pay through Stripe. FHIR Scorecard never sees your card.</li>
<li>Stripe sends you to a short form: organization name, an accent color, an optional logo, the
endpoint ids you want (up to the number the plan you bought covers), and where to send the
archive.</li>
<li>The reports render from the published scorecards and the download link goes to that
address, normally within the hour and always within two business days.</li>
<li>The link stays valid for 30 days. Stripe emails your receipt; reply to it with any
question about the order.</li>
</ol>
<h2>Questions</h2>
<div class="method-list method-list--faq">{faq_items}</div>
<p><a href="/bundle/trust/">Independence, data handling, and refunds →</a></p>
{jsonld_blocks}{measure}
"""
    return Page(
        path="bundle",
        title="Compliance report bundle for FHIR endpoint tracking",
        description=(
            "Branded compliance evidence reports for the FHIR endpoints you track, delivered "
            "as one archive. An endpoint's own evidence page stays free."
        ),
        body=body,
        priority="0.4",
    )


def bundle_setup_page(origin: str, plan: Mapping[str, Any] | None = None) -> Page:
    """/bundle/setup/: the post-checkout form. Never indexed, and says nothing to a reader who
    did not just arrive from a Stripe redirect carrying ``?session_id=...``.

    The form carries the setup API base as ``data-api`` whenever plan.json names one, even while
    the tier is closed to the public: the owner's test-mode purchase goes straight to a test
    Payment Link and lands here, and has to be able to send the form. Without an API base the
    script disables the form and says why (``assets/bundle-setup.js``).

    ``action`` and ``method`` are set although the script always sends the form itself. Without
    them a browser with scripting off would submit by GET to this same address, putting the
    buyer's organization, email, and endpoint list in the URL, where a history, a server log, or
    an analytics page view could keep them.
    """
    api = bundle_setup_api(plan or {})
    data_api = f' data-api="{html.escape(api)}"' if api else ""
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item"><a href="/bundle/" class="usa-breadcrumb__link"><span>Compliance report bundle</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page"><span>Set up your bundle</span></li>
</ol></nav>
<h1>Set up your compliance report bundle</h1>
<p class="lede">Thank you. Tell us what goes on the cover and which endpoints to include. The
reports render from the published scorecards and the download link goes to the address below,
normally within the hour and always within two business days.</p>
<noscript>
<p class="usa-alert usa-alert--warning usa-alert--slim"><span class="usa-alert__body">
This form is sent by a script, and scripting is switched off in this browser, so the button
below will not do anything. Nothing is lost: the payment is recorded. Open this same address
again in a browser with scripting enabled and fill the form there, promptly, because the two
business days are counted from when you paid, not from when this form is sent.
</span></p>
</noscript>
<form id="bundle-setup-form" class="usa-form usa-form--large submit-form" action="/bundle/setup/" method="post" novalidate{data_api}>
<div class="usa-form-group"><label class="usa-label" for="program_name">Organization name (required)</label>
<span class="usa-hint" id="program_name-hint">Printed as "Prepared by" on every cover.</span>
<input class="usa-input" id="program_name" name="program_name" type="text" required maxlength="120"
autocomplete="organization" aria-describedby="program_name-hint"
placeholder="e.g. Example Compliance Partners"></div>
<div class="usa-form-group"><label class="usa-label" for="accent">Accent color</label>
<span class="usa-hint" id="accent-hint">A #rrggbb value. It colors the cover band and section
rules only, never text.</span>
<input class="usa-input usa-input--small" id="accent" name="accent" type="text" maxlength="7"
pattern="#[0-9A-Fa-f]{{6}}" autocomplete="off" spellcheck="false" aria-describedby="accent-hint"
placeholder="#162e51"></div>
<div class="usa-form-group"><label class="usa-label" for="logo">Logo</label>
<span class="usa-hint" id="logo-hint">An https link to an SVG, PNG, or JPEG up to 512 KiB,
embedded so each report stays self-contained. Leave blank for no logo.</span>
<input class="usa-input" id="logo" name="logo" type="url" aria-describedby="logo-hint"
placeholder="https://.../logo.svg"></div>
<div class="usa-form-group"><label class="usa-label" for="endpoint_ids">Endpoint ids (required)</label>
<span class="usa-hint" id="endpoint_ids-hint">Comma or line separated, up to the number your
plan covers. The id is the last part of an endpoint's address on this site:
fhir.chelseakr.com/endpoint/<strong>cms-blue-button-2</strong>/. An id we do not track is listed
in the manifest with the reason, never silently dropped.</span>
<textarea class="usa-textarea" id="endpoint_ids" name="endpoint_ids" rows="6" required
aria-describedby="endpoint_ids-hint" placeholder="cms-blue-button-2, ..."></textarea></div>
<div class="usa-form-group"><label class="usa-label" for="deliver_to">Send the download link to</label>
<span class="usa-hint" id="deliver_to-hint">Leave blank to use the email you paid with.</span>
<input class="usa-input" id="deliver_to" name="deliver_to" type="email" autocomplete="email"
aria-describedby="deliver_to-hint"></div>
<button type="submit" class="usa-button">Build my reports</button>
<p id="bundle-setup-status" class="form-status" role="status" aria-live="polite"></p>
</form>
<p class="fineprint">Every report keeps its attribution to the open-source scorecard and its
numbers are the ones on the public site. Purchase buys no influence over grades, methodology,
or which endpoints are listed.</p>
<script src="/assets/bundle-setup.js" defer></script>
"""
    return Page(
        path="bundle/setup",
        title="Set up your compliance report bundle",
        description=(
            "After checkout: give the organization name, accent, logo, and the endpoint ids to "
            "include, and say where the download link should go."
        ),
        body=body,
        noindex=True,
        changefreq="yearly",
        priority="0.1",
    )


def bundle_trust_page(origin: str) -> Page:
    """/bundle/trust/: independence guarantee, data handling, and the refund commitment.

    Written as its own page rather than folded into /bundle/ because a compliance buyer's own
    reviewers are a realistic reader of this page specifically, and a page they can cite on its
    own is more useful to them than a paragraph inside a sales page. It is also the Terms of
    Service address the Stripe Payment Links' consent box points to.
    """
    # True only while the build carries GA4; with no measurement ID the bundle pages send nothing
    # and the page must not say otherwise.
    measured = (
        """<h2>What the pages measure</h2>
<p>The bundle pages count three steps toward a purchase in Google Analytics, as described on
<a href="/privacy/">the privacy page</a>: that the plans were shown, that a checkout link was
followed, and that Stripe sent a buyer back after paying. Each step carries the plan and its
price and nothing about the buyer. Nothing typed into the setup form, and not Stripe's order
reference itself, is ever sent.</p>"""
        if analytics.enabled()
        else ""
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item"><a href="/bundle/" class="usa-breadcrumb__link"><span>Compliance report bundle</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page"><span>Independence, data, and refunds</span></li>
</ol></nav>
<p class="eyebrow">What a purchase does and does not change</p>
<h1>Independence, data handling, and refunds</h1>
<h2>Independence</h2>
<p>Grades, methodology, weights, and which endpoints are listed are never for sale. A bundle's
numbers are read from the same published dataset a free visitor reads on the same day; nothing
about a purchase changes a finding, a score, or a grade for any endpoint, including one owned by
the buyer. Every report keeps its attribution to the open-source project and cites the FHIR R4
or SMART App Launch spec passage behind each finding, the same citations the free evidence page
carries.</p>
<h2>What is measured, and what never is</h2>
<p>Every report, free or paid, comes from exactly two public, unauthenticated documents: a
FHIR CapabilityStatement at <code>/metadata</code> and, where published, a SMART discovery
document at <code>/.well-known/smart-configuration</code>. No credential is ever used, no
patient data is ever requested, and no authenticated behavior is ever exercised. This project
has never held patient data and a purchase does not change that; see
<a href="/how-we-grade/">how we grade</a> for the full probe contract.</p>
<h2>What a purchase stores</h2>
<p>Fulfilling an order stores only what building and delivering it requires: the organization
name, accent color, and logo you supply for the cover, the endpoint ids you request, and the
email address the download link goes to. The form's details and the archive are deleted after
30 days, the life of the download link. The email address Stripe collected at checkout stays
with the order record, so that a later question or refund can be matched to the purchase.
Nothing about an order is published. Payment details are handled by Stripe and never reach this
project. A capability link is the credential for a download; there is no account and no
password.</p>
{measured}
<h2>The delivery promise</h2>
<p>The archive arrives normally within the hour and always within two business days of payment.
The date is computed from Stripe's own payment timestamp, stated to the buyer when the form is
sent, and is not re-estimated later. If the archive has not arrived by then, the purchase is
refunded in full.</p>
<h2>Refunds</h2>
<p>A purchase is refunded in full on request within 30 days, for any reason, and in full when the
delivery promise above is missed. Reply to the receipt Stripe emailed you. Refunds are issued by
hand through Stripe and reach the original card, normally within 5 to 10 business days.</p>
"""
    return Page(
        path="bundle/trust",
        title="Independence, data handling, and refunds for the compliance report bundle",
        description=(
            "What a compliance report bundle purchase does and does not change: independence "
            "from grading, what is stored, and the refund commitment."
        ),
        body=body,
        changefreq="monthly",
        priority="0.3",
    )
