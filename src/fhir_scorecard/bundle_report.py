"""One endpoint's graded scorecard as a single portable, brandable HTML file.

``entity_report.report_page`` already builds the richest single-endpoint report this project
publishes -- what was observed, what was not, and what would change the result -- and it is
free, on the live site, at ``/endpoint/<id>/report/``. This module renders the *same* body for a
buyer who wants it as a file: something that opens with no network in the room, prints cleanly,
and can carry a buyer's own name on the cover the way a compliance packet does.

Nothing here computes a new finding, a new score, or a new action item. ``render_report``
reconstructs the same :class:`~fhir_scorecard.grading.Scorecard` dataclass the live site renders
and calls :func:`fhir_scorecard.entity_report.report_page` for the body -- the same function, on
the same typed data, that ``fhir-scorecard grade`` calls to build the free page. A buyer reading
a report from this module and a visitor reading the free page for the same endpoint on the same
day see the identical findings, the identical "what would change this" action list, and the
identical spec citations; this module only changes the wrapper: a self-contained document shell,
an optional brand, and absolute rather than site-relative links, since the file may be opened
with no site behind it.

A state Medicaid or CHIP office overseeing several managed-care organizations, or a compliance
consultancy tracking several payer clients, can put its own name, logo, and accent color on the
cover via :class:`Brand`. The accent is decorative only: it colors a band and rules, never text,
so any accent keeps the document readable.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard import entity_report
from fhir_scorecard.grading import DimensionScore, Finding, Scorecard
from fhir_scorecard.site import _GRADE_COLORS, _INTERNAL_LINK, DEFAULT_ORIGIN, _grade_slug
from fhir_scorecard.vantage import VantageReport

DEFAULT_ACCENT = "#162e51"

_LOGO_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Print-safe document palette. Every text/background pair here is a plain, high-contrast choice;
# the accent is never used for text, so a buyer's brand color cannot make the document unreadable.
_INK = "#1a1f1c"
_INK_SOFT = "#4b524b"
_LINE = "#ccd1c8"
_HEAD_BG = "#eceee7"
_PAPER = "#ffffff"


class ReportError(ValueError):
    """A report input problem the message explains in plain language."""


@dataclass(frozen=True)
class Brand:
    """The organization putting its name on the report cover.

    ``logo_data_uri`` is the logo embedded as a data: URI so the document stays a single
    self-contained file. ``accent`` is a #rrggbb hex used only for decorative bands and rules,
    never for text.
    """

    name: str
    logo_data_uri: str | None = None
    accent: str = DEFAULT_ACCENT


def _validate_accent(value: str) -> str:
    v = value.strip()
    if len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:]):
        return v.lower()
    raise ReportError(f"accent must be a #rrggbb hex color, got {value!r}")


def _logo_data_uri(path: Path) -> str:
    media_type = _LOGO_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        supported = ", ".join(sorted(_LOGO_MEDIA_TYPES))
        raise ReportError(f"logo {path.name}: use one of {supported}")
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise ReportError(f"logo file not readable: {path} ({err})") from err
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


# ---------------------------------------------------------------------------
# Reconstructing a Scorecard from its published (asdict) JSON shape.
# ---------------------------------------------------------------------------


def scorecard_from_dict(data: Mapping[str, Any]) -> Scorecard:
    """The inverse of ``dataclasses.asdict(Scorecard)``, which is what
    :func:`fhir_scorecard.report.to_json` publishes at ``site/scorecards.json``.

    Reconstructing the dataclass rather than rendering the dict directly means the bundle report
    calls the exact same rendering path (``entity_report.report_page``) the live pages call, on
    the exact same typed data -- there is no second, parallel notion of what a "finding" or a
    "dimension" looks like for this module to drift from the grader's.
    """
    dimensions = tuple(
        DimensionScore(
            key=str(dim["key"]),
            title=str(dim["title"]),
            score=dim["score"],
            findings=tuple(
                Finding(
                    code=str(f["code"]),
                    ok=bool(f["ok"]),
                    points=int(f["points"]),
                    max_points=int(f["max_points"]),
                    message=str(f["message"]),
                    citation=str(f["citation"]),
                    observed=bool(f.get("observed", True)),
                    unanswered=bool(f.get("unanswered", False)),
                    withheld_points=int(f.get("withheld_points", 0)),
                )
                for f in dim["findings"]
            ),
            withheld_points=int(dim.get("withheld_points", 0)),
        )
        for dim in data["dimensions"]
    )
    vantage_reports = tuple(
        VantageReport(
            vantage=str(v["vantage"]),
            network=str(v["network"]),
            reachable=bool(v["reachable"]),
            status=v.get("status"),
            failure_kind=v.get("failure_kind"),
            elapsed_ms=v.get("elapsed_ms"),
            error=v.get("error"),
        )
        for v in (data.get("vantage_reports") or ())
    )
    return Scorecard(
        endpoint_id=str(data["endpoint_id"]),
        name=str(data["name"]),
        grade=str(data["grade"]),
        reachable=bool(data["reachable"]),
        dimensions=dimensions,
        vantage_note=str(data.get("vantage_note") or ""),
        kind=str(data.get("kind") or "reference"),
        observed_since=data.get("observed_since"),
        drift_events=tuple(data.get("drift_events") or ()),
        drift_alternations=tuple(data.get("drift_alternations") or ()),
        availability=str(data.get("availability") or ""),
        last_answered=data.get("last_answered"),
        vantage_reports=vantage_reports,
        failure_kinds=tuple(data.get("failure_kinds") or ()),
    )


def _absolute_links(markup: str, origin: str) -> str:
    """Root-relative ``href``/``src`` values rewritten to absolute ones.

    ``entity_report.report_page`` links to ``/how-we-grade/#<code>``, ``/claim/``,
    ``/history/<id>/``, and its own breadcrumb, because it renders a page that lives *on*
    fhir.chelseakr.com. This file does not: it is meant to be opened with no network in the
    room, and a root-relative link inside a local file resolves against the local filesystem,
    not against the site. Reuses the exact substitution ``site._shell`` already applies for the
    analogous problem (a site served under a path prefix), so "make an internal link absolute"
    has one implementation on this codebase, not two.
    """
    return _INTERNAL_LINK.sub(rf'\1="{origin}/', markup)


# ---------------------------------------------------------------------------
# Rendering: Scorecard + Brand -> one self-contained HTML document.
# ---------------------------------------------------------------------------


def _css(accent: str) -> str:
    grade_rules = "\n".join(
        f"    .grade-{_grade_slug(grade)} {{ background: {color}; color: #fff; }}"
        for grade, color in _GRADE_COLORS.items()
    )
    return f"""    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 0 1.25rem 3rem; background: {_PAPER}; color: {_INK};
      font: 16px/1.55 "Public Sans", "Helvetica Neue", Arial, sans-serif;
    }}
    .accent-band {{ height: 0.5rem; background: {accent}; margin: 0 -1.25rem; }}
    .report {{ max-width: 50rem; margin: 0 auto; }}
    .prepared-by {{
      display: flex; align-items: center; gap: 0.75rem;
      border-bottom: 1px solid {_LINE}; padding: 0.75rem 0; margin-bottom: 1.5rem;
    }}
    .prepared-by img {{ max-height: 3rem; max-width: 12rem; }}
    .prepared-by p {{ margin: 0; color: {_INK_SOFT}; font-size: 0.9375rem; }}
    h1 {{ margin: 1rem 0 0.5rem; font-size: 1.6rem; line-height: 1.25; }}
    h2 {{
      margin: 2rem 0 0.75rem; font-size: 1.2rem;
      border-bottom: 3px solid {accent}; padding-bottom: 0.25rem;
    }}
    h3 {{ margin: 1.25rem 0 0.5rem; font-size: 1.02rem; }}
    a {{ color: {_INK}; }}
    p, li {{ font-size: 0.95rem; }}
    .eyebrow {{
      margin: 0; color: {_INK_SOFT}; font-size: 0.85rem; letter-spacing: 0.02em;
      text-transform: uppercase; font-weight: 600;
    }}
    .lede, .report-print-note {{ color: {_INK_SOFT}; }}
    nav.usa-breadcrumb {{ font-size: 0.8rem; color: {_INK_SOFT}; margin-bottom: 0.5rem; }}
    .usa-breadcrumb__list {{ list-style: none; margin: 0; padding: 0; display: flex; gap: 0.4rem; flex-wrap: wrap; }}
    .usa-breadcrumb__list-item:not(:last-child)::after {{ content: "/"; margin-left: 0.4rem; color: {_LINE}; }}
    .usa-breadcrumb__link {{ color: {_INK_SOFT}; text-decoration: underline; }}
    .grade {{
      display: inline-block; border-radius: 0.25rem; padding: 0.1rem 0.6rem;
      font-weight: 700; font-size: 1rem;
    }}
{grade_rules}
    .evidence-card, .report-dimension {{
      border: 1px solid {_LINE}; border-radius: 0.25rem; padding: 1rem; margin: 1rem 0;
    }}
    dl.facts {{ margin: 0.5rem 0 0; }}
    dl.facts dt {{ font-weight: 600; margin-top: 0.5rem; color: {_INK_SOFT}; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.02em; }}
    dl.facts dd {{ margin: 0.1rem 0 0; }}
    dl.facts code {{ word-break: break-all; }}
    table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0; font-size: 0.85rem; }}
    caption {{ text-align: left; color: {_INK_SOFT}; padding-bottom: 0.4rem; }}
    th, td {{ border: 1px solid {_LINE}; padding: 0.4rem 0.5rem; text-align: left; vertical-align: top; }}
    thead th {{ background: {_HEAD_BG}; }}
    tbody th {{ font-weight: 600; }}
    .usa-table-container--scrollable {{ overflow-x: auto; }}
    ol.report-actions, ul.report-actions {{ padding-left: 1.25rem; margin: 0.5rem 0; }}
    .report-action {{ margin: 0 0 1.1rem; }}
    .report-action h3 {{ margin: 0 0 0.2rem; }}
    .report-action-worth {{ color: {_INK_SOFT}; font-size: 0.85rem; margin: 0.1rem 0; }}
    .finding-links {{ display: flex; gap: 0.75rem; font-size: 0.8rem; color: {_INK_SOFT}; }}
    .finding-links a {{ color: inherit; }}
    .usa-alert {{
      border-left: 4px solid {accent}; background: {_HEAD_BG}; padding: 0.75rem 1rem;
      margin: 1.5rem 0; font-size: 0.9rem;
    }}
    .usa-alert__body {{ margin: 0; }}
    .vantage-note {{ color: {_INK_SOFT}; font-size: 0.85rem; }}
    .report-foot {{
      margin-top: 2.5rem; border-top: 1px solid {_LINE}; padding-top: 0.75rem;
      color: {_INK_SOFT}; font-size: 0.875rem;
    }}
    @media print {{
      body {{ padding: 0; font-size: 12px; }}
      .accent-band {{ margin: 0; }}
      section, .report-dimension, .evidence-card {{ break-inside: avoid; }}
      h2 {{ break-after: avoid-page; }}
      .report-foot a[href^="http"]::after {{ content: " (" attr(href) ")"; }}
    }}"""


def _prepared_by_html(brand: Brand | None) -> str:
    if brand is None:
        return ""
    logo = (
        f'<img src="{html.escape(brand.logo_data_uri)}" alt="{html.escape(brand.name)} logo">'
        if brand.logo_data_uri
        else ""
    )
    return (
        f'<div class="prepared-by">{logo}<p>Prepared by {html.escape(brand.name)}</p></div>\n    '
    )


def render_report(
    card: Scorecard,
    *,
    base_url: str = "",
    verified: str = "",
    brand: Brand | None = None,
    generated_at: dt.datetime | None = None,
    origin: str = DEFAULT_ORIGIN,
) -> str:
    """One self-contained HTML document for one endpoint's scorecard.

    No external stylesheet, font, script, or image: everything a browser needs to show or print
    it travels inside the file. The content -- summary, dimension detail, "what would change
    this", the correction channel, and provenance -- is rendered by
    ``entity_report.report_page``, the same function the live site's free single-endpoint report
    uses; only the wrapper (cover, brand, absolute links) is different.
    """
    accent = brand.accent if brand else DEFAULT_ACCENT
    when = (generated_at or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    page = entity_report.report_page(
        card,
        base_url=base_url,
        verified=verified,
        origin=origin.rstrip("/"),
        generated_at=when.strftime("%Y-%m-%d %H:%M UTC"),
    )
    body = _absolute_links(page.body, origin.rstrip("/"))
    produced = (
        f"Prepared by {html.escape(brand.name)} with FHIR Scorecard, an independent open-source "
        "operational scorecard for publicly observable FHIR endpoints."
        if brand
        else "Produced by FHIR Scorecard, an independent open-source operational scorecard "
        "for publicly observable FHIR endpoints."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page.title)}</title>
  <style>
{_css(accent)}
  </style>
</head>
<body>
  <div class="accent-band" role="presentation"></div>
  <div class="report">
    {_prepared_by_html(brand)}{body}
    <footer class="report-foot">
      <p>{produced} Every finding cites the FHIR R4 or SMART App Launch spec passage behind it.
      Purchase buys no influence over grades, methodology, or which endpoints are listed; the
      content above is the same page fhir.chelseakr.com publishes for this endpoint, generated
      here rather than fetched, so this file works with no network in the room.
      Report generated {html.escape(when.strftime("%Y-%m-%d %H:%M UTC"))}.</p>
    </footer>
  </div>
</body>
</html>
"""
