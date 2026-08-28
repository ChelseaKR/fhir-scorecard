"""The observation record, made browsable.

ROADMAP phase 2 listed a historical archive as *"the part nobody else has"* and marked it
partial: the `capability-history` branch has accrued one commit per day on which an observation
changed since 2026-08-05, and none of it was readable without cloning a branch and reading
JSON. This module publishes it.

What it publishes is exactly what `history.json` holds and nothing derived beyond counting:
which dates an endpoint was observed on, whether it answered on each, and when it was first and
last seen. Two rules follow from that and are enforced here rather than left to the templates.

**An endpoint with no observations says so.** It does not render an empty table, a zero, or a
zero percent. A rate over no observations is the "count without a denominator" this project
exists to refuse, and the same reasoning that keeps `NOT_OBSERVED` out of the letter grades
keeps an unobserved endpoint out of the availability figures.

**A rate is printed only where the floor is met.** `drift.MIN_OBSERVATIONS_TO_REPORT` is 14, and
the reason is already recorded in `drift.Availability.summary`: a percentage off two data points
is noise dressed as a metric. The archive respects the same floor, prints the raw counts below
it, and says how many more observations the endpoint needs.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from fhir_scorecard.drift import META_KEY, MIN_OBSERVATIONS_TO_REPORT
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.site import Page, json_ld

#: Site path of the archive index, used by the archive pages and the pages that link them.
ARCHIVE_PATH = "history"


@dataclass(frozen=True)
class Observation:
    date: str
    up: bool


@dataclass(frozen=True)
class Record:
    """One endpoint's observation record, read from ``history.json`` and nothing else."""

    endpoint_id: str
    name: str
    observations: tuple[Observation, ...]
    first_seen: str | None
    last_seen: str | None

    @property
    def answered(self) -> int:
        return sum(1 for observation in self.observations if observation.up)

    @property
    def observed(self) -> int:
        return len(self.observations)

    @property
    def reportable(self) -> bool:
        """Whether enough observations exist to state a rate. See the module docstring."""
        return self.observed >= MIN_OBSERVATIONS_TO_REPORT

    @property
    def rate_percent(self) -> int | None:
        """Answered share as a whole percent, or ``None`` where the floor is not met.

        ``None`` is not zero and must never be rendered as a number. Every caller here either
        prints the counts instead or says the endpoint is below the floor.
        """
        if not self.reportable:
            return None
        return round(100 * self.answered / self.observed)

    def summary(self) -> str:
        """One sentence a reader can act on, whichever side of the floor this record is."""
        if not self.observed:
            return "no observations recorded yet"
        percent = self.rate_percent
        if percent is None:
            short = MIN_OBSERVATIONS_TO_REPORT - self.observed
            return (
                f"answered {self.answered} of {self.observed} checks so far. No rate is "
                f"published below {MIN_OBSERVATIONS_TO_REPORT} observations; this record needs "
                f"{short} more"
            )
        return f"answered {self.answered} of {self.observed} recorded checks ({percent}%)"


def records(history: dict[str, Any], cards: list[Scorecard]) -> list[Record]:
    """One record per graded endpoint, in the order the cards were graded.

    Keyed off the cards rather than off the history file, so an endpoint the history remembers
    but the registry no longer grades does not reappear on the site as though it were still
    being watched, and a newly added endpoint with no history yet still gets a page saying so.
    """
    built = []
    for card in cards:
        entry = history.get(card.endpoint_id)
        entry = entry if isinstance(entry, dict) else {}
        raw = entry.get("observations")
        observations = tuple(
            Observation(date=str(item.get("date")), up=bool(item.get("up")))
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict) and item.get("date")
        )
        built.append(
            Record(
                endpoint_id=card.endpoint_id,
                name=card.name,
                observations=observations,
                first_seen=entry.get("first_seen"),
                last_seen=entry.get("last_seen"),
            )
        )
    return built


def window(built: list[Record]) -> tuple[str, str] | None:
    """The first and last date any endpoint was observed on, or ``None`` if none were."""
    dates = sorted({observation.date for record in built for observation in record.observations})
    return (dates[0], dates[-1]) if dates else None


def history_json(record: Record, generated_at: str) -> str:
    """The machine-readable record, carrying the same refusals the page does."""
    return json.dumps(
        {
            "endpoint_id": record.endpoint_id,
            "name": record.name,
            "generated_at": generated_at,
            "first_seen": record.first_seen,
            "last_seen": record.last_seen,
            "observations_recorded": record.observed,
            "observations_answered": record.answered,
            # Null, never 0, below the floor: a consumer that saw a number here would have no
            # way to tell a real 0% from "not enough observations to say".
            "answered_percent": record.rate_percent,
            "minimum_observations_to_report": MIN_OBSERVATIONS_TO_REPORT,
            "observations": [
                {"date": observation.date, "answered": observation.up}
                for observation in record.observations
            ],
        },
        indent=2,
        sort_keys=True,
    )


def _observation_rows(record: Record) -> str:
    return "".join(
        f"<tr><td>{html.escape(observation.date)}</td>"
        f"<td>{'answered' if observation.up else 'did not answer'}</td></tr>"
        for observation in reversed(record.observations)
    )


def _record_body(record: Record) -> str:
    if not record.observations:
        return (
            '<p class="lede">No observations are recorded for this endpoint yet.</p>'
            "<p>It entered the registry more recently than the observation record, or every "
            "run since it was added failed to complete. Either way there is nothing here to "
            "read, and an empty table with a zero in it would say something this project has "
            "not measured.</p>"
        )
    return (
        f'<p class="lede">{html.escape(record.summary())}.</p>'
        '<table class="usa-table usa-table--striped">'
        "<caption>Every recorded observation, most recent first</caption>"
        '<thead><tr><th scope="col">Date</th><th scope="col">Result</th></tr></thead>'
        f"<tbody>{_observation_rows(record)}</tbody></table>"
    )


