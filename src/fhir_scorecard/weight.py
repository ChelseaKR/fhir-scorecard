"""Transfer-size budgets over the built site.

The README's Performance row said, accurately, that *"no transfer-size or timing budget is
enforced in CI and none is claimed"*. This module is the transfer-size half. Timing stays
unclaimed: there is no server-side surface to time, and a wall-clock number measured on a CI
runner is a fact about the runner.

Two budgets, because the two things they bound fail differently, and because a resource one
page links and a resource every page links are not the same cost.

**Per page.** A page's own HTML plus any subresource **no other page links** - an endpoint's
grade badge, for instance. The home page and the cohort pages grow with the registry, so this
is a ceiling against a page growing without bound - a table that stopped paginating, a payload
accidentally inlined - not a target to sit near. The measured maximum on the published site on
2026-08-27 was 23,306 bytes (the home page, with 45 endpoints in its registry table); the
ceiling leaves room for the registry to grow several times over before anyone has to think
about it again, and the measurement is recorded here so the next reader can see how much room
was left rather than guess.

**Shared subresources.** Everything more than one page links, counted once: the vendored
stylesheets, scripts and icons. This is the tight budget, about five percent above what the
site ships today, because it moves only on a deliberate act - a new vendored asset, or a
U.S. Web Design System version bump. Those are exactly the changes worth a second look, since
the design system is 527,000 of the 624,450 bytes and is served from this origin rather than
from a CDN.

The split is what keeps the shared budget meaningful. Counting every subresource in one total
would make it grow by one badge per endpoint, so the number a version bump had to fit under
would depend on how many endpoints the registry happened to hold that week, and the budget
would have to be loosened on a schedule until it bounded nothing.

What is not counted, and why: fonts and icons that the vendored CSS pulls in at render time.
Which of them a browser fetches depends on the glyphs and components a page actually uses,
which a static reader cannot determine, and counting all of them would bound a number no
visitor ever transfers.
"""

from __future__ import annotations

import re
from pathlib import Path

from fhir_scorecard.audit import SiteFinding, page_file, page_paths

#: Ceiling on one page's own bytes: its HTML plus any subresource no other page links.
#: Measured maximum on the published site, 2026-08-27: 23,306 bytes (the home page). See the
#: module docstring for why the headroom is wide.
MAX_PAGE_BYTES = 65_536

#: Ceiling on the total of the subresources more than one page links, counted once each.
#: Measured on the published site, 2026-08-27: 624,450 bytes across six files, of which
#: uswds.min.css is 527,000. Deliberately close to the measurement.
MAX_SHARED_SUBRESOURCE_BYTES = 655_360

WEIGHT_CODES: dict[str, str] = {
    "WEIGHT_PAGE_OVER_BUDGET": (
        f"a page's HTML, plus any subresource only it links, exceeds {MAX_PAGE_BYTES} bytes"
    ),
    "WEIGHT_SUBRESOURCES_OVER_BUDGET": (
        f"the subresources more than one page links total more than "
        f"{MAX_SHARED_SUBRESOURCE_BYTES} bytes"
    ),
    "WEIGHT_SUBRESOURCE_MISSING": (
        "a page links a subresource that is not in the build, so its bytes cannot be counted "
        "and neither total below is the whole cost"
    ),
}

#: Same-origin subresources a document names directly. Kept to a regex over the raw bytes
#: rather than a parse because this reads the file as shipped, byte for byte, which is what a
#: size budget is about.
_SUBRESOURCE = re.compile(rb'(?:href|src)="(/[^"]+\.(?:css|js|svg|png|jpe?g|webp|gif|woff2?))"')


def subresources(root: Path, page: str) -> set[str]:
    """Site-relative paths of the subresources one page names."""
    body = (root / page_file(page)).read_bytes()
    return {match.decode().lstrip("/") for match in _SUBRESOURCE.findall(body)}


def _usage(root: Path) -> tuple[dict[str, set[str]], list[SiteFinding]]:
    """Which pages link each subresource, and a finding for each one the build did not write."""
    usage: dict[str, set[str]] = {}
    missing = []
    for page in page_paths(root):
        for reference in sorted(subresources(root, page)):
            if (root / reference).is_file():
                usage.setdefault(reference, set()).add(page)
            else:
                missing.append(
                    SiteFinding("WEIGHT_SUBRESOURCE_MISSING", page_file(page), reference)
                )
    return usage, missing


def audit_weight(root: Path) -> list[SiteFinding]:
    """Every page over its budget, plus the shared-subresource total if it is over.

    An empty list means both budgets hold for this build. Both are ceilings on bytes this
    build writes; neither says anything about how long a page takes to render.
    """
    usage, findings = _usage(root)
    own: dict[str, int] = {}
    for reference, pages in usage.items():
        if len(pages) == 1:
            own[next(iter(pages))] = (
                own.get(next(iter(pages)), 0) + (root / reference).stat().st_size
            )
    for page in page_paths(root):
        where = page_file(page)
        size = (root / where).stat().st_size + own.get(page, 0)
        if size > MAX_PAGE_BYTES:
            findings.append(
                SiteFinding(
                    "WEIGHT_PAGE_OVER_BUDGET", where, f"{size} bytes, budget {MAX_PAGE_BYTES}"
                )
            )
    shared = {reference for reference, pages in usage.items() if len(pages) > 1}
    total = sum((root / reference).stat().st_size for reference in shared)
    if total > MAX_SHARED_SUBRESOURCE_BYTES:
        findings.append(
            SiteFinding(
                "WEIGHT_SUBRESOURCES_OVER_BUDGET",
                "",
                f"{total} bytes across {len(shared)} files, budget {MAX_SHARED_SUBRESOURCE_BYTES}",
            )
        )
    return sorted(findings, key=lambda f: (f.where, f.code, f.detail))
