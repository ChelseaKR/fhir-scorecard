"""Which endpoints answered, ordered, and which ones this project will not order.

ROADMAP phase 5 asked for an *"availability leaderboard once the 14-observation floor is met
across the registry"*. Two decisions govern how that is built here, both recorded in
[ADR 0005](../../docs/adr/0005-a-leaderboard-that-publishes-what-meets-the-floor.md).

**The floor is applied per endpoint, not to the whole registry.** As written, the condition
never arrives: the registry gains endpoints, and a new one resets a registry-wide floor to
unmet for a fortnight, every time. So the page publishes the endpoints that meet the floor and
names the ones that do not, with their counts and how many more observations each needs. A
below-floor endpoint is never given a rate and never given a position.

**Ordering happens within a kind, never across.** This project's own rule, stated in
``gate.evaluate`` and on every kind page: an EHR vendor's sandbox and a payer's production
Patient Access API are not the same kind of thing, and a table that put them in one order would
be inventing a comparison the data does not support.

What a position means is stated on the page, because a ranked table invites a reading it cannot
support: this orders a measurement of whether a public address answered a request from one
provider's network over a recorded window. It is not a service-level measurement, not an
uptime guarantee, and not a statement about an organization. A run where no vantage reached an
endpoint is published as not reached from that network, and the site says so wherever
availability appears.
"""

from __future__ import annotations

import html

from fhir_scorecard.archive import ARCHIVE_PATH, Record
from fhir_scorecard.drift import MIN_OBSERVATIONS_TO_REPORT
from fhir_scorecard.site import KIND_LABELS, Page, json_ld

#: Site path of the leaderboard.
LEADERBOARD_PATH = "availability"


def ranked(built: list[Record]) -> list[Record]:
    """Records that meet the floor, best answered share first.

    Ties break on the larger number of observations and then on name, so the order is total
    and a rebuild cannot reshuffle equal rows. Position is not stored on the record: it is a
    property of the table, and a record that leaves the table takes no position with it.
    """
    return sorted(
        (record for record in built if record.reportable),
        key=lambda record: (-(record.rate_percent or 0), -record.observed, record.name.lower()),
    )


def below_floor(built: list[Record]) -> list[Record]:
    """Records that do not meet the floor, most observations first, then name."""
    return sorted(
        (record for record in built if not record.reportable),
        key=lambda record: (-record.observed, record.name.lower()),
    )


def _rows(records_in_kind: list[Record]) -> str:
    rows = []
    for position, record in enumerate(records_in_kind, start=1):
        rows.append(
            f'<tr><td>{position}</td><th scope="row">'
            f'<a href="/endpoint/{html.escape(record.endpoint_id)}/">'
            f"{html.escape(record.name)}</a></th>"
            f"<td>{record.rate_percent}%</td>"
            f"<td>{record.answered} of {record.observed}</td>"
            f'<td><a href="/{ARCHIVE_PATH}/{html.escape(record.endpoint_id)}/">'
            "every observation</a></td></tr>"
        )
    return "".join(rows)


def _kind_table(kind: str, records_in_kind: list[Record]) -> str:
    label = KIND_LABELS.get(kind, kind)
    return (
        f"<h2>{html.escape(label)}</h2>"
        '<table class="usa-table usa-table--striped">'
        f"<caption>{html.escape(label)}, ordered by measured answered share</caption>"
        '<thead><tr><th scope="col">Position</th><th scope="col">Endpoint</th>'
        '<th scope="col">Answered share</th><th scope="col">Answered of observed</th>'
        '<th scope="col">Full record</th></tr></thead>'
        f"<tbody>{_rows(records_in_kind)}</tbody></table>"
    )


