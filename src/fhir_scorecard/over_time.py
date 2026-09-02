"""What changed across the recorded window, month by month.

ROADMAP phase 5 asked for a *"conformance-over-time report, published monthly, with the
write-up as its front door"*. This is the computed half. Every figure comes from
``history.json`` and the graded payload; nothing on the page is typed, and the prose sections a
write-up would need are the maintainer's to write, which the page says rather than generating
filler.

The months are read out of the observation record itself rather than persisted, so the report
is regenerated whole on every publish and needs no new state anywhere. A month appears because
the record contains observations in it.

**Both edges of that window are stated on the page, because both were reading as facts about
the endpoints.** ``drift`` keeps a bounded rolling window of observations and writes
``first_seen`` once without ever advancing it, so endpoints eventually enter the record in a
month this report no longer covers, and every section says "No endpoint entered the record this
month" - each sentence true of its month, the run of them reading as though nothing ever
joined. And the last month covered runs only to the most recent observation, so its counts are
a part of a month set beside whole ones. :func:`entered_before_the_window` and
:func:`last_observed` answer both from the record itself; nothing here reads a clock, which is
what keeps the page regenerable byte-for-byte from committed inputs.

**What this report cannot say, and why.** It does not report grade changes. ``history.json``
retains availability observations and a capability fingerprint; it has never retained a grade,
so no run can look up what an endpoint was graded last month. Deriving one from the fingerprint
would be inventing a measurement nobody took. The honest section is the one that says so, and
the limitation is on the page rather than only in this docstring, because a reader who does not
find grades on a conformance-over-time report is owed the reason.
"""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass

from fhir_scorecard.archive import Record
from fhir_scorecard.site import Page, json_ld

#: Site path of the report.
OVER_TIME_PATH = "over-time"


@dataclass(frozen=True)
class MonthlySection:
    """One calendar month of the record, computed from it and nothing else."""

    month: str
    observed: tuple[str, ...]
    entered: tuple[str, ...]
    answered_every_day: tuple[str, ...]
    missed_at_least_once: tuple[str, ...]
    changes: tuple[tuple[str, str, tuple[str, ...]], ...]
    returns: tuple[tuple[str, str, int], ...]
    #: How many endpoints entered the record before the first month this report covers. A
    #: report-level fact, carried on every section on purpose: "No endpoint entered the record
    #: this month" is the sentence it qualifies, and a reader meets that sentence inside a
    #: section rather than at the top of the page. See :func:`entered_before_the_window`.
    entered_before_the_window: int = 0

    @property
    def observations(self) -> int:
        return len(self.observed)


def _months(records: list[Record]) -> list[str]:
    return sorted(
        {observation.date[:7] for record in records for observation in record.observations}
    )


def entered_before_the_window(records: list[Record], months: list[str]) -> int:
    """Endpoints whose ``first_seen`` predates the earliest month this report can cover.

    The months come out of ``record.observations``, which ``drift`` bounds at
    ``_MAX_OBSERVATIONS`` (120, roughly four months of daily runs). ``first_seen`` is written
    once and never advances. So after enough runs every endpoint's ``first_seen`` falls
    permanently outside the retained window, every section renders "No endpoint entered the
    record this month", and each of those sentences is *true* about its month and reads as
    though nothing joined this project at all.

    The sentence is not the fix; the missing count is. This is the number of endpoints the
    months below cannot show entering, and the page prints it rather than leaving a reader to
    infer a population of zero from a run of empty sections.
    """
    if not months:
        return 0
    return sum(
        1
        for record in records
        if record.first_seen is not None and record.first_seen[:7] < months[0]
    )


def last_observed(records: list[Record]) -> str | None:
    """The most recent observation date on the record, or ``None`` where there are none.

    The final month a report covers runs to this date, not to the end of its calendar month,
    and the page says so. Answering it from the record rather than from a clock is deliberate:
    nothing in this module reads ``now()``, which is what lets the report be regenerated
    byte-for-byte from committed inputs.
    """
    dates = [observation.date for record in records for observation in record.observations]
    return max(dates) if dates else None


