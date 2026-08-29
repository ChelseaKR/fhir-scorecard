"""How much of the federal-marketplace frame has a publicly checkable FHIR endpoint.

ROADMAP phase 5 listed a coverage tracker and marked it partial: *"which CMS-regulated payers
have a publicly checkable endpoint at all, with the 'documented but unreachable' and 'no public
URL found' populations counted separately and never merged. The denominator now exists ... The
tracker page itself is still open."*

The denominator is `data/frames/qhp-landscape-py2026-individual-medical.csv`: every issuer
selling an individual-market qualified health plan on HealthCare.gov, as CMS enumerates them.
The frame is national; the review proceeds a state at a time, which is why a fourth population
exists and why it is the one that matters most.

**Four populations, and no two of them may be added together.**

``VERIFIED``
    The organization publishes a base URL this project retrieved a CapabilityStatement from.

``DOCUMENTED_UNREACHABLE``
    The organization publishes a base URL in its own materials and this project could not
    retrieve a document from it. A finding about the public record, and the reason the registry
    keeps such an entry instead of pruning it.

``NO_PUBLIC_URL_FOUND``
    A review retrieved the organization's own documentation and found no base URL a stranger
    could read.

``NOT_YET_REVIEWED``
    Nobody has looked yet. **This is a fact about this project, never about the issuer**, and
    `docs/SAMPLING-FRAME.md` says so in those words. Merging it into any of the three above
    would publish this project's backlog as an issuer's silence.

That last rule is enforced rather than trusted: :func:`publishing_rate` refuses a set
containing an unreviewed organization instead of dividing by it. A rate over a partly reviewed
frame is not a coverage rate, and the only safe way to prevent one is to make it unrepresentable.

Every classification here is read from committed data and nothing else. The basis for the first
two comes from ``registry.Endpoint.verification_basis``, whose values and default are defined
and documented in that module; the third from a cohort member's exclusion record; the fourth
from the absence of a review covering that frame row. Nothing is inferred from a name, a
hostname, or a URL path.

**The join key is (state, issuer name), never the issuer name alone.** The frame's unit is a
state-issuer: a national carrier appears once per state it sells in, and CMS names corporate
siblings separately. Joining on the name alone credited 23 states with a review that only Texas
and Florida received, and would have published an Alabama issuer's status on the strength of
reading a Texas issuer's developer portal. Which frame rows a cohort reviewed is therefore read
from that cohort's own committed roster CSV, the file
``tests/test_plan_evidence.py`` already pins to be an exact state-slice of the national frame,
rather than from a state code written down here.

That key governs **which review is published**, not only whether a row counts as reviewed. Three
roster names sit in both the Texas and the Florida cohort, and a lookup keyed on the name alone
returned whichever cohort loaded last, so Florida's rows carried Texas's review text. The two
halves of the join are kept together in :func:`_members_by_row` for that reason.
"""

from __future__ import annotations

import csv
import html
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fhir_scorecard.cohort import Cohort, CohortMember
from fhir_scorecard.registry import Endpoint
from fhir_scorecard.site import Page, json_ld

#: Site path of the coverage tracker.
COVERAGE_PATH = "coverage"

#: How a cohort's committed roster file is named beside its cohort JSON. The stem either side
#: of it is the cohort id, which is what ties a roster's rows to the members that reviewed them.
ROSTER_SUFFIX = ".roster.csv"

VERIFIED = "verified"
DOCUMENTED_UNREACHABLE = "documented_unreachable"
NO_PUBLIC_URL_FOUND = "no_public_url_found"
NOT_YET_REVIEWED = "not_yet_reviewed"

#: Every population, in the order the page presents them, with the sentence that defines each.
#: A classification outside this map is a bug, which :func:`classify` asserts.
POPULATIONS: dict[str, str] = {
    VERIFIED: "publishes a base URL a CapabilityStatement was retrieved from",
    DOCUMENTED_UNREACHABLE: (
        "publishes a base URL in its own materials that did not answer on the date it was checked"
    ),
    NO_PUBLIC_URL_FOUND: (
        "was reviewed, and no base URL a stranger could read was found in its own documentation"
    ),
    NOT_YET_REVIEWED: (
        "has not been reviewed by this project. A fact about this project's progress, never "
        "about what the organization publishes"
    ),
}

#: The three populations a review can produce. The fourth is the absence of a review.
REVIEWED_POPULATIONS: tuple[str, ...] = (VERIFIED, DOCUMENTED_UNREACHABLE, NO_PUBLIC_URL_FOUND)


