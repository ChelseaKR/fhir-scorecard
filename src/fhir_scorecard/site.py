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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.grading import (
    NOT_OBSERVED,
    WEIGHTED_DIMENSIONS,
    Finding,
    Scorecard,
)

DEFAULT_ORIGIN = "https://fhir.chelseakr.com"

_PROGRAM_LABELS = {
    "medi-cal": "Medi-Cal managed care",
    "covered-ca": "Covered California",
    "tx-marketplace": "Texas individual marketplace (HealthCare.gov)",
    "fl-marketplace": "Florida individual marketplace (HealthCare.gov)",
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
        "a judgement about anyone's production systems."
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


def org_display_name(names: Sequence[str]) -> str:
    """An organization's name, taken as the leading words all of its endpoints share.

    An org page groups several endpoints, and naming it after whichever one happened to come first
    produced "Cigna Patient Access API" as the heading of a page that also lists Cigna's provider
    directory. The shared prefix is the part that is actually about the organization rather than
    about one of its surfaces: Cigna, Sharp Health Plan, HAPI FHIR public test server.

    Parenthetical qualifiers are dropped first, so "(R4)" and "(R5)" do not stop two releases of
    the same server from sharing a name. Slugs and URLs are unaffected, because ``org_slug``
    already strips the surface words this is removing.
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
    return " ".join(common) or " ".join(stripped[0])


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


def _grade_badge(grade: str) -> str:
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
    """Render every real endpoint as one labelled signal on the landing page."""
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


def _dimension_meter(title: str, score: int | None) -> str:
    """A dimension's score, or the absence of one.

    An unobserved dimension gets no bar and no number. Rendering it as 0 was the visual half of
    the same error: a bar at zero next to a named organization reads as a measurement.
    """
    if score is None:
        return (
            '<div class="dimension-meter dimension-meter-unscored">'
            f"<div><span>{html.escape(title)}</span><strong>not observed</strong></div>"
            f'<span class="meter meter-unscored" '
            f'aria-label="{html.escape(title)}: not observed on this run"></span></div>'
        )
    return (
        '<div class="dimension-meter">'
        f"<div><span>{html.escape(title)}</span><strong>{score}</strong></div>"
        f'<span class="meter" aria-label="{html.escape(title)}: {score} out of 100">'
        f'<span style="--score:{score}%"></span></span></div>'
    )


def _finding_mark(finding: Finding) -> tuple[str, str, str]:
    """Class, glyph, and screen-reader prefix for one finding.

    Three states, not two. A check that never ran is neither a pass nor a failure, and a ✗ beside
    it would publish the thing this project exists not to publish. A finding worth no points is
    not a verdict either: "not applicable to a Provider Directory API" and "this document names
    CARIN in prose" are notes, and a ✓ or a ✗ would both misread them.
    """
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
            state, glyph, prefix = _finding_mark(f)
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
            f"{_dimension_meter(dim.title, dim.score)}"
            f'<ul class="findings">{items}</ul></section>'
        )
    return "".join(out)


def endpoint_page(
    card: Scorecard,
    base_url: str,
    verified: str,
    origin: str,
    organization: tuple[str, str] | None = None,
) -> Page:
    """One endpoint's page.

    ``organization`` is ``(display name, slug)`` when this endpoint is one of several surfaces
    the same organization publishes, and ``None`` when it is the only one. It is what puts the
    /org/ page in the breadcrumb, and it is not decoration: organization pages were built and
    listed in the sitemap while no page on the site linked to one, so twelve published pages
    were reachable only by reading the sitemap. ``tests/test_site_audit.py`` now fails on an
    orphan, which is how that would be caught next time rather than by inspection.
    """
    kind_label = KIND_LABELS.get(card.kind, card.kind)
    summary = _status_words(card)
    unobserved = card.grade == NOT_OBSERVED
    dimensions = "".join(_dimension_meter(dim.title, dim.score) for dim in card.dimensions)
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
{_grade_badge(card.grade)}</div>
</header>
<section class="score-overview" aria-label="Dimension scores">{dimensions}</section>
<div class="evidence-grid">
<section class="evidence-card">
<p class="eyebrow">Observed surface</p>
<dl class="facts">
  <dt>Base URL</dt><dd><code>{html.escape(base_url)}</code></dd>
  <dt>Category</dt><dd>{html.escape(kind_label)}</dd>
  <dt>Availability</dt><dd>{html.escape(card.availability or "not yet recorded")}</dd>
  {
        f"<dt>Vantage agreement</dt><dd>{html.escape(card.vantage_note)}</dd>"
        if card.vantage_note
        else ""
    }
</dl>
</section>
<section class="evidence-card evidence-card-accent">
<p class="eyebrow">Interpretation</p>
<p>A grade describes two public discovery documents at one point in time. It does not inspect
patient data, authenticated behavior, or clinical quality.</p>
<a class="usa-link" href="/how-we-grade/">Read the scoring method →</a>
</section>
</div>
<h2>Findings</h2>
{_findings_html(card)}
{drift}
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
        f'<div>{_grade_badge(c.grade)}<span class="eyebrow">'
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
        f"{html.escape(c.name)}</a></td><td>{_grade_badge(c.grade)}</td>"
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
        f"<td>{_grade_badge(card.grade)}</td></tr>"
        for member in cohort.included
        for card in (cards[eid] for eid in member.endpoint_ids if eid in cards)
    )
    if not rows:
        return (
            '<tr><td colspan="5">No member of this cohort currently publishes a base URL '
            "this project could verify. That gap is the finding.</td></tr>"
        )
    return rows


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


def cohort_page(cohort: Cohort, cards: dict[str, Scorecard], origin: str) -> Page:
    """A curated cohort: who is in it, who could be listed, and who could not, with reasons.

    The exclusions table is not an appendix. For a cohort whose membership is public and finite,
    "this plan publishes no base URL an unregistered visitor can see" is as much a result as any
    grade, and omitting it would make the included list read as the whole cohort.
    """
    # Counted from the cards this run actually produced, not from the ids in the curation file:
    # a listed endpoint is a row somebody wrote down, and the number beside "answered" has to
    # come from a probe. They are usually the same number, and when they are not, the difference
    # is the interesting part.
    listed = [cards[eid] for m in cohort.included for eid in m.endpoint_ids if eid in cards]
    included_endpoints = len(listed)
    answered = sum(card.reachable for card in listed)
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
<p><strong>{included_endpoints}</strong><span>endpoints listed</span></p>
<p><strong>{answered}</strong><span>answered on this run</span></p>
</div>
<p>{len(cohort.included)} of {len(cohort.members)} member organizations publish a FHIR base URL
this project could verify from public documentation, which is a curation record with a date on
it, not a live figure; {included_endpoints} verified
{"endpoint is" if included_endpoints == 1 else "endpoints are"} listed below. Of those,
<strong>{answered} answered when this page was generated</strong>, which is the measured number:
it comes from this run's probes, and it moves when the endpoints do. The rest of the roster is
recorded with the reason it could not be listed, because for a cohort whose membership is public
and finite, the gap is itself a finding.</p>
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


def sitemap(pages: list[Page], origin: str) -> str:
    entries = "".join(
        f"<url><loc>{origin}/{p.path + '/' if p.path else ''}</loc>"
        f"<changefreq>{p.changefreq}</changefreq>"
        f"<priority>{p.priority}</priority></url>"
        for p in pages
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

    Every page links /assets/uswds/css/uswds.min.css and /assets/site.css, so the site stays
    fully self-contained: the design system is served from the same origin as the pages, at the
    version pinned in assets/uswds/VERSION.txt, and no page ever fetches a third-party
    subresource. The files ship inside the package so an installed copy builds the same site a
    checkout does.
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
<link rel="canonical" href="{html.escape(canonical)}">
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
<script src="/assets/uswds/js/uswds-init.min.js"></script>
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
probing network intercepted TLS and the error surfaced as one uninformative word. That is why
every published grade reconciles probes from more than one vantage, and why reaching an endpoint
from any of them settles that it is up.</p>
<p>What those vantages are, exactly: three GitHub-hosted runner images (Ubuntu, macOS, Windows).
They are three hosts on one provider's network, not three independent networks. They catch a
fault local to one host, which is the failure above; they cannot catch a source-address rule,
bot filter, geo rule, or rate limit your edge applies to that provider's address space, because
that hits all three at once. So when all three fail, the page says the endpoint was not reached
from that network on that day. It does not say the endpoint is down.</p>
<p>You do not need to prove anything before asking us to look again.</p>
<p><a class="usa-button usa-button--outline" href="https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=remove-or-dispute.yml">Dispute
or remove an entry</a></p></section>
</div>
<section class="probe-contract"><div><p class="eyebrow">Our probe contract</p>
<h2>What we do to your servers</h2></div>
<p>At most two unauthenticated GET requests per endpoint per probing run: <code>/metadata</code>
and <code>/.well-known/smart-configuration</code>. Three probing runs a day, one per runner
image, so a scheduled day is at most six requests to any one endpoint. The run that publishes
this site adds none: it grades the documents those runs already retrieved.</p>
<p>Requests carry an identifying User-Agent with a contact address. We never authenticate, never
register for API access, never request patient data, and never probe beyond those two paths.
If your server redirects one of them somewhere else, we do not follow: the redirect is refused,
the run records that it retrieved nothing, and your endpoint is published as <strong>not
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
documents the probing runs retrieved, which is also why a scheduled day costs an endpoint at
most six requests.</p>
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