def _month_section(month: str, records: list[Record], entered_before: int = 0) -> MonthlySection:
    observed: list[str] = []
    entered: list[str] = []
    clean: list[str] = []
    missed: list[str] = []
    changes: list[tuple[str, str, tuple[str, ...]]] = []
    returns: list[tuple[str, str, int]] = []
    for record in records:
        in_month = [o for o in record.observations if o.date.startswith(month)]
        if in_month:
            observed.append(record.name)
            (clean if all(o.up for o in in_month) else missed).append(record.name)
        if (record.first_seen or "").startswith(month):
            entered.append(record.name)
        for change in record.changes:
            if change.date.startswith(month):
                changes.append((change.date, record.name, change.changes))
        for item in record.returns:
            # A return group is placed by the month it was last seen in, which is the only date
            # on it that the record guarantees falls inside a single month.
            if item.last_return.startswith(month):
                returns.append((item.window, record.name, item.times))
    return MonthlySection(
        month=month,
        observed=tuple(sorted(observed)),
        entered=tuple(sorted(entered)),
        answered_every_day=tuple(sorted(clean)),
        missed_at_least_once=tuple(sorted(missed)),
        changes=tuple(sorted(changes)),
        returns=tuple(sorted(returns)),
        entered_before_the_window=entered_before,
    )


def sections(records: list[Record]) -> list[MonthlySection]:
    """One section per calendar month the record holds observations in, oldest first."""
    months = _months(records)
    before = entered_before_the_window(records, months)
    return [_month_section(month, records, before) for month in months]


def _list_or_sentence(names: tuple[str, ...], nothing: str) -> str:
    if not names:
        return f"<p>{nothing}</p>"
    items = "".join(f"<li>{html.escape(name)}</li>" for name in names)
    return f"<ul>{items}</ul>"


def _changes_block(section: MonthlySection) -> str:
    if not section.changes:
        return (
            "<p>No endpoint changed what it declares this month. That is a result, not a gap: "
            "a stable conformance document is the ordinary case and this report says so rather "
            "than printing an empty table.</p>"
        )
    items = "".join(
        f"<li><strong>{html.escape(date)}</strong> {html.escape(name)}"
        + (
            "<ul>"
            + "".join(f"<li><code>{html.escape(one)}</code></li>" for one in detail)
            + "</ul>"
            if detail
            else ": recorded with no detail on the record"
        )
        + "</li>"
        for date, name, detail in section.changes
    )
    return (
        f"<p>{len(section.changes)} recorded "
        f"{'change' if len(section.changes) == 1 else 'changes'} to what an endpoint "
        f"declares.</p><ol>{items}</ol>"
    )


def _returns_block(section: MonthlySection) -> str:
    if not section.returns:
        return ""
    items = "".join(
        f"<li>{html.escape(name)}: returned {times} "
        f"{'time' if times == 1 else 'times'} ({html.escape(window)})</li>"
        for window, name, times in section.returns
    )
    return (
        "<h4>Declarations returned to</h4>"
        "<p>Counted apart from the changes above, because one hostname in front of more than "
        f"one backend is not a run of releases.</p><ul>{items}</ul>"
    )


def _nobody_entered(section: MonthlySection) -> str:
    """The empty "Entered the record" sentence, qualified where it would mislead.

    True of its month either way. Unqualified in a report whose window still reaches back to
    every endpoint's first sighting; qualified once it does not, because a run of sections all
    saying nobody entered reads as a fact about the registry and is a fact about how far back
    the retained observations go.
    """
    if not section.entered_before_the_window:
        return "No endpoint entered the record this month."
    count = section.entered_before_the_window
    return (
        f"No endpoint entered the record this month. {count} "
        f"{'endpoint' if count == 1 else 'endpoints'} entered before the earliest month this "
        "report covers, so no section below can show them entering."
    )


def _section_html(section: MonthlySection) -> str:
    return f"""
<h3>{html.escape(section.month)}</h3>
<p>{section.observations} endpoints were observed at least once this month.
{len(section.answered_every_day)} answered every check they were given and
{len(section.missed_at_least_once)} missed at least one. An endpoint absent from both numbers
was not observed at all this month, which is a fact about this project's runs.</p>
<h4>Entered the record</h4>
{_list_or_sentence(section.entered, _nobody_entered(section))}
<h4>Missed at least one check</h4>
{
        _list_or_sentence(
            section.missed_at_least_once,
            "Every endpoint observed this month answered every check it was given.",
        )
    }
<h4>Changed what it declares</h4>
{_changes_block(section)}
{_returns_block(section)}
"""