@dataclass(frozen=True)
class FrameOrg:
    """One organization on the frame, and which population it falls in."""

    state: str
    roster_name: str
    population: str
    detail: str

    @property
    def reviewed(self) -> bool:
        return self.population != NOT_YET_REVIEWED


def read_frame(path: Path) -> list[tuple[str, str]]:
    """(state code, issuer name) for every row of a committed frame or roster CSV, in file order."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [(row["state_code"], row["issuer_name"]) for row in csv.DictReader(handle)]


def read_reviewed_rows_by_cohort(cohort_dir: Path) -> dict[str, frozenset[tuple[str, str]]]:
    """Every (state, issuer name) a committed cohort roster covers, kept per cohort.

    Per cohort rather than pooled into one set, because two questions have to be answered and a
    pooled set answers only the first: *was this row reviewed*, and *which cohort reviewed it*.
    The second is what decides whose review may be published against the row, and losing it is
    how one state's review came to be published under another state's heading.

    The key is the roster file's stem, which is the cohort id: ``texas-marketplace.json`` and
    ``texas-marketplace.roster.csv``. :func:`classify` reads it back through ``cohort_id``, so a
    roster naming no loaded cohort contributes rows no member can answer for, and those rows
    stay unreviewed rather than picking up somebody else's review.

    A cohort with no roster CSV beside it - California's, whose frame is a state's own program
    roster rather than a slice of the federal file - contributes nothing here, correctly: its
    members are not rows of this frame.
    """
    return {
        roster.name[: -len(ROSTER_SUFFIX)]: frozenset(read_frame(roster))
        for roster in sorted(cohort_dir.glob("*" + ROSTER_SUFFIX))
    }


def _population_for(
    member_endpoints: tuple[str, ...],
    endpoints: dict[str, Endpoint],
) -> tuple[str, str]:
    """The population and the sentence behind it, for a member that carries endpoints.

    An organization with more than one surface is counted on its best-evidenced one. Publishing
    a Provider Directory that answers is a checkable endpoint whatever the Patient Access URL
    did, and counting the organization twice would break the frame's denominator.
    """
    bases = [endpoints[e].verification_basis for e in member_endpoints if e in endpoints]
    if any(basis == "live_capability" for basis in bases):
        live = sum(1 for basis in bases if basis == "live_capability")
        return VERIFIED, f"{live} of {len(bases)} listed surfaces answered when they were checked"
    return (
        DOCUMENTED_UNREACHABLE,
        f"{len(bases)} listed {'surface' if len(bases) == 1 else 'surfaces'}, "
        "each published by the organization and not retrievable on the date it was checked",
    )


def _members_by_row(
    cohorts: tuple[Cohort, ...],
    reviewed_rows: Mapping[str, frozenset[tuple[str, str]]],
) -> dict[tuple[str, str], CohortMember]:
    """(state, issuer name) -> the member of the cohort that reviewed *that row*.

    Keyed on the pair on both sides, which is the whole point. Keyed on ``roster_name`` alone
    and built across every cohort at once, this map held one member per name for the whole
    frame, and the last cohort to load won every name two cohorts shared. ``load_cohort_dir``
    sorts by filename, so ``texas-marketplace.json`` loaded after ``florida-marketplace.json``
    and Florida's rows for Cigna Healthcare, Molina Healthcare and UnitedHealthcare published
    Texas's review. Molina's two exclusion reasons are materially different findings about two
    different developer portals, and Florida's was never published anywhere.

    A member may only answer for the rows its own cohort's roster covers, so the rows come from
    that cohort's entry in ``reviewed_rows`` and never from the pooled set.
    """
    by_row: dict[tuple[str, str], CohortMember] = {}
    for cohort in cohorts:
        by_name = {m.roster_name: m for m in cohort.members if m.roster_name}
        for row in sorted(reviewed_rows.get(cohort.cohort_id, frozenset())):
            member = by_name.get(row[1])
            if member is not None:
                by_row[row] = member
    return by_row


def classify(
    frame: list[tuple[str, str]],
    cohorts: tuple[Cohort, ...],
    endpoints: list[Endpoint],
    reviewed_rows: Mapping[str, frozenset[tuple[str, str]]],
) -> list[FrameOrg]:
    """Every frame row assigned to exactly one population.

    Joined on ``(state, roster_name)``: the state code and the name the roster's publisher
    prints, both kept verbatim on both sides, and on *both* halves of the join. ``reviewed_rows``
    comes from the committed cohort roster CSVs, per cohort, so a member can only answer for the
    frame rows its own cohort actually reviewed. A join on the name alone puts a national
    carrier's Texas review against its rows in twenty-two other states; a join on this project's
    own normalisation of a name would give the frame a denominator only this project could
    reproduce. Both are the defect ``docs/SAMPLING-FRAME.md`` exists to prevent.
    """
    by_id = {endpoint.endpoint_id: endpoint for endpoint in endpoints}
    members = _members_by_row(cohorts, reviewed_rows)
    classified = []
    for state, issuer_name in frame:
        # Reviewed-ness is a property of the (state, issuer) row, not of the name, and so is the
        # review itself. A row no member of the cohort that reviewed it answers for is unreviewed.
        member = members.get((state, issuer_name))
        if member is None:
            population, detail = NOT_YET_REVIEWED, "no review has been recorded for this state"
        elif member.endpoint_ids:
            population, detail = _population_for(member.endpoint_ids, by_id)
        else:
            reason = member.exclusion.reason if member.exclusion else ""
            population, detail = NO_PUBLIC_URL_FOUND, reason
        classified.append(FrameOrg(state, issuer_name, population, detail))
    return classified


def counts(orgs: list[FrameOrg]) -> dict[str, int]:
    """How many organizations fall in each population, every population present even at zero."""
    counted = Counter(org.population for org in orgs)
    return {population: counted.get(population, 0) for population in POPULATIONS}


def publishing_rate(orgs: list[FrameOrg]) -> tuple[int, int]:
    """(verified, denominator) over reviewed organizations only.

    Raises if handed an unreviewed organization. A rate whose denominator mixes "we looked and
    found nothing" with "nobody looked" is not a coverage rate, and the only reliable way to
    stop one being published is to refuse to compute it. ``docs/SAMPLING-FRAME.md``: the two
    "must never be merged".
    """
    unreviewed = [org for org in orgs if not org.reviewed]
    if unreviewed:
        raise ValueError(
            f"publishing_rate was given {len(unreviewed)} unreviewed "
            f"{'organization' if len(unreviewed) == 1 else 'organizations'} "
            f"(first: {unreviewed[0].state} {unreviewed[0].roster_name!r}). An unreviewed "
            "organization has no publishing outcome to put in a numerator or a denominator"
        )
    return sum(1 for org in orgs if org.population == VERIFIED), len(orgs)


def _state_rows(orgs: list[FrameOrg]) -> str:
    by_state: dict[str, list[FrameOrg]] = {}
    for org in orgs:
        by_state.setdefault(org.state, []).append(org)
    rows = []
    for state in sorted(by_state):
        in_state = by_state[state]
        tally = counts(in_state)
        reviewed = [org for org in in_state if org.reviewed]
        status = f"{len(reviewed)} of {len(in_state)} reviewed" if reviewed else "not yet reviewed"
        cells = "".join(f"<td>{tally[population]}</td>" for population in REVIEWED_POPULATIONS)
        rows.append(
            f'<tr><th scope="row">{html.escape(state)}</th><td>{len(in_state)}</td>'
            f"{cells}<td>{tally[NOT_YET_REVIEWED]}</td><td>{html.escape(status)}</td></tr>"
        )
    return "".join(rows)


def _population_list(orgs: list[FrameOrg], population: str) -> str:
    named = sorted(
        (org for org in orgs if org.population == population),
        key=lambda org: (org.state, org.roster_name.lower()),
    )
    if not named:
        return "<p>No organization on the reviewed part of the frame is in this population.</p>"
    items = "".join(
        f"<li><strong>{html.escape(org.roster_name)}</strong> ({html.escape(org.state)})"
        + (f": {html.escape(org.detail)}" if org.detail else "")
        + "</li>"
        for org in named
    )
    return f"<ul>{items}</ul>"


def page(orgs: list[FrameOrg], origin: str) -> Page:
    """The coverage tracker: four populations over the frame, none of them added together."""
    tally = counts(orgs)
    reviewed = [org for org in orgs if org.reviewed]
    states = {org.state for org in orgs}
    reviewed_states = {org.state for org in reviewed}
    if reviewed:
        verified, denominator = publishing_rate(reviewed)
        headline = (
            f"Of the {denominator} organizations reviewed so far, {verified} publish a base URL "
            "this project retrieved a conformance document from."
        )
    else:
        headline = (
            "No state on this frame has been reviewed yet, so there is no publishing outcome to "
            "report for any organization on it."
        )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>Coverage</span></li>
</ol></nav>
<p class="eyebrow">Coverage</p>
<h1>How much of the federal marketplace has a publicly checkable FHIR endpoint</h1>
<p class="lede">{headline} That figure is over the reviewed part of the frame only:
{len(reviewed)} of {len(orgs)} organizations, in {len(reviewed_states)} of {len(states)} states.
The other {tally[NOT_YET_REVIEWED]} have not been looked at, which is a fact about this
project.</p>
<div class="usa-alert usa-alert--info usa-alert--slim"><div class="usa-alert__body">
<p class="usa-alert__text">The four counts below are never added together and never reported as
one number. "Nobody has looked yet" is not evidence about an issuer, and a rate whose
denominator mixed it with "we looked and found nothing" would publish this project's backlog as
an issuer's silence. The code refuses to compute such a rate rather than relying on nobody
asking for one.</p>
</div></div>
<h2>The four populations</h2>
<table class="usa-table usa-table--striped">
<caption>Organizations on the CMS QHP Landscape PY2026 Individual Medical frame</caption>
<thead><tr><th scope="col">Population</th><th scope="col">Organizations</th>
<th scope="col">What it means</th></tr></thead>
<tbody>{
        "".join(
            f'<tr><th scope="row">{html.escape(population.replace("_", " "))}</th>'
            f"<td>{tally[population]}</td><td>{html.escape(meaning)}</td></tr>"
            for population, meaning in POPULATIONS.items()
        )
    }</tbody></table>
<h2>By state</h2>
<table class="usa-table usa-table--striped">
<caption>Frame organizations by state and population</caption>
<thead><tr><th scope="col">State</th><th scope="col">On the frame</th>
<th scope="col">Verified</th><th scope="col">Documented, unreachable</th>
<th scope="col">No public URL found</th><th scope="col">Not yet reviewed</th>
<th scope="col">Review status</th></tr></thead>
<tbody>{_state_rows(orgs)}</tbody></table>
<h2>Publishes a base URL that answered</h2>
{_population_list(orgs, VERIFIED)}
<h2>Publishes a base URL that did not answer</h2>
<p>A finding about the public record rather than a gap in this one. Such an endpoint stays
listed and keeps being probed from every vantage, because an entry that was dropped cannot be
corrected.</p>
{_population_list(orgs, DOCUMENTED_UNREACHABLE)}
<h2>Reviewed, no base URL a stranger could read</h2>
<p>The federal rule these organizations are in frame for does not require an issuer to print
its base URL where an unregistered visitor can read it, so this is not a compliance finding
about anyone. What a missing base URL costs is narrower and worth naming exactly: conformance
stops being checkable by anyone who has not already entered a business relationship with the
plan.</p>
{_population_list(orgs, NO_PUBLIC_URL_FOUND)}
<h2>Not yet reviewed</h2>
<p>{tally[NOT_YET_REVIEWED]} organizations, in the states this project has not reached. Each
state-issuer's documentation has to be found and read by a person, which is why cohorts are
published per state as they are completed rather than all at once. Until a state is reviewed
its issuers are carried here and nowhere else, and never rendered as publishing nothing. See
<a href="https://github.com/ChelseaKR/fhir-scorecard/blob/main/docs/SAMPLING-FRAME.md">the
sampling frame</a>.</p>
{
        json_ld(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": "Public FHIR endpoint coverage of the federal marketplace frame",
                "description": (
                    "Every issuer on CMS's QHP Landscape PY2026 Individual Medical file, assigned to one "
                    "of four populations: a verified public FHIR endpoint, a documented endpoint that "
                    "did not answer, no public base URL found by review, or not yet reviewed."
                ),
                "url": f"{origin}/{COVERAGE_PATH}/",
                "isAccessibleForFree": True,
            }
        )
    }
"""
    return Page(
        path=COVERAGE_PATH,
        title="Coverage: which marketplace issuers publish a checkable FHIR endpoint",
        description=(
            f"{len(reviewed)} of {len(orgs)} organizations on CMS's federal-marketplace frame "
            "have been reviewed, in four populations that are never added together: verified, "
            "documented but unreachable, no public URL found, and not yet reviewed."
        ),
        body=body,
        priority="0.8",
    )
