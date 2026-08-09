"""Static site generation: one indexable page per endpoint, per organization, and per kind.

Deterministic and dependency-free, same discipline as the grader. Every page is real HTML with
its own title, description, canonical URL, and structured data, because a single-page report is
not something a person can find from a search or link a colleague to.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.grading import Scorecard

DEFAULT_ORIGIN = "https://chelseakr.github.io/fhir-scorecard"

_PROGRAM_LABELS = {
    "medi-cal": "Medi-Cal managed care",
    "covered-ca": "Covered California",
}

_KIND_LABELS = {
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
    "payer": ("Patient Access APIs let a member pull their own claims and coverage data into an "
              "app they choose. These grades describe what each endpoint publicly declares."),
    "payer_provider_directory": ("Provider Directory APIs are required to be reachable without "
                                 "authentication, so they are not graded on an authorization "
                                 "surface they must not have."),
    "provider": "APIs published by health systems and provider organizations.",
    "ehr": ("Sandboxes published by EHR vendors for developers evaluating their platforms. "
            "Graded separately from payer APIs, which answer to different expectations."),
    "reference": ("Open test servers used by the FHIR community. Included as a baseline, not as "
                  "a judgement about anyone's production systems."),
}

_GRADE_WORDS = {
    "A": "declares a complete, interoperable public surface",
    "B": "declares a solid public surface with minor gaps",
    "C": "answers publicly but declares little about itself",
    "D": "answers publicly with substantial gaps",
    "F": "could not be reached from this vantage point",
}

_GRADE_COLORS = {
    "A": "#19734b",
    "B": "#00666a",
    "C": "#a35d00",
    "D": "#a43b2a",
    "F": "#8f2430",
}


@dataclass(frozen=True)
class Page:
    path: str          # site-relative directory, e.g. "endpoint/humana"
    title: str
    description: str
    body: str
    changefreq: str = "daily"
    priority: str = "0.5"


def org_slug(name: str) -> str:
    """Stable slug for an organization name, used for /org/<slug>/ pages."""
    cleaned = re.sub(r"\(.*?\)", " ", name.lower())
    cleaned = re.sub(r"\b(api|apis|patient access|provider directory|public|sandbox|preview|"
                     r"production|open|test server|server|inc|llc)\b", " ", cleaned)
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


def _json_ld(payload: dict[str, object]) -> str:
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
    return (f'<span class="grade grade-{grade.lower()}" '
            f'aria-label="Grade {html.escape(grade)}: {html.escape(word)}">{grade}</span>')


def _grade_counts(cards: Sequence[Scorecard]) -> str:
    """Compact, accessible distribution used beside category links."""
    counts = {grade: sum(card.grade == grade for card in cards) for grade in "ABCDF"}
    return "".join(
        f'<span class="grade-count grade-count-{grade.lower()}">'
        f'<strong>{count}</strong><span>{grade}</span></span>'
        for grade, count in counts.items() if count
    )


def _signal_map(cards: Sequence[Scorecard]) -> str:
    """Render every real endpoint as one labelled signal on the landing page."""
    rows: list[str] = []
    for kind in _KIND_SLUGS:
        group = [card for card in cards if card.kind == kind]
        if not group:
            continue
        signals = "".join(
            f'<a class="signal signal-{card.grade.lower()}" '
            f'href="/fhir-scorecard/endpoint/{html.escape(card.endpoint_id)}/" '
            f'title="{html.escape(card.name)}: grade {html.escape(card.grade)}">'
            f'<span class="sr-only">{html.escape(card.name)}: grade '
            f'{html.escape(card.grade)}</span></a>'
            for card in sorted(group, key=lambda card: card.name)
        )
        rows.append(
            '<div class="signal-row">'
            f'<a class="signal-label" href="/fhir-scorecard/{_KIND_SLUGS[kind]}/">'
            f'{html.escape(_KIND_LABELS[kind])}</a>'
            f'<span class="signal-count">{len(group):02d}</span>'
            f'<div class="signal-track">{signals}</div></div>'
        )
    return "".join(rows)


def _dimension_meter(title: str, score: int) -> str:
    return (
        '<div class="dimension-meter">'
        f'<div><span>{html.escape(title)}</span><strong>{score}</strong></div>'
        f'<span class="meter" aria-label="{html.escape(title)}: {score} out of 100">'
        f'<span style="--score:{score}%"></span></span></div>'
    )


def _findings_html(card: Scorecard) -> str:
    out: list[str] = []
    for dim in card.dimensions:
        items = "".join(
            f'<li class="finding {"ok" if f.ok else "no"}">'
            f'<span class="mark" aria-hidden="true">{"✓" if f.ok else "✗"}</span>'
            '<span class="finding-copy">'
            f'<span class="sr-only">{"Pass" if f.ok else "Needs attention"}: </span>'
            f'{html.escape(f.message)}</span>'
            '<span class="finding-links">'
            f'<a href="/fhir-scorecard/how-we-grade/#{html.escape(f.code)}">{f.code}</a>'
            f'<a href="{html.escape(f.citation)}" rel="nofollow">Spec ↗</a></span></li>'
            for f in dim.findings
        )
        out.append(
            '<section class="finding-group">'
            f'{_dimension_meter(dim.title, dim.score)}'
            f'<ul class="findings">{items}</ul></section>'
        )
    return "".join(out)


def endpoint_page(card: Scorecard, base_url: str, verified: str, origin: str) -> Page:
    kind_label = _KIND_LABELS.get(card.kind, card.kind)
    summary = _GRADE_WORDS.get(card.grade, "")
    dimensions = "".join(_dimension_meter(dim.title, dim.score) for dim in card.dimensions)
    drift = ""
    if card.drift_events:
        events = "".join(f"<li>{html.escape(e)}</li>" for e in card.drift_events)
        drift = f"<h3>Declared capability changes</h3><ul>{events}</ul>"
    elif card.observed_since:
        drift = (f"<p>Observed since {html.escape(card.observed_since)}; no changes to declared "
                 "capability recorded.</p>")

    jsonld = {
        "@context": "https://schema.org",
        "@type": "WebAPI",
        "name": card.name,
        "url": base_url,
        "documentation": f"{origin}/endpoint/{card.endpoint_id}/",
        "provider": {"@type": "Organization", "name": card.name},
        "isAccessibleForFree": True,
    }
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a> /
<a href="/fhir-scorecard/{_KIND_SLUGS.get(card.kind, "reference-servers")}/">
{html.escape(kind_label)}</a></nav>
<header class="endpoint-hero">
<div class="endpoint-heading">
<p class="eyebrow">Public surface / {html.escape(kind_label)}</p>
<h1>{html.escape(card.name)}</h1>
<p class="lede">This endpoint {html.escape(summary)}.</p>
</div>
<div class="hero-grade"><span>Current grade</span>{_grade_badge(card.grade)}</div>
</header>
<section class="score-overview" aria-label="Dimension scores">{dimensions}</section>
<div class="evidence-grid">
<section class="evidence-card">
<p class="eyebrow">Observed surface</p>
<dl class="facts">
  <dt>Base URL</dt><dd><code>{html.escape(base_url)}</code></dd>
  <dt>Category</dt><dd>{html.escape(kind_label)}</dd>
  <dt>Availability</dt><dd>{html.escape(card.availability or "not yet recorded")}</dd>
  {f'<dt>Vantage agreement</dt><dd>{html.escape(card.vantage_note)}</dd>'
   if card.vantage_note else ''}
</dl>
</section>
<section class="evidence-card evidence-card-accent">
<p class="eyebrow">Interpretation</p>
<p>A grade describes two public discovery documents at one point in time. It does not inspect
patient data, authenticated behavior, or clinical quality.</p>
<a class="text-link" href="/fhir-scorecard/how-we-grade/">Read the scoring method →</a>
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
<summary>Share this endpoint's grade</summary>
<div><img src="/fhir-scorecard/badge/{html.escape(card.endpoint_id)}.svg"
alt="FHIR Scorecard grade {html.escape(card.grade)} for {html.escape(card.name)}" width="126"
height="28">
<p>Link the badge back to this evidence page so readers can inspect the current findings.</p>
<code>&lt;a href="{html.escape(origin)}/endpoint/{html.escape(card.endpoint_id)}/"&gt;
&lt;img src="{html.escape(origin)}/badge/{html.escape(card.endpoint_id)}.svg"
alt="FHIR Scorecard grade {html.escape(card.grade)}"&gt;&lt;/a&gt;</code></div>
</details>
<p class="caveat">This is an observational snapshot of a public, unauthenticated surface. It is
not an audit, a ranking of care quality, or a statement about anyone's regulatory compliance.
See <a href="/fhir-scorecard/how-we-grade/">how we grade</a>.</p>
{_json_ld(jsonld)}
"""
    return Page(
        path=f"endpoint/{card.endpoint_id}",
        title=f"{card.name}: FHIR API grade {card.grade}",
        description=(f"{card.name} {summary}. Public FHIR CapabilityStatement graded on "
                     f"reachability, transparency, and interoperability readiness."),
        body=body,
        priority="0.8",
    )