def _what_the_window_leaves_out(records: list[Record], built: list[MonthlySection]) -> str:
    """The two edges of the window, stated rather than left for a reader to discover.

    Both are about the *record*, not about the endpoints, and both were being presented as
    though they were about the endpoints.

    The far edge: ``drift`` keeps a bounded rolling window of observations and never advances
    ``first_seen``, so endpoints that entered before the retained window exist and no section
    can show them entering. The near edge: the last month covered runs only to the most recent
    observation, so its counts are a partial month and a reader comparing it to the month
    before it is comparing a part to a whole. Neither sentence needs a clock; both come out of
    the record, which is what keeps this page byte-for-byte regenerable.
    """
    parts = []
    before = built[0].entered_before_the_window if built else 0
    if before:
        parts.append(
            f"<p>{before} {'endpoint' if before == 1 else 'endpoints'} entered the record "
            f"before {html.escape(built[0].month)}, the earliest month this report still "
            "covers, so no section below shows them entering. The observation record is a "
            "bounded rolling window; an endpoint's first sighting eventually falls out of "
            "it.</p>"
        )
    latest = last_observed(records)
    if latest is not None and built:
        parts.append(
            f"<p>{html.escape(built[-1].month)} is covered only as far as "
            f"{html.escape(latest)}, the most recent observation on the record. Its counts are "
            "a part of that month, not all of it.</p>"
        )
    return "".join(parts)


def page(records: list[Record], origin: str) -> Page:
    """The conformance-over-time report for the whole recorded window."""
    built = sections(records)
    tally: Counter[str] = Counter()
    for section in built:
        tally["changes"] += len(section.changes)
        tally["returns"] += len(section.returns)
    window = f"{built[0].month} to {built[-1].month}" if built else "no month yet"
    if not built:
        summary = (
            "<p>The observation record holds no observations, so there is no window to report "
            "over. This page fills in as the record accumulates.</p>"
        )
        body_sections = ""
    else:
        summary = (
            f"<p>{len(records)} endpoints, {len(built)} "
            f"{'month' if len(built) == 1 else 'months'} of record, "
            f"{tally['changes']} recorded declaration "
            f"{'change' if tally['changes'] == 1 else 'changes'} and {tally['returns']} "
            f"recorded {'return' if tally['returns'] == 1 else 'returns'}.</p>"
            + _what_the_window_leaves_out(records, built)
        )
        body_sections = "".join(_section_html(section) for section in built)
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>Over time</span></li>
</ol></nav>
<p class="eyebrow">Conformance over time</p>
<h1>What changed across the record, month by month</h1>
<p class="lede">Covering {html.escape(window)}. Every figure below is computed from the
observation record on each publish; none of it is stored, and none of it is written by hand.</p>
{summary}
<div class="usa-alert usa-alert--info usa-alert--slim"><div class="usa-alert__body">
<p class="usa-alert__text"><strong>This report does not say whether grades moved.</strong> The
observation record retains availability and a capability fingerprint; it has never retained a
grade, so no run can look up what an endpoint was graded last month. Deriving one from the
fingerprint would be inventing a measurement nobody took. What is reported instead is what the
record does hold: who was observed, who answered, and what changed in what they declare.</p>
</div></div>
<h2>Month by month</h2>
{body_sections}
<h2>What a reader should take from this</h2>
<p>Nothing here is a trend. A month is a window over one project's probing schedule, and a
month with fewer observations is a month with fewer runs, not a month with less availability.
The per-endpoint record at <a href="/history/">the observation record</a> carries every
observation with its date, which is the evidence these counts are drawn from.</p>
<p>The narrative front door this report was planned to have is a piece of writing, not a
computation, and is deliberately not generated here.</p>
{
        json_ld(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": "Public FHIR endpoint conformance over time",
                "description": (
                    "Month-by-month record of which public FHIR endpoints were observed, which answered "
                    "every check, and which changed what they declare."
                ),
                "url": f"{origin}/{OVER_TIME_PATH}/",
                "isAccessibleForFree": True,
            }
        )
    }
"""
    return Page(
        path=OVER_TIME_PATH,
        title="Conformance over time: what changed across the record",
        description=(
            f"Month-by-month conformance record covering {window}: who was observed, who "
            "answered every check, and what changed in what they declare."
        ),
        body=body,
        priority="0.6",
    )