def _below_floor_section(waiting: list[Record], total: int) -> str:
    if not waiting:
        return (
            "<h2>Endpoints below the reporting floor</h2>"
            f"<p>None. Every one of the {total} endpoints in the registry has at least "
            f"{MIN_OBSERVATIONS_TO_REPORT} recorded observations.</p>"
        )
    items = "".join(
        f'<li><a href="/{ARCHIVE_PATH}/{html.escape(record.endpoint_id)}/">'
        f"{html.escape(record.name)}</a>: {record.observed} "
        f"{'observation' if record.observed == 1 else 'observations'}, "
        f"{MIN_OBSERVATIONS_TO_REPORT - record.observed} more needed</li>"
        for record in waiting
    )
    return (
        "<h2>Endpoints below the reporting floor</h2>"
        f"<p>{len(waiting)} of {total} endpoints have fewer than "
        f"{MIN_OBSERVATIONS_TO_REPORT} recorded observations, so no share is published for them "
        "and they hold no position above. They are named here rather than left out: an endpoint "
        "missing from a table reads as an endpoint nobody watched, which is the opposite of what "
        "is true.</p>"
        f"<ul>{items}</ul>"
    )


def page(built: list[Record], origin: str) -> Page:
    """The availability page: ordered within each kind, with the waiting population named."""
    eligible = ranked(built)
    waiting = below_floor(built)
    by_kind: dict[str, list[Record]] = {}
    for record in eligible:
        by_kind.setdefault(record.kind, []).append(record)
    tables = (
        "".join(_kind_table(kind, by_kind[kind]) for kind in sorted(by_kind))
        if by_kind
        else (
            "<h2>Nothing is ordered yet</h2>"
            f"<p>No endpoint has reached {MIN_OBSERVATIONS_TO_REPORT} recorded observations, "
            "so there is nothing to order. This page fills in as the record accumulates; it "
            "does not rank a shorter record more cautiously, it declines to rank it.</p>"
        )
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>Availability</span></li>
</ol></nav>
<p class="eyebrow">Availability</p>
<h1>Which public FHIR endpoints answered, and how often</h1>
<p class="lede">{len(eligible)} of {len(built)} endpoints have enough recorded observations to
state a share. Each kind is ordered on its own; an EHR vendor's sandbox and a payer's
production Patient Access API are not the same kind of thing, and one table over both would be
a comparison this data does not support.</p>
<div class="usa-alert usa-alert--info usa-alert--slim"><div class="usa-alert__body">
<p class="usa-alert__text">A position here orders one measurement: whether a public address
answered a request for its <code>/metadata</code> document, from three hosts on one provider's
network, over the recorded window. It is not a service-level measurement, not an uptime
guarantee, and not a statement about an organization. A run where no vantage reached an
endpoint is published as not reached from that network on that day, which is a fact about the
measurement as much as about the endpoint. See <a href="/how-we-grade/">how we grade</a>.</p>
</div></div>
{tables}
{_below_floor_section(waiting, len(built))}
<h2>Why fourteen</h2>
<p>An answered share over two or three observations moves by tens of points on a single missed
day, so it reports the sampling rather than the endpoint. Fourteen is the floor this project
already applies to the availability sentence on every scorecard, and the same number governs
here so that two surfaces of the same record cannot disagree. The full record for every
endpoint, above the floor or below it, is at
<a href="/{ARCHIVE_PATH}/">the observation record</a>.</p>
{
        json_ld(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": "Measured availability of public FHIR endpoints",
                "description": (
                    "Answered share for publicly observable FHIR endpoints with at least "
                    f"{MIN_OBSERVATIONS_TO_REPORT} recorded observations, ordered within each kind."
                ),
                "url": f"{origin}/{LEADERBOARD_PATH}/",
                "isAccessibleForFree": True,
            }
        )
    }
"""
    return Page(
        path=LEADERBOARD_PATH,
        title="Availability of public FHIR endpoints, measured",
        description=(
            f"Answered share for the {len(eligible)} FHIR endpoints with at least "
            f"{MIN_OBSERVATIONS_TO_REPORT} recorded observations, ordered within each kind, with "
            "the endpoints below the floor named rather than dropped."
        ),
        body=body,
        priority="0.7",
    )
