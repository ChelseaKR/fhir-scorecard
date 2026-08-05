"""Static site generation: one indexable page per endpoint, per organization, and per kind.

Deterministic and dependency-free, same discipline as the grader. Every page is real HTML with
its own title, description, canonical URL, and structured data, because a single-page report is
not something a person can find from a search or link a colleague to.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from fhir_scorecard.grading import Scorecard

DEFAULT_ORIGIN = "https://chelseakr.github.io/fhir-scorecard"

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
    return f'<span class="grade grade-{grade.lower()}">{grade}</span>'


def _findings_html(card: Scorecard) -> str:
    out: list[str] = []
    for dim in card.dimensions:
        items = "".join(
            f'<li class="{"ok" if f.ok else "no"}">'
            f'<span class="mark" aria-hidden="true">{"✓" if f.ok else "✗"}</span> '
            f'<span class="sr-only">{"pass" if f.ok else "fail"}: </span>'
            f'{html.escape(f.message)} '
            f'<a href="/fhir-scorecard/how-we-grade/#{html.escape(f.code)}">{f.code}</a> '
            f'<a href="{html.escape(f.citation)}" rel="nofollow">spec</a></li>'
            for f in dim.findings
        )
        out.append(f"<h3>{html.escape(dim.title)}: {dim.score}/100</h3><ul>{items}</ul>")
    return "".join(out)


def endpoint_page(card: Scorecard, base_url: str, verified: str, origin: str) -> Page:
    kind_label = _KIND_LABELS.get(card.kind, card.kind)
    summary = _GRADE_WORDS.get(card.grade, "")
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
<h1>{html.escape(card.name)} {_grade_badge(card.grade)}</h1>
<p class="lede">This endpoint {html.escape(summary)}.</p>
<dl class="facts">
  <dt>Base URL</dt><dd><code>{html.escape(base_url)}</code></dd>
  <dt>Category</dt><dd>{html.escape(kind_label)}</dd>
  <dt>Availability</dt><dd>{html.escape(card.availability or "not yet recorded")}</dd>
</dl>
{_findings_html(card)}
{drift}
<h3>How this entry was verified</h3>
<p>{html.escape(verified)}</p>
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
        f'<li>{_grade_badge(c.grade)} <a href="/fhir-scorecard/endpoint/{c.endpoint_id}/">'
        f"{html.escape(c.name)}</a> ({html.escape(_KIND_LABELS.get(c.kind, c.kind))})</li>"
        for c in sorted(cards, key=lambda c: c.name)
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<h1>{html.escape(name)}: public FHIR endpoints</h1>
<p class="lede">{len(cards)} publicly observable FHIR surfaces from this organization.</p>
<ul class="cards">{rows}</ul>
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
<h1>{html.escape(label)}</h1>
<p class="lede">{html.escape(blurb)}</p>
<table><caption>{len(cards)} graded {html.escape(label.lower())}</caption>
<thead><tr><th scope="col">Endpoint</th><th scope="col">Grade</th>
<th scope="col">Availability</th></tr></thead>
<tbody>{rows}</tbody></table>
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


def write_page(out_dir: Path, page: Page, origin: str, generated_at: str) -> None:
    target = out_dir / page.path if page.path else out_dir
    target.mkdir(parents=True, exist_ok=True)
    canonical = f"{origin}/{page.path + '/' if page.path else ''}"
    (target / "index.html").write_text(
        _shell(page, canonical=canonical, origin=origin, generated_at=generated_at),
        encoding="utf-8")


_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, sans-serif; max-width: 48rem; margin: 0 auto;
       padding: 1.5rem 1rem 4rem; line-height: 1.55; color: #1a1a1a; background: #fff; }
a { color: #0b5cad; }
nav[aria-label="Breadcrumb"] { font-size: .9rem; margin-bottom: .5rem; }
h1 { line-height: 1.2; }
.lede { font-size: 1.1rem; color: #333; }
.grade { display: inline-block; min-width: 1.7em; text-align: center; border-radius: 4px;
         padding: 0 .35em; color: #fff; background: #555; font-weight: 700; }
.grade-a { background: #14691f; } .grade-b { background: #3f7d20; }
.grade-c { background: #8a5a00; } .grade-d { background: #a8421f; }
.grade-f { background: #96110f; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
caption { text-align: left; font-weight: 600; margin-bottom: .5rem; }
th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #e3e3e3;
         vertical-align: top; }
dl.facts { display: grid; grid-template-columns: auto 1fr; gap: .3rem .9rem; }
dl.facts dt { font-weight: 600; }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
ul.cards { list-style: none; padding: 0; }
ul.cards li { padding: .4rem 0; border-bottom: 1px solid #eee; }
li.no .mark { color: #96110f; } li.ok .mark { color: #14691f; }
.caveat { font-size: .9rem; color: #555; border-top: 1px solid #e3e3e3; padding-top: .8rem;
          margin-top: 2rem; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0);
           white-space: nowrap; }
footer { margin-top: 3rem; font-size: .9rem; color: #555; }
@media (prefers-color-scheme: dark) {
  body { background: #131313; color: #e9e9e9; }
  a { color: #79b8ff; }
  .lede { color: #cfcfcf; }
  th, td, ul.cards li { border-color: #333; }
  .caveat { color: #bbb; border-color: #333; }
  footer { color: #bbb; }
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
<style>{_STYLE}</style>
</head>
<body>
<main>
{page.body}
</main>
<footer>
<p>Generated {html.escape(generated_at)}. Only public <code>/metadata</code> and SMART discovery
documents are read; no patient data is ever accessed.
<a href="https://github.com/ChelseaKR/fhir-scorecard">Source and methodology</a>.</p>
</footer>
</body>
</html>
"""