def record_page(record: Record, origin: str) -> Page:
    """One endpoint's observation record."""
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item">
<a href="/{ARCHIVE_PATH}/" class="usa-breadcrumb__link"><span>Observation record</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>{html.escape(record.name)}</span></li>
</ol></nav>
<p class="eyebrow">Observation record</p>
<h1>{html.escape(record.name)}: every observation on record</h1>
{_record_body(record)}
<h2>What this record is</h2>
<p>One row per day a probing run recorded a result for this endpoint. It says whether the
endpoint answered, not whether what it answered with was any good; the
<a href="/endpoint/{html.escape(record.endpoint_id)}/">current scorecard</a> is where the
grade and its findings live. A day with no row is a day nothing was recorded, which is a fact
about this project rather than about the endpoint.</p>
<p>The same record as JSON:
<a href="/api/{ARCHIVE_PATH}/{html.escape(record.endpoint_id)}.json">
api/{ARCHIVE_PATH}/{html.escape(record.endpoint_id)}.json</a>.</p>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">Observational snapshots of a public surface, not audits or compliance
determinations. Reachability is measured from one network; see
<a href="/how-we-grade/">how we grade</a>.</p>
</div></div>
"""
    return Page(
        path=f"{ARCHIVE_PATH}/{record.endpoint_id}",
        title=f"{record.name}: FHIR endpoint observation record",
        description=(
            f"Every recorded daily observation of {record.name}'s public FHIR endpoint: "
            f"{record.summary()}."
        ),
        body=body,
        priority="0.6",
    )


def _index_rows(built: list[Record], origin: str) -> str:
    rows = []
    for record in sorted(built, key=lambda r: r.name.lower()):
        percent = record.rate_percent
        rate = "not enough observations" if percent is None else f"{percent}%"
        rows.append(
            f'<tr><th scope="row"><a href="/{ARCHIVE_PATH}/{html.escape(record.endpoint_id)}/">'
            f"{html.escape(record.name)}</a></th>"
            f"<td>{record.observed}</td><td>{record.answered}</td><td>{rate}</td>"
            f"<td>{html.escape(record.first_seen or 'not yet observed')}</td></tr>"
        )
    return "".join(rows)


def index_page(built: list[Record], origin: str, mode: str = "live") -> Page:
    """The archive index: every endpoint's record, and the window the whole record covers."""
    span = window(built)
    below = [record for record in built if not record.reportable]
    covered = (
        f"Observations run from {html.escape(span[0])} to {html.escape(span[1])}."
        if span
        else "No observations are recorded yet."
    )
    floor_note = (
        f"<p>{len(below)} of {len(built)} endpoints have fewer than "
        f"{MIN_OBSERVATIONS_TO_REPORT} observations, so no rate is published for them. They are "
        "listed here with their counts rather than left out: an endpoint missing from a table "
        "reads as an endpoint that was not watched.</p>"
        if below
        else f"<p>Every endpoint listed has at least {MIN_OBSERVATIONS_TO_REPORT} observations.</p>"
    )
    fixture_note = (
        ""
        if mode == "live"
        else '<div class="usa-alert usa-alert--warning usa-alert--slim"><div class="usa-alert__body">'
        f'<p class="usa-alert__text">This record was written by a run in <strong>{html.escape(mode)}</strong> '
        "mode. Fixture observations describe captured documents, not the internet, and must not be "
        "read as availability.</p></div></div>"
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>Observation record</span></li>
</ol></nav>
{fixture_note}
<p class="eyebrow">Observation record</p>
<h1>What these endpoints have done, day by day</h1>
<p class="lede">{covered} Every endpoint in the registry is probed on a daily schedule, and
every result is kept. This is the record, not a summary of it.</p>
{floor_note}
<table class="usa-table usa-table--striped">
<caption>Observation record by endpoint</caption>
<thead><tr>
<th scope="col">Endpoint</th><th scope="col">Observations</th><th scope="col">Answered</th>
<th scope="col">Answered share</th><th scope="col">First observed</th>
</tr></thead>
<tbody>{_index_rows(built, origin)}</tbody></table>
<h2>How to read it</h2>
<p>An observation says the endpoint answered a request for its public
<code>/metadata</code> document, not that what came back was any good. The grade is a separate
question and lives on each endpoint's scorecard. A day with no observation is a day this
project recorded nothing, which happens when a scheduled run does not complete; it is never
evidence about the endpoint.</p>
<p>Reachability is measured from three hosts on one provider's network. One of them reaching an
endpoint settles that it is up; all three failing settles only that it was not reached from
there. See <a href="/how-we-grade/">how we grade</a>.</p>
{
        json_ld(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": "FHIR endpoint observation record",
                "description": (
                    "Daily reachability observations for publicly observable FHIR endpoints, one row "
                    "per endpoint per day observed."
                ),
                "url": f"{origin}/{ARCHIVE_PATH}/",
                "isAccessibleForFree": True,
            }
        )
    }
"""
    return Page(
        path=ARCHIVE_PATH,
        title="Observation record: daily availability of public FHIR endpoints",
        description=(
            "Every recorded daily observation for the FHIR endpoints in this registry, with the "
            "endpoints below the reporting floor named rather than dropped."
        ),
        body=body,
        priority="0.7",
    )


def mode_of(history: dict[str, Any]) -> str:
    """Whether this history was written by live runs or by fixture runs.

    Published on the archive index so a reader of a locally built site is never shown fixture
    observations as though they were measurements of the real internet.
    """
    meta = history.get(META_KEY)
    return str(meta.get("mode")) if isinstance(meta, dict) and meta.get("mode") else "unknown"
