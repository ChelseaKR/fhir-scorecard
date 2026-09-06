"""Render scorecards to machine-readable JSON.

This module also held ``render_html``, a standalone no-JavaScript summary page, from the
v0.1 scaffold when that page was the only output the tool produced. ``home_page`` arrived
later with ``path=""``, which ``site.write_page`` resolves to ``out_dir`` itself, and quietly
took the same filename: every grade run wrote ``out/index.html`` twice and the first write
was destroyed by the second, in the same ``try`` block, before anything could read it. No
reader ever received it, and nothing in README.md, CHANGELOG.md, ``docs/`` or any ADR
described it. It was removed rather than published; see the commit that removed it for why,
and ``_write_site``, which now refuses to build two pages that target one path.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from fhir_scorecard.grading import Scorecard


def to_json(scorecards: list[Scorecard], *, generated_at: str, vantage: str = "unspecified") -> str:
    payload = {
        "generator": "fhir-scorecard",
        "generated_at": generated_at,
        "vantage": vantage,
        "disclaimer": (
            "Observational snapshot of public, unauthenticated FHIR discovery "
            "surfaces. Not an audit, a ranking of care quality, or a statement "
            "about any organization's regulatory compliance."
        ),
        "scorecards": [asdict(s) for s in scorecards],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