def home_page(cards: list[Scorecard], origin: str) -> Page:
    """Landing page: what this is, what it found, where to go next."""
    by_kind: dict[str, list[Scorecard]] = {}
    for c in cards:
        by_kind.setdefault(c.kind, []).append(c)
    sections = "".join(
        f'<li><a href="/fhir-scorecard/{_KIND_SLUGS.get(k, k)}/">'
        f"{html.escape(_KIND_LABELS.get(k, k))}</a> ({len(v)})</li>"
        for k, v in sorted(by_kind.items(), key=lambda kv: list(_KIND_SLUGS).index(kv[0])
                           if kv[0] in _KIND_SLUGS else 99)
    )
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
    body = f"""
<h1>FHIR Scorecard</h1>
<p class="lede">Can you check a health payer's FHIR API without asking permission first? For most
of them, no. This grades the ones you can.</p>
<p>Every grade here comes from two public documents: the
<code>CapabilityStatement</code> at <code>/metadata</code> that every FHIR server must expose,
and the SMART discovery document. Nothing authenticated, no patient data, one request each per
day. Findings cite the spec clause they rest on, and an endpoint that cannot be reached scores F
with a stated reason rather than disappearing from the data.</p>
<h2>Browse by category</h2>
<ul class="cards">{sections}</ul>
<p>Grades are comparable within a category only. A payer Patient Access API and an EHR vendor
sandbox answer to different implementation guides, so they are never ranked against each other.</p>
<h2>What the curation found</h2>
<p>Of the payer organizations whose FHIR base URL is documented on a public developer portal,
most do expose a readable CapabilityStatement, and they mostly grade well. The harder problem is
that finding the URL at all is manual: payer base URLs are not predictable, so a registry has to
be built one developer portal at a time. Every EHR vendor sandbox tried answered on the first
attempt.</p>
<p><a href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/payer-verifiability.md">Read
the full write-up</a>, including the measurement errors this project made and corrected.</p>
<h2>Use the data</h2>
<ul class="cards">
<li><a href="/fhir-scorecard/scorecards.json">scorecards.json</a>, the full graded dataset</li>
<li><a href="/fhir-scorecard/how-we-grade/">How we grade</a>, every finding code and
its citation</li>
<li><a href="https://github.com/ChelseaKR/fhir-scorecard">Source, registry, and
candidate log</a></li>
</ul>
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


def how_we_grade_page(origin: str) -> Page:
    rows = "".join(
        f'<section id="{code}"><h3>{code}: {html.escape(title)}</h3>'
        f"<p><strong>{html.escape(question)}</strong></p><p>{html.escape(detail)}</p></section>"
        for code, title, question, detail in _FINDING_DOCS
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="/fhir-scorecard/">Home</a></nav>
<h1>How we grade</h1>
<p class="lede">Every finding is deterministic, cites a spec clause, and can be explained in one
sentence. There is no model anywhere in the grading path.</p>
<h2>Dimensions</h2>
<p>Three dimensions are weighted into a letter grade: reachability (35%), capability transparency
(35%), and interoperability readiness (30%). An unreachable endpoint is an F regardless of
anything else, because nothing else could be observed.</p>
<h2>Findings</h2>
{rows}
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
