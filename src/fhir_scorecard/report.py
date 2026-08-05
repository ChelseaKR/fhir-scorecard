"""Render scorecards to machine-readable JSON and an accessible, no-JavaScript HTML page."""

from __future__ import annotations

import html
import json
from dataclasses import asdict

from fhir_scorecard.grading import Scorecard


def to_json(scorecards: list[Scorecard], *, generated_at: str,
            vantage: str = "unspecified") -> str:
    payload = {
        "generator": "fhir-scorecard",
        "generated_at": generated_at,
        "vantage": vantage,
        "disclaimer": ("Observational snapshot of public, unauthenticated FHIR discovery "
                       "surfaces. Not an audit, a ranking of care quality, or a statement "
                       "about any organization's regulatory compliance."),
        "scorecards": [asdict(s) for s in scorecards],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _card(s: Scorecard) -> str:
    rows: list[str] = []
    for d in s.dimensions:
        items = "".join(
            f"<li>{'✓' if f.ok else '✗'} {html.escape(f.message)} "
            f'<a href="{html.escape(f.citation)}">spec</a></li>'
            for f in d.findings
        )
        rows.append(
            f"<h3>{html.escape(d.title)}: {d.score}/100</h3><ul>{items}</ul>"
        )
    availability_html = (f"<p class=\"avail\">Availability: {html.escape(s.availability)}</p>"
                         if s.availability else "")
    drift_html = ""
    if s.observed_since is not None:
        if s.drift_events:
            events = "".join(f"<li>{html.escape(e)}</li>" for e in s.drift_events)
            drift_html = (f"<h3>Capability changes (informational, not scored)</h3>"
                          f"<p>Observed since {html.escape(s.observed_since)}.</p>"
                          f"<ul>{events}</ul>")
        else:
            drift_html = (f"<p>Observed since {html.escape(s.observed_since)}; "
                          "no capability changes recorded.</p>")
    return (
        f'<section aria-labelledby="h-{html.escape(s.endpoint_id)}">'
        f'<h2 id="h-{html.escape(s.endpoint_id)}">{html.escape(s.name)} '
        f"<span class=\"grade grade-{s.grade.lower()}\">{s.grade}</span></h2>"
        + availability_html + "".join(rows) + drift_html + "</section>"
    )


_KIND_LABELS = {
    "payer": "Payer Patient Access APIs",
    "payer_provider_directory": "Payer Provider Directory APIs (public by design)",
    "provider": "Provider / health system APIs",
    "ehr": "EHR vendor sandboxes",
    "reference": "Reference and test servers",
}
_KIND_ORDER = ("payer", "payer_provider_directory", "provider", "ehr", "reference")


def _summary_table(scorecards: list[Scorecard]) -> str:
    """One table per kind. Grades are not comparable across kinds, so they are never
    ranked together: a payer Patient Access API and an EHR sandbox answer to different
    implementation guides and different expectations."""
    tables: list[str] = []
    for kind in _KIND_ORDER:
        group = [s for s in scorecards if s.kind == kind]
        if not group:
            continue
        rows = "".join(
            f'<tr><td><a href="#h-{html.escape(s.endpoint_id)}">{html.escape(s.name)}</a></td>'
            f'<td><span class="grade grade-{s.grade.lower()}">{s.grade}</span></td></tr>'
            for s in sorted(group, key=lambda s: (s.grade, s.name))
        )
        label = _KIND_LABELS.get(kind, kind)
        tables.append(
            f"<table><caption>{html.escape(label)} ({len(group)})</caption>"
            '<thead><tr><th scope="col">Endpoint</th><th scope="col">Grade</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )
    return "".join(tables)


def render_html(scorecards: list[Scorecard], *, generated_at: str,
                vantage: str = "unspecified") -> str:
    body = _summary_table(scorecards) + "".join(_card(s) for s in scorecards)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FHIR Scorecard</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem;
       line-height: 1.5; color: #1a1a1a; background: #fff; }}
.grade {{ display: inline-block; min-width: 1.6em; text-align: center; border-radius: 4px;
          padding: 0 .3em; color: #fff; background: #666; }}
.grade-a {{ background: #14691f; }} .grade-b {{ background: #3f7d20; }}
.grade-c {{ background: #9a6700; }} .grade-d {{ background: #b4432c; }}
.grade-f {{ background: #a01212; }}
section {{ border-top: 1px solid #ddd; padding-top: 1rem; margin-top: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
caption {{ text-align: left; font-weight: 600; margin-bottom: .5rem; }}
th, td {{ text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #eee; }}
.avail {{ color: #444; font-size: .95rem; }}
</style>
</head>
<body>
<header>
<h1>FHIR Scorecard</h1>
<p>Deterministic grades for publicly observable FHIR endpoint surfaces. Generated
{html.escape(generated_at)} from a single vantage point ({html.escape(vantage)}).
No patient data is ever accessed; only public <code>/metadata</code> and SMART discovery
documents are graded. Grades are comparable only within a kind.</p>
</header>
<main>
{body}
</main>
<footer><p>Observational snapshot, not an audit or a compliance determination.
<a href="https://github.com/ChelseaKR/fhir-scorecard">Source and methodology</a>.</p></footer>
</body>
</html>
"""