def org_page(name: str, cards: list[Scorecard], origin: str) -> Page:
    rows = "".join(
        '<li class="surface-card">'
        f'<div>{_grade_badge(c.grade)}<span class="eyebrow">'
        f'{html.escape(_KIND_LABELS.get(c.kind, c.kind))}</span></div>'
        f'<a href="/fhir-scorecard/endpoint/{c.endpoint_id}/">{html.escape(c.name)}</a>'
        f'<p>{html.escape(_GRADE_WORDS.get(c.grade, ""))}.</p></li>'
        for c in sorted(cards, key=lambda c: c.name)
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<p class="eyebrow">Organization record</p>
<h1>{html.escape(name)}: public FHIR endpoints</h1>
<p class="lede">{len(cards)} publicly observable FHIR surfaces from this organization.</p>
<ul class="surface-grid">{rows}</ul>
<p class="caveat">Observational snapshots of public surfaces, not audits or compliance
determinations.</p>
"""
    return Page(
        path=f"org/{org_slug(name)}",
        title=f"{name}: public FHIR API grades",
        description=f"Grades for {len(cards)} publicly observable FHIR endpoints from {name}.",
        body=body,
        priority="0.7",
    )


def kind_page(kind: str, cards: list[Scorecard], origin: str) -> Page:
    label = _KIND_LABELS.get(kind, kind)
    blurb = _KIND_BLURBS.get(kind, "")
    rows = "".join(
        f'<tr><td><a href="/fhir-scorecard/endpoint/{c.endpoint_id}/">'
        f"{html.escape(c.name)}</a></td><td>{_grade_badge(c.grade)}</td>"
        f"<td>{html.escape(c.availability or 'not yet recorded')}</td></tr>"
        for c in sorted(cards, key=lambda c: (c.grade, c.name))
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<p class="eyebrow">Endpoint registry / {len(cards)} surfaces</p>
<h1>{html.escape(label)}</h1>
<p class="lede">{html.escape(blurb)}</p>
<div class="category-summary">
<p><strong>{sum(c.reachable for c in cards)}</strong><span>answer publicly</span></p>
<div class="grade-distribution" aria-label="Grade distribution">{_grade_counts(cards)}</div>
</div>
<div class="table-scroll" tabindex="0" role="region" aria-label="Graded endpoints">
<table class="registry-table"><caption>{len(cards)} graded {html.escape(label.lower())}</caption>
<thead><tr><th scope="col">Endpoint</th><th scope="col">Grade</th>
<th scope="col">Availability</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="caveat">Grades are comparable within this category only. A payer Patient Access API and
an EHR vendor sandbox answer to different implementation guides, so they are never ranked
against each other.</p>
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
        f'<tr><td>{html.escape(member.name)}</td>'
        f"<td>{html.escape(_programs_text(member))}</td>"
        f'<td><a href="/fhir-scorecard/endpoint/{card.endpoint_id}/">'
        f"{html.escape(card.name)}</a></td>"
        f"<td>{html.escape(_KIND_LABELS.get(card.kind, card.kind))}</td>"
        f"<td>{_grade_badge(card.grade)}</td></tr>"
        for member in cohort.included
        for card in (cards[eid] for eid in member.endpoint_ids if eid in cards)
    )
    if not rows:
        return ('<tr><td colspan="5">No member of this cohort currently publishes a base URL '
                "this project could verify. That gap is the finding.</td></tr>")
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
    included_endpoints = sum(len(m.endpoint_ids) for m in cohort.included)
    notes = "".join(f"<p>{html.escape(note)}</p>" for note in cohort.notes)
    sources = "".join(
        f'<li><a href="{html.escape(s.url)}" rel="nofollow">{html.escape(s.label)}</a> '
        f"(retrieved {html.escape(s.date)})</li>"
        for s in cohort.sources
    )
    sources_html = (f"<h2>Membership sources</h2><ul class=\"cards\">{sources}</ul>"
                    if sources else "")
    excluded_rows = _cohort_excluded_rows(cohort)
    excluded_html = ""
    if excluded_rows:
        excluded_html = f"""
<h2>Members reviewed and not listed</h2>
<p>Each exclusion records how far the review went, on what date, and where to check it. A review
that found nothing is not proof that nothing exists: if one of these plans publishes a base URL
we missed, please <a href="/fhir-scorecard/claim/">tell us</a>.</p>
<table><caption>Cohort members with no verifiable public endpoint</caption>
<thead><tr><th scope="col">Plan</th><th scope="col">Programs</th>
<th scope="col">Why it is not listed</th></tr></thead>
<tbody>{excluded_rows}</tbody></table>
"""
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<p class="eyebrow">Curated cohort / fixed public roster</p>
<h1>{html.escape(cohort.name)}</h1>
<p class="lede">{html.escape(cohort.description)}</p>
<div class="cohort-stats" aria-label="Cohort coverage">
<p><strong>{len(cohort.members)}</strong><span>organizations reviewed</span></p>
<p><strong>{len(cohort.included)}</strong><span>with verified public URLs</span></p>
<p><strong>{included_endpoints}</strong><span>graded endpoints</span></p>
</div>
<p>{len(cohort.included)} of {len(cohort.members)} member organizations publish a FHIR base URL
this project could verify from public documentation; {included_endpoints} verified
{"endpoint is" if included_endpoints == 1 else "endpoints are"} listed below. The rest are
recorded with the reason they could not be, because for a cohort whose membership is public and
finite, the gap is itself a finding.</p>
{notes}
{sources_html}
<h2>Listed endpoints</h2>
<div class="table-scroll" tabindex="0" role="region" aria-label="Verified cohort endpoints">
<table><caption>Verified public FHIR endpoints of cohort members</caption>
<thead><tr><th scope="col">Plan</th><th scope="col">Programs</th>
<th scope="col">Endpoint</th><th scope="col">Category</th><th scope="col">Grade</th></tr></thead>
<tbody>{_cohort_included_rows(cohort, cards)}</tbody></table></div>
<p>Grades are comparable within a category only: a Patient Access API and a Provider Directory
API answer to different expectations and are never ranked against each other.</p>
{excluded_html}
<p class="caveat">Observational snapshots of public discovery surfaces. Not audits, not
compliance determinations, and not statements about care quality. Publishing a base URL to
unregistered visitors is not required by any rule this project reads, and a plan that does not
is not violating anything; it is only not independently checkable from outside.</p>
"""
    return Page(
        path=cohort.cohort_id,
        title=f"{cohort.name}: public FHIR endpoint grades",
        description=(f"{cohort.description} {len(cohort.included)} of {len(cohort.members)} "
                     "member organizations publish a verifiable public FHIR endpoint."),
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
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>")


def robots(origin: str) -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n"


def status_badge(card: Scorecard) -> str:
    """A small, dependency-free SVG owners can embed while linking to the evidence page."""
    label = f"FHIR grade {card.grade}"
    title = f"{card.name}: {label}"
    color = _GRADE_COLORS.get(card.grade, "#435c68")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="126" height="28"
role="img" aria-labelledby="title desc">
<title id="title">{html.escape(title)}</title>
<desc id="desc">Current observational grade for this endpoint</desc>
<rect width="126" height="28" rx="3" fill="#102b3f"/>
<rect x="98" width="28" height="28" rx="3" fill="{color}"/>
<path fill="{color}" d="M98 0h3v28h-3z"/>
<text x="10" y="18" fill="#fff" font-family="Arial,sans-serif" font-size="11">FHIR grade</text>
<text x="112" y="19" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif"
font-size="14" font-weight="700">{html.escape(card.grade)}</text>
</svg>
"""


def write_page(out_dir: Path, page: Page, origin: str, generated_at: str) -> None:
    target = out_dir / page.path if page.path else out_dir
    target.mkdir(parents=True, exist_ok=True)
    canonical = f"{origin}/{page.path + '/' if page.path else ''}"
    (target / "index.html").write_text(
        _shell(page, canonical=canonical, origin=origin, generated_at=generated_at),
        encoding="utf-8")


_STYLE = """
:root {
  --ink: #102b3f;
  --ink-soft: #435c68;
  --paper: #f5f8f8;
  --paper-deep: #e8f0f1;
  --white: #ffffff;
  --line: #cbdadd;
  --teal: #007f82;
  --teal-dark: #00666a;
  --teal-wash: #d9eded;
  --pass: #19734b;
  --warn: #a35d00;
  --alert: #a43b2a;
  --fail: #8f2430;
  --shadow: 0 18px 50px rgb(16 43 63 / 8%);
  color-scheme: light;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper);
  font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
  font-size: 1rem;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
body::before {
  position: fixed;
  z-index: -1;
  inset: 0;
  content: "";
  opacity: .34;
  background-image:
    linear-gradient(rgb(16 43 63 / 3%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(16 43 63 / 3%) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: linear-gradient(to bottom, black, transparent 48rem);
}
a { color: var(--teal-dark); text-underline-offset: .16em; }
a:hover { color: var(--ink); }
a:focus-visible, [tabindex="0"]:focus-visible {
  outline: 3px solid #eea83b;
  outline-offset: 4px;
}
.skip-link {
  position: fixed;
  z-index: 20;
  top: .75rem;
  left: .75rem;
  padding: .65rem 1rem;
  color: var(--white);
  background: var(--ink);
  transform: translateY(-150%);
}
.skip-link:focus { transform: none; }
.site-header {
  border-bottom: 1px solid var(--line);
  background: rgb(245 248 248 / 92%);
  backdrop-filter: blur(12px);
}
.site-header-inner, .site-footer-inner, main {
  width: min(100% - 2rem, 74rem);
  margin-inline: auto;
}
.site-header-inner {
  display: flex;
  min-height: 4.75rem;
  align-items: center;
  justify-content: space-between;
  gap: 2rem;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: .75rem;
  color: var(--ink);
  font-size: .92rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-decoration: none;
  text-transform: uppercase;
}
.brand-mark {
  display: grid;
  width: 2.25rem;
  height: 2.25rem;
  place-items: center;
  color: var(--white);
  border-radius: 50%;
  background: var(--ink);
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-size: 1.35rem;
  font-weight: 400;
  letter-spacing: -.25em;
  text-indent: -.25em;
}
.site-nav { display: flex; align-items: center; gap: 1.5rem; }
.site-nav a {
  color: var(--ink-soft);
  font-size: .86rem;
  font-weight: 600;
  text-decoration: none;
}
.site-nav a:hover { color: var(--teal-dark); }
.site-nav .nav-action {
  padding: .48rem .8rem;
  border: 1px solid var(--ink);
  color: var(--ink);
}
main { min-height: 70vh; padding-block: 4rem 6rem; }
h1, h2, h3, p { margin-top: 0; }
h1, h2 {
  font-family: Charter, "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-weight: 500;
  letter-spacing: -.035em;
}
h1 {
  max-width: 18ch;
  margin-bottom: 1rem;
  font-size: clamp(2.6rem, 7vw, 5.4rem);
  line-height: .98;
}
h2 { font-size: clamp(1.8rem, 4vw, 3rem); line-height: 1.08; }
h3 { line-height: 1.25; }
em { color: var(--teal-dark); font-weight: 400; }
.eyebrow {
  margin-bottom: .65rem;
  color: var(--teal-dark);
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-size: .72rem;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.lede { max-width: 48rem; color: var(--ink-soft); font-size: clamp(1.15rem, 2vw, 1.35rem); }
code {
  padding: .08em .3em;
  border-radius: 2px;
  color: var(--ink);
  background: var(--paper-deep);
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-size: .88em;
  overflow-wrap: anywhere;
}
nav[aria-label="Breadcrumb"] {
  margin-bottom: 2.5rem;
  color: var(--ink-soft);
  font-size: .82rem;
}
.button {
  display: inline-block;
  padding: .78rem 1.15rem;
  border: 1px solid var(--teal-dark);
  color: var(--white);
  background: var(--teal-dark);
  font-size: .9rem;
  font-weight: 700;
  text-decoration: none;
}
.button:hover { color: var(--white); background: var(--ink); }
.button-secondary { color: var(--ink); background: transparent; border-color: var(--ink); }
.button-secondary:hover { color: var(--white); }
.text-link { font-size: .9rem; font-weight: 700; text-decoration-thickness: 1px; }
.grade {
  display: inline-grid;
  min-width: 2rem;
  min-height: 2rem;
  padding: .16rem .42rem;
  place-items: center;
  color: var(--white);
  border-radius: 50%;
  background: var(--ink-soft);
  font-family: ui-monospace, "SFMono-Regular", Consolas, monospace;
  font-weight: 800;
  line-height: 1;
}
.grade-a { background: var(--pass); }
.grade-b { background: var(--teal-dark); }
.grade-c { background: var(--warn); }
.grade-d { background: var(--alert); }
.grade-f { background: var(--fail); }
.home-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(17rem, .7fr);
  gap: clamp(2rem, 7vw, 7rem);
  align-items: end;
  padding: 2.5rem 0 5.5rem;
}
.home-hero h1 { max-width: 13ch; }
.hero-copy .lede { max-width: 42rem; }
.hero-actions {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  margin-top: 2rem;
  flex-wrap: wrap;
}
.scope-note {
  position: relative;
  padding: 2rem;
  border: 1px solid var(--line);
  background: var(--white);
  box-shadow: var(--shadow);
}
.scope-note::before {
  position: absolute;
  inset: 7px;
  border: 1px solid var(--paper-deep);
  content: "";
}
.scope-note > * { position: relative; }
.scope-note code { display: block; margin: .55rem 0; padding: .5rem .65rem; }
.scope-note p:last-child { margin: 1.4rem 0 0; color: var(--ink-soft); font-size: .88rem; }
.scope-icon { display: block; margin-bottom: 2rem; color: var(--teal); font-size: 2rem; }
.signal-panel { padding: clamp(1.5rem, 4vw, 3rem); color: var(--white); background: var(--ink); }
.signal-panel .eyebrow { color: #77d3d0; }
.signal-panel h2 { margin-bottom: 0; color: var(--white); }
.signal-panel-heading { display: flex; justify-content: space-between; gap: 2rem; }
.signal-totals { display: flex; gap: 1.6rem; align-items: end; }
.signal-totals p { margin: 0; color: #b8cbcf; font-size: .75rem; text-transform: uppercase; }
.signal-totals strong { display: block; color: var(--white); font-size: 1.25rem; }
.signal-map { margin-top: 3rem; }
.signal-row {
  display: grid;
  grid-template-columns: minmax(12rem, 1.25fr) 2rem 2fr;
  gap: 1rem;
  min-height: 3rem;
  align-items: center;
  border-top: 1px solid rgb(255 255 255 / 14%);
}
.signal-label { color: #d8e5e7; font-size: .82rem; text-decoration: none; }
.signal-label:hover { color: var(--white); }
.signal-count { color: #86a2a9; font-family: ui-monospace, monospace; font-size: .75rem; }
.signal-track { display: flex; align-items: center; gap: clamp(.35rem, 1vw, .8rem); }
.signal {
  display: block;
  width: .75rem;
  height: .75rem;
  border: 2px solid var(--ink);
  border-radius: 50%;
  outline: 1px solid currentcolor;
  transition: transform .15s ease, box-shadow .15s ease;
}
.signal:hover, .signal:focus {
  z-index: 1;
  transform: scale(1.65);
  box-shadow: 0 0 0 4px var(--ink);
}
.signal-a, .grade-count-a, .signal.signal-a { color: #67c28e; background: #67c28e; }
.signal-b, .grade-count-b, .signal.signal-b { color: #42b9bd; background: #42b9bd; }
.signal-c, .grade-count-c, .signal.signal-c { color: #efb04b; background: #efb04b; }
.signal-d, .grade-count-d, .signal.signal-d { color: #e27c58; background: #e27c58; }
.signal-f, .grade-count-f, .signal.signal-f { color: #d85b6b; background: #d85b6b; }
.signal-legend { display: flex; gap: 1rem; margin-top: 1.5rem; color: #a9bec3; font-size: .72rem; }
.signal-legend span { display: flex; align-items: center; gap: .35rem; }
.signal-legend i { width: .5rem; height: .5rem; border-radius: 50%; }
.home-section { padding: 6rem 0; }
.section-heading {
  display: grid;
  grid-template-columns: 1.15fr .85fr;
  gap: 4rem;
  align-items: end;
}
.section-heading h2 { max-width: 16ch; margin-bottom: 0; }
.section-heading > p { max-width: 33rem; margin-bottom: .3rem; color: var(--ink-soft); }
.category-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 3rem 0 0;
  padding: 1px;
  list-style: none;
  background: var(--line);
}
.category-card { min-height: 17rem; padding: 2rem; background: var(--white); }
.category-card-top { display: flex; justify-content: space-between; }
.category-arrow { color: var(--teal); }
.category-card > a {
  display: inline-block;
  color: var(--ink);
  font-size: 1.35rem;
  font-weight: 700;
}
.category-card > p { max-width: 34rem; color: var(--ink-soft); font-size: .9rem; }
.grade-distribution { display: flex; gap: .5rem; margin-top: 1.5rem; }
.grade-count {
  display: inline-flex;
  min-width: 2.2rem;
  overflow: hidden;
  align-items: stretch;
  color: var(--ink);
  border: 1px solid currentcolor;
  background: transparent;
  font-family: ui-monospace, monospace;
  font-size: .68rem;
}
.grade-count strong { padding: .2rem .35rem; color: currentcolor; background: var(--white); }
.grade-count span { padding: .2rem .3rem; color: var(--ink); background: currentcolor; }
.ruled-section { border-block: 1px solid var(--line); }
ul.cards, .surface-grid { margin: 2rem 0; padding: 0; list-style: none; }
ul.cards li { padding: 1rem 0; border-bottom: 1px solid var(--line); }
.cohort-list a { font-weight: 700; }
.evidence-callout { display: grid; grid-template-columns: 1fr 1fr; gap: 5rem; }
.evidence-callout h2 { max-width: 13ch; }
.evidence-callout > div:last-child { color: var(--ink-soft); font-size: 1.08rem; }
.data-section { padding-bottom: 1rem; }
.data-links { margin: 3rem 0 0; padding: 0; border-top: 1px solid var(--line); list-style: none; }
.data-links a {
  display: grid;
  grid-template-columns: 6rem 1fr auto;
  gap: 1rem;
  padding: 1.2rem .25rem;
  align-items: center;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
  font-weight: 700;
  text-decoration: none;
}
.data-links a:hover { padding-inline: .75rem; background: var(--white); }
.data-links span {
  color: var(--teal-dark);
  font-family: ui-monospace, monospace;
  font-size: .7rem;
}
.data-links b { font-size: 1.2rem; }
.endpoint-hero {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 3rem;
  align-items: end;
  padding-bottom: 3rem;
  border-bottom: 1px solid var(--line);
}
.endpoint-hero h1 { max-width: 20ch; font-size: clamp(2.5rem, 6vw, 4.7rem); }
.hero-grade { min-width: 8rem; padding: 1.4rem; text-align: center; background: var(--white); }
.hero-grade > span {
  display: block;
  margin-bottom: .8rem;
  color: var(--ink-soft);
  font-size: .72rem;
}
.hero-grade .grade { min-width: 4.3rem; min-height: 4.3rem; font-size: 2rem; }
.score-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 2rem 0; }
.dimension-meter { padding: 1rem; background: var(--white); }
.dimension-meter > div {
  display: flex;
  justify-content: space-between;
  gap: .75rem;
  font-size: .8rem;
}
.dimension-meter strong { font-family: ui-monospace, monospace; }
.meter { display: block; height: 3px; margin-top: .85rem; background: var(--paper-deep); }
.meter > span { display: block; width: var(--score); height: 100%; background: var(--teal); }
.evidence-grid {
  display: grid;
  grid-template-columns: 1.35fr .65fr;
  gap: 1rem;
  margin: 1rem 0 4rem;
}
.evidence-card { padding: 2rem; border: 1px solid var(--line); background: var(--white); }
.evidence-card-accent { background: var(--teal-wash); }
dl.facts {
  display: grid;
  grid-template-columns: minmax(7rem, auto) 1fr;
  gap: .65rem 1.5rem;
  margin: 0;
}
dl.facts dt { color: var(--ink-soft); font-size: .8rem; font-weight: 600; }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
.finding-group {
  margin: 1rem 0;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: var(--white);
}
.finding-group .dimension-meter { padding: 0 0 1.1rem; }
ul.findings { margin: 0; padding: 0; list-style: none; }
.finding {
  display: grid;
  grid-template-columns: 2rem minmax(0, 1fr) auto;
  gap: .8rem;
  padding: .85rem 0;
  align-items: start;
  border-top: 1px solid var(--paper-deep);
}
.mark {
  display: grid;
  width: 1.5rem;
  height: 1.5rem;
  place-items: center;
  border-radius: 50%;
  font-weight: 800;
}
li.no .mark { color: var(--fail); background: #f7e6e8; }
li.ok .mark { color: var(--pass); background: #e4f1e9; }
.finding-copy { font-size: .92rem; }
.finding-links {
  display: flex;
  gap: .8rem;
  font-family: ui-monospace, monospace;
  font-size: .7rem;
}
.verification {
  margin-top: 4rem;
  padding: 2rem;
  border-left: 4px solid var(--teal);
  background: var(--white);
}
.verification h2 { font-size: 1.7rem; }
.badge-embed { margin-top: 1rem; border: 1px solid var(--line); background: var(--white); }
.badge-embed summary { padding: 1rem 1.25rem; cursor: pointer; font-weight: 700; }
.badge-embed > div { padding: 0 1.25rem 1.25rem; }
.badge-embed img { display: block; }
.badge-embed p { margin: 1rem 0 .5rem; color: var(--ink-soft); font-size: .86rem; }
.badge-embed code { display: block; padding: .75rem; white-space: normal; }
.category-summary, .cohort-stats {
  display: flex;
  gap: 2rem;
  margin: 3rem 0 2rem;
  padding: 1.2rem 0;
  align-items: center;
  border-block: 1px solid var(--line);
}
.category-summary > p, .cohort-stats p { margin: 0; }
.category-summary > p strong, .cohort-stats strong { margin-right: .5rem; font-size: 1.6rem; }
.category-summary > p span, .cohort-stats span { color: var(--ink-soft); font-size: .78rem; }
.category-summary .grade-distribution { margin: 0 0 0 auto; }
.table-scroll { overflow-x: auto; }
table { width: 100%; margin: 1.5rem 0; border-collapse: collapse; background: var(--white); }
caption { padding: .75rem 0; color: var(--ink-soft); text-align: left; font-size: .8rem; }
th, td {
  padding: .85rem 1rem;
  border-bottom: 1px solid var(--paper-deep);
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--ink-soft);
  font-family: ui-monospace, monospace;
  font-size: .68rem;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.registry-table td:first-child a { color: var(--ink); font-weight: 700; }
.surface-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }
.surface-card { padding: 1.5rem; border: 1px solid var(--line); background: var(--white); }
.surface-card > div { display: flex; gap: 1rem; align-items: center; }
.surface-card .eyebrow { margin: 0; }
.surface-card > a { display: block; margin-top: 1.3rem; color: var(--ink); font-weight: 700; }
.surface-card p { margin: .5rem 0 0; color: var(--ink-soft); font-size: .86rem; }
.action-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  margin: 4rem 0;
  background: var(--line);
}
.action-grid section { padding: clamp(1.5rem, 4vw, 3rem); background: var(--white); }
.action-grid h2 { font-size: 1.8rem; }
.action-number { color: var(--teal); font-family: ui-monospace, monospace; }
.probe-contract, .weight-panel {
  display: grid;
  grid-template-columns: .8fr 1.2fr;
  gap: 4rem;
  padding: 3rem;
  color: var(--white);
  background: var(--ink);
}
.probe-contract .eyebrow, .weight-panel .eyebrow { color: #77d3d0; }
.probe-contract h2, .weight-panel h2 { color: var(--white); }
.probe-contract code { color: var(--white); background: rgb(255 255 255 / 12%); }
.weight-bars p {
  position: relative;
  display: grid;
  grid-template-columns: 1fr auto;
  padding-bottom: 1rem;
}
.weight-bars i {
  position: absolute;
  bottom: 0;
  left: 0;
  width: var(--weight);
  height: 3px;
  background: #77d3d0;
}
.method-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1px;
  margin: 2rem 0 4rem;
  background: var(--line);
}
.method-card {
  display: grid;
  grid-template-columns: 2.5rem 1fr;
  gap: 1rem;
  padding: 1.5rem;
  background: var(--white);
  scroll-margin-top: 2rem;
}
.method-card > span {
  color: var(--teal-dark);
  font-family: ui-monospace, monospace;
  font-weight: 800;
}
.method-card h3 { margin-bottom: .65rem; }
.method-card p { margin-bottom: .65rem; color: var(--ink-soft); font-size: .9rem; }
.method-card strong { color: var(--ink); }
.caveat {
  margin-top: 4rem;
  padding-top: 1rem;
  color: var(--ink-soft);
  border-top: 1px solid var(--line);
  font-size: .82rem;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.site-footer { color: #bad0d4; background: var(--ink); }
.site-footer-inner {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4rem;
  padding-block: 3rem;
}
.footer-brand { color: var(--white); font-family: Charter, Georgia, serif; font-size: 1.5rem; }
.site-footer p { max-width: 42rem; font-size: .82rem; }
.site-footer nav { display: flex; justify-content: flex-end; gap: 1.25rem; flex-wrap: wrap; }
.site-footer a { color: #d5e3e5; font-size: .8rem; }
@media (max-width: 760px) {
  .site-header-inner { min-height: 4rem; }
  .site-nav a:not(.nav-action) { display: none; }
  main { padding-block: 2.5rem 4rem; }
  .home-hero, .endpoint-hero, .evidence-grid, .section-heading, .evidence-callout,
  .probe-contract, .weight-panel, .site-footer-inner { grid-template-columns: 1fr; gap: 2rem; }
  .home-hero { padding-bottom: 3rem; }
  .signal-panel-heading { display: block; }
  .signal-totals { margin-top: 1.5rem; }
  .signal-row { grid-template-columns: minmax(8rem, 1fr) 1.5rem 1fr; }
  .category-grid, .surface-grid, .action-grid, .method-list { grid-template-columns: 1fr; }
  .score-overview { grid-template-columns: 1fr; }
  .category-summary, .cohort-stats { align-items: flex-start; flex-wrap: wrap; }
  .category-summary .grade-distribution { width: 100%; margin-left: 0; }
  .finding { grid-template-columns: 2rem 1fr; }
  .finding-links { grid-column: 2; }
  .data-links a { grid-template-columns: 4.5rem 1fr auto; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
@media print {
  .site-header, .site-footer, .hero-actions { display: none; }
  body { background: #fff; }
  body::before { display: none; }
  main { width: 100%; padding: 0; }
  a { color: inherit; }
}
"""


def _shell(page: Page, *, canonical: str, origin: str, generated_at: str) -> str:
    return f"""<!DOCTYPE html>
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
<meta name="theme-color" content="#102b3f">
<style>{_STYLE}</style>
</head>
<body>
<a class="skip-link" href="#content">Skip to content</a>
<header class="site-header">
<div class="site-header-inner">
<a class="brand" href="/fhir-scorecard/">
<span class="brand-mark" aria-hidden="true">+·</span><span>FHIR Scorecard</span></a>
<nav class="site-nav" aria-label="Primary">
<a href="/fhir-scorecard/#registry">Registry</a>
<a href="/fhir-scorecard/how-we-grade/">Method</a>
<a href="/fhir-scorecard/dataset.csv">Data</a>
<a class="nav-action" href="/fhir-scorecard/claim/">Correct a record</a>
</nav>
</div>
</header>
<main id="content">
{page.body}
</main>
<footer class="site-footer">
<div class="site-footer-inner"><div>
<p class="footer-brand">Public evidence, plainly stated.</p>
<p>Generated {html.escape(generated_at)}. Only public <code>/metadata</code> and SMART discovery
documents are read; no patient data is ever accessed.</p></div>
<nav aria-label="Footer">
<a href="/fhir-scorecard/how-we-grade/">Method</a>
<a href="/fhir-scorecard/scorecards.json">JSON</a>
<a href="/fhir-scorecard/dataset.csv">CSV</a>
<a href="https://github.com/ChelseaKR/fhir-scorecard">Source ↗</a>
</nav></div>
</footer>
</body>
</html>
"""


def home_page(cards: list[Scorecard], origin: str,
              cohorts: tuple[Cohort, ...] = ()) -> Page:
    """Landing page: what this is, what it found, where to go next.

    ``cohorts`` is empty when no cohort curation was loaded, and the section is then omitted
    entirely rather than rendered as an empty list or a dead link.
    """
    by_kind: dict[str, list[Scorecard]] = {}
    for c in cards:
        by_kind.setdefault(c.kind, []).append(c)
    sections = "".join(
        '<li class="category-card">'
        f'<div class="category-card-top"><span class="eyebrow">{len(v)} endpoints</span>'
        f'<span class="category-arrow" aria-hidden="true">↗</span></div>'
        f'<a href="/fhir-scorecard/{_KIND_SLUGS.get(k, k)}/">'
        f'{html.escape(_KIND_LABELS.get(k, k))}</a>'
        f'<p>{html.escape(_KIND_BLURBS.get(k, "Publicly observable FHIR surfaces."))}</p>'
        f'<div class="grade-distribution" aria-label="Grade distribution">'
        f'{_grade_counts(v)}</div></li>'
        for k, v in sorted(by_kind.items(), key=lambda kv: list(_KIND_SLUGS).index(kv[0])
                           if kv[0] in _KIND_SLUGS else 99)
    )
    reachable = sum(card.reachable for card in cards)
    orgs = len({org_slug(card.name) for card in cards})
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "FHIR Scorecard",
        "description": ("Grades for publicly observable FHIR endpoint discovery surfaces across "
                        "payers, providers, EHR vendors, and reference servers."),
        "url": f"{origin}/",
        "license": "https://www.apache.org/licenses/LICENSE-2.0",
        "creator": {"@type": "Person", "name": "Chelsea Kelly-Reif"},
        "distribution": [{
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": f"{origin}/scorecards.json",
        }],
        "isAccessibleForFree": True,
    }
    cohort_section = ""
    if cohorts:
        items = "".join(
            f'<li><a href="/fhir-scorecard/{c.cohort_id}/">{html.escape(c.name)}</a>: '
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
<a class="button" href="#registry">Explore the registry</a>
<a class="text-link" href="/fhir-scorecard/how-we-grade/">See exactly how grades work →</a>
</div>
</div>
<aside class="scope-note" aria-label="Scope of measurement">
<span class="scope-icon" aria-hidden="true">{{ }}</span>
<p class="eyebrow">The entire probe surface</p>
<code>GET /metadata</code>
<code>GET /.well-known/smart-configuration</code>
<p>No login. No patient data. Two public documents.</p>
</aside>
</header>
<section class="signal-panel" aria-labelledby="signal-title">
<div class="signal-panel-heading"><div><p class="eyebrow">Latest registry snapshot</p>
<h2 id="signal-title">Every dot is a public endpoint.</h2></div>
<div class="signal-totals"><p><strong>{len(cards)}</strong> endpoints</p>
<p><strong>{reachable}</strong> answering</p>
<p><strong>{orgs}</strong> organizations</p></div></div>
<div class="signal-map">{_signal_map(cards)}</div>
<div class="signal-legend"><span>Grade</span>
<span><i class="signal-a"></i>A</span><span><i class="signal-b"></i>B</span>
<span><i class="signal-c"></i>C</span><span><i class="signal-d"></i>D</span>
<span><i class="signal-f"></i>F</span></div>
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
<a class="text-link" href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/payer-verifiability.md">Read
the research note and its corrections →</a></div>
</section>
<section class="home-section data-section">
<div class="section-heading"><div><p class="eyebrow">Open by construction</p>
<h2>Inspect the result—or the machinery.</h2></div></div>
<ul class="data-links">
<li><a href="/fhir-scorecard/dataset.csv"><span>CSV</span>Flat dataset <b>↓</b></a></li>
<li><a href="/fhir-scorecard/scorecards.json"><span>JSON</span>Full scorecards <b>↓</b></a></li>
<li><a href="/fhir-scorecard/how-we-grade/"><span>METHOD</span>Finding codes <b>→</b></a></li>
<li><a href="https://github.com/ChelseaKR/fhir-scorecard"><span>SOURCE</span>Code and registry
<b>↗</b></a></li>
<li><a href="/fhir-scorecard/claim/"><span>CORRECT</span>Add or dispute an endpoint
<b>→</b></a></li>
</ul>
</section>
<p class="caveat">Observational snapshots of public surfaces. Not audits, not rankings of care
quality, not statements about anyone's regulatory compliance.</p>
{_json_ld(jsonld)}
"""
    return Page(path="", title="Public FHIR API grades for payers, providers, and EHR vendors",
                description=("Independent grades for publicly observable FHIR endpoints. "
                             "Reachability, capability transparency, and interoperability "
                             "readiness, with spec citations and daily availability tracking."),
                body=body, priority="1.0")


_FINDING_DOCS = [
    ("R1", "Reachability", "Does /metadata answer with HTTP 2xx over HTTPS?",
     "An endpoint that cannot be reached scores F with the reason stated, rather than dropping "
     "out of the dataset. Causes are distinguished: DNS non-resolution, TLS failure, timeout, "
     "and refusal are different facts, and only some of them are about the endpoint."),
    ("R2", "Response time", "How long did /metadata take?",
     "Measured from a single vantage point per run, so bands are deliberately coarse: full "
     "credit under 3s, partial under 8s. The raw milliseconds and the vantage are always shown. "
     "A network path difference must never flip a grade."),
    ("T1", "FHIR version", "Does the server declare the release it intends to serve?",
     "Checked against the endpoint's registered intent, not against R4 unconditionally. An R5 "
     "server declaring 5.0.0 is correct. R4 is the default because the CMS interoperability "
     "rules require it of payer APIs."),
    ("T2", "Software identity", "Are software name and version declared?",
     "Knowing what is running is part of what a CapabilityStatement is for."),
    ("T3", "Declared breadth", "How many resource types are declared?",
     "Five or more earns full credit, and so does narrow-but-complete: two to four resource "
     "types with every one documenting its interactions. CMS Blue Button 2.0 is deliberately "
     "scoped to three, which is a design decision, not a deficiency."),
    ("T4", "Interaction coverage", "Do declared resources document their interactions?",
     "A resource listed with no interactions tells a client nothing it can act on."),
    ("I1", "Interoperability profiles", "Are US Core, CARIN, or Da Vinci profiles declared?",
     "Declared profiles are how a client knows which implementation guide the server follows."),
    ("I2", "SMART discovery", "Is .well-known/smart-configuration present and complete?",
     "Not applicable to Provider Directory APIs, which are required to be reachable without "
     "authentication and are not scored on an authorization surface they must not have."),
    ("I3", "Declared security", "Does the CapabilityStatement declare an OAuth/SMART service?",
     "Not applicable to Provider Directory APIs, for the same reason as I2."),
]


def claim_page(origin: str) -> Page:
    body = """
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
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
<p><a class="button" href="https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=add-endpoint.yml">Tell
us about an endpoint</a></p></section>
<section><span class="action-number">02</span><h2>Something here is wrong</h2>
<p>This has happened. A live payer endpoint was recorded as dead because a middlebox on the
probing network intercepted TLS and the error surfaced as one uninformative word. That is why
probing now runs from several vantages and why reaching an endpoint from anywhere settles that
it is up.</p>
<p>You do not need to prove anything before asking us to look again.</p>
<p><a class="button button-secondary" href="https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=remove-or-dispute.yml">Dispute
or remove an entry</a></p></section>
</div>
<section class="probe-contract"><div><p class="eyebrow">Our probe contract</p>
<h2>What we do to your servers</h2></div>
<p>At most two unauthenticated GET requests per run: <code>/metadata</code> and
<code>/.well-known/smart-configuration</code>. Requests carry an identifying User-Agent with a
contact address. We never authenticate, never register for API access, never request patient
data, and never probe beyond those two paths.</p></section>
<p class="caveat">Grades describe observable properties of public documents. They are not
audits, not compliance determinations, and not statements about care quality.</p>
"""
    return Page(path="claim",
                title="Add, correct, or remove a FHIR endpoint listing",
                description=("Submit a public FHIR endpoint, correct a mistake, or ask to be "
                             "removed. What this project does and does not do to your servers."),
                body=body, changefreq="monthly", priority="0.6")


def how_we_grade_page(origin: str) -> Page:
    rows = "".join(
        f'<section class="method-card" id="{code}"><span>{code}</span>'
        f'<div><h3>{html.escape(title)}</h3><p><strong>{html.escape(question)}</strong></p>'
        f'<p>{html.escape(detail)}</p></div></section>'
        for code, title, question, detail in _FINDING_DOCS
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<p class="eyebrow">Transparent by design</p>
<h1>How we grade</h1>
<p class="lede">Every finding is deterministic, cites a spec clause, and can be explained in one
sentence. There is no model anywhere in the grading path.</p>
<section class="weight-panel"><div><p class="eyebrow">Weighted score</p><h2>Dimensions</h2>
<p>An unreachable endpoint is an F regardless of anything else, because nothing else could be
observed.</p></div><div class="weight-bars">
<p><span>Reachability</span><strong>35%</strong><i style="--weight:35%"></i></p>
<p><span>Capability transparency</span><strong>35%</strong><i style="--weight:35%"></i></p>
<p><span>Interop readiness</span><strong>30%</strong><i style="--weight:30%"></i></p>
</div></section>
<h2>Findings</h2>
<div class="method-list">{rows}</div>
<h2>What a grade is not</h2>
<p>It is not an audit, a compliance determination, or a statement about care quality. It
describes what a public document declared on a given day, from one vantage point. Grades are
comparable within a category only.</p>
<h2>Corrections</h2>
<p>This project has made and published several measurement errors, including grading narrow APIs
as deficient, penalizing a public-by-design API for having no authorization surface, and
recording a live endpoint as dead because of TLS interception on the probing network. Each is
documented in the
<a href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/payer-verifiability.md">write-up</a>.
If something here is wrong, please
<a href="https://github.com/ChelseaKR/fhir-scorecard/issues">open an issue</a>.</p>
"""
    return Page(path="how-we-grade",
                title="How the FHIR endpoint grades are calculated",
                description=("Every finding code, what it checks, the spec clause it cites, and "
                             "the calibration decisions behind it."),
                body=body, changefreq="monthly", priority="0.6")
