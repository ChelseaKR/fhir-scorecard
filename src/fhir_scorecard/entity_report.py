"""The single-endpoint report: one organization's own record, free and unauthenticated.

The endpoint page answers "what is this endpoint's grade". This answers the question an
organization asks about itself: *what did you observe, what did you not observe, and what
would we have to change?* It is the same evidence, re-cut for the reader who is responsible
for the endpoint rather than shopping across them, and it is built to be printed or handed to
a compliance reviewer as it stands.

**Free, and deliberately so.** No paywall, no email gate, no sign-up, no analytics, no cookie.
The report is a page like every other page on this site: indexable, in the sitemap, linked
from the endpoint it describes, and reachable by anyone with the URL.

**Three states, carried through rather than flattened.** Every published surface here
distinguishes a check that ran, a check that was asked from every reporting vantage and
answered by none, and a check that was never asked because nothing was retrieved for it to
read. Only the first can produce a score, and only the first can produce an action. A report
that listed "declare ``fhirVersion``" under an endpoint whose document nobody retrieved would
be the #135 defect in its most expensive form -- a named organization handed a to-do list
derived from an absence -- so the action list is built from ``observed`` findings only, and a
test holds that.

**Both vantage numbers travel with every claim.** An endpoint reachable from one of three
vantages is a different fact from one reachable from three of three, and the difference is not
resolvable into a verdict. The summary states the reached count, the reporting count, and how
many networks those vantages sit on, because several hosts on one provider's network are one
network's view sampled several times.

**Why a page and not a generated document.** A PDF renderer would be a second layout engine,
a second accessibility surface, and a second thing to keep true; the site already has one of
each, gated. So the report is HTML with print rules in the shared stylesheet: a reader who
needs a file uses the browser's print or "Save as PDF", and what they get is what the audit,
the accessibility gate and the weight budget already examined.

**What this cannot measure.** How many of these anybody reads. The site is static GitHub Pages
output and this repository adds no analytics or tracking (``docs/RESPONSIBLE-TECH-AUDITS.md``),
so there is no server log, no counter, and no privacy-respecting way to count views. The only
demand signal that exists without collecting anything about a person is an inbound request, and
``/claim/`` is already that channel; the report links to it. A view counter is not being added.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from fhir_scorecard.grading import (
    NOT_OBSERVED,
    DimensionScore,
    Finding,
    Scorecard,
)
from fhir_scorecard.site import (
    KIND_LABELS,
    Page,
    dimension_unanswered,
    finding_mark,
    grade_badge,
    json_ld,
    last_answered_words,
    vantage_rows,
)

#: What a publisher would change to make a failing check pass, per finding code, naming the
#: element the grading module actually reads. Rendered only beside an ``observed`` finding that
#: ran and did not pass: see :func:`recoverable`.
#:
#: Every sentence names a document element rather than an outcome, because "improve
#: interoperability" is not something anyone can act on and ``rest.resource.supportedProfile``
#: is. The text is held to the same standard as a finding message: it may describe what this
#: project reads, and it may not invent a requirement. Where a check's subject is an
#: implementation guide's, the citation on the finding is what states it.
ACTIONS: dict[str, str] = {
    "R2": (
        "Reduce the time /metadata takes to answer. The band is measured on the median across "
        "the vantages that answered, and it is deliberately coarse so that a network-path "
        "difference cannot move a grade."
    ),
    "T0": (
        "Serve a FHIR CapabilityStatement at /metadata. What answered was not one, so every "
        "check that reads inside that document was skipped rather than failed."
    ),
    "T1": "Declare the release this server serves, in the CapabilityStatement's fhirVersion.",
    "T2": (
        "Declare what is running, in software.name and software.version. Both are needed; one "
        "without the other does not pass."
    ),
    "T3": (
        "Declare the resource types this server supports, as rest.resource[].type entries. "
        "Five or more passes, and so does a narrower set -- two to four -- where every one of "
        "them documents its interactions."
    ),
    "T4": (
        "Give each declared resource its rest.resource[].interaction list. A resource listed "
        "with no interactions tells a client nothing it can act on."
    ),
    "I0": (
        "Serve a readable CapabilityStatement at /metadata. It is the same document T0 names, "
        "and the profile and security declarations are read from inside it."
    ),
    "I1": (
        "Declare the implementation guide's canonical URLs. Any of five conformance elements "
        "is read: rest.resource.supportedProfile, rest.resource.profile, instantiates, "
        "imports, or meta.profile. The finding above names where this project looked and what "
        "it found there."
    ),
    "I2": (
        "Publish .well-known/smart-configuration at the base URL, carrying at least "
        "authorization_endpoint and token_endpoint."
    ),
    "I3": (
        "Declare the authorization surface in the CapabilityStatement itself, at "
        "rest[].security.service, as well as in SMART discovery."
    ),
    "I4": (
        "Move the claim out of prose. The document names an implementation guide in its text; "
        "adding rest.resource.supportedProfile entries makes it a claim a client can read."
    ),
}

#: Finding codes that never carry an action, and why. Held against the published method by
#: ``tests/test_entity_report.py``, so a new code cannot ship without a decision about it.
#:
#: ``R1`` is here because a failing R1 is not a finding about the endpoint at all: after #138
#: it publishes no score, withholds its whole scale, and says only that these vantages were not
#: answered. What it needs is a vantage this project does not have, which is the subject of the
#: "If this does not match what you see" section rather than a to-do item for a publisher.
#: ``NR`` is the absence of a check, and an absence has nothing to act on.
NO_ACTION: frozenset[str] = frozenset({"R1", "NR"})


def page_path(endpoint_id: str) -> str:
    """Site-relative directory of one endpoint's report."""
    return f"endpoint/{endpoint_id}/report"


def recoverable(card: Scorecard) -> list[tuple[DimensionScore, Finding, int]]:
    """Every check that ran, did not pass, and has points behind it, worst first.

    ``observed`` is the gate and it is not negotiable. A check that did not run produced no
    finding about this endpoint, so it cannot produce an instruction to this endpoint's
    publisher either; a report that ranked unobserved checks by the points they "cost" would be
    handing a named organization a list of things it has not been shown to have done wrong.

    Measured 2026-09-12, and worth writing down because it says which of the two refusals is
    doing the work: every ``observed=False`` finding this grader can currently produce also has
    ``max_points == 0`` -- its real scale lives in ``withheld_points`` -- so the points check is
    what fires today and the ``observed`` check is defence against a grader that changes. Both
    are tested, the second against a finding built by hand, because a guard no fixture can reach
    is a guard a negative control cannot tell apart from ``if True``.

    Ties are broken by the dimension's published order and then by code, so two runs of the
    same card render the same list.
    """
    ranked: list[tuple[int, int, str, DimensionScore, Finding, int]] = []
    for position, dimension in enumerate(card.dimensions):
        for finding in dimension.findings:
            if not finding.observed or finding.ok or finding.max_points <= 0:
                continue
            gap = finding.max_points - finding.points
            if gap <= 0:
                continue
            ranked.append((-gap, position, finding.code, dimension, finding, gap))
    ranked.sort(key=lambda row: row[:3])
    return [(dimension, finding, gap) for *_, dimension, finding, gap in ranked]


def notes(card: Scorecard) -> list[tuple[DimensionScore, Finding]]:
    """Observed findings worth no points in either direction that still say something to do.

    ``I4`` is the one this exists for: a document naming CARIN in prose and declaring nothing.
    It scores nothing deliberately, and ranking it beside checks that carry points would
    misrepresent it -- but dropping it would lose the single most actionable sentence on some
    reports. The "not applicable" notes (``ok`` and worth nothing, on a Provider Directory API)
    are excluded here and appear in the dimension detail, where they belong.
    """
    return [
        (dimension, finding)
        for dimension in card.dimensions
        for finding in dimension.findings
        if finding.observed and not finding.ok and finding.max_points == 0
    ]


def _states(card: Scorecard) -> tuple[int, int, int]:
    """How many checks ran, were answered by nobody, and were never asked."""
    findings = [f for dimension in card.dimensions for f in dimension.findings]
    ran = sum(1 for f in findings if f.observed)
    unanswered = sum(1 for f in findings if not f.observed and f.unanswered)
    unasked = sum(1 for f in findings if not f.observed and not f.unanswered)
    return ran, unanswered, unasked


def _dimension_scale(dimension: DimensionScore) -> int:
    """The dimension's whole scale, including the part this run could not measure."""
    return sum(f.max_points for f in dimension.findings) + dimension.withheld_points


def absence_sentence(dimension: DimensionScore) -> str:
    """Why this dimension publishes no score, in the words of the state that caused it.

    Three causes, three sentences, and they are different facts. "Nobody answered us" is a
    dated observation about an endpoint; "nothing was retrieved" is the absence of one; "part
    of the scale was never measured" is a measurement this project refuses to rescale. The
    empty string where a score exists, so a caller can render it unconditionally.
    """
    if dimension.score is not None:
        return ""
    scale = _dimension_scale(dimension)
    if dimension_unanswered(dimension):
        cause = (
            "every check here was asked from every reporting vantage on this run and answered "
            "by none"
        )
    elif dimension.findings and all(not f.observed for f in dimension.findings):
        cause = "no check here ran, because nothing was retrieved for it to read"
    else:
        cause = "part of this dimension's scale covers a check this run could not make"
    if dimension.withheld_points and scale:
        return (
            f"No score is published: {cause}, so {dimension.withheld_points} of its {scale} "
            "points went unmeasured. A percentage over the remainder would be on a different "
            "scale from every other percentage on this site, so none is published."
        )
    return f"No score is published: {cause}."


def _score_words(dimension: DimensionScore) -> str:
    if dimension.score is None:
        return "no score" if not dimension_unanswered(dimension) else "no answer"
    return f"{dimension.score} out of 100"


def _findings_table(dimension: DimensionScore) -> str:
    rows = ""
    for finding in dimension.findings:
        _, glyph, prefix = finding_mark(finding)
        rows += (
            "<tr>"
            f'<th scope="row"><a href="/how-we-grade/#{html.escape(finding.code)}">'
            f"{html.escape(finding.code)}</a></th>"
            f'<td><span aria-hidden="true">{glyph}</span> {html.escape(prefix)}</td>'
            f"<td>{html.escape(finding.message)}</td>"
            "</tr>"
        )
    return (
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        f'aria-label="{html.escape(dimension.title)} checks">'
        '<table class="usa-table usa-table--striped report-checks">'
        f"<caption>{html.escape(dimension.title)}: {html.escape(_score_words(dimension))}"
        "</caption>"
        '<thead><tr><th scope="col">Check</th><th scope="col">State</th>'
        '<th scope="col">What this run observed</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _dimension_sections(card: Scorecard) -> str:
    out = ""
    for dimension in card.dimensions:
        absence = absence_sentence(dimension)
        out += (
            '<section class="report-dimension">'
            f"<h3>{html.escape(dimension.title)}</h3>"
            + (f"<p>{html.escape(absence)}</p>" if absence else "")
            + _findings_table(dimension)
            + "</section>"
        )
    return out


def _reach_sentence(card: Scorecard) -> str:
    """The reachability claim, with both numbers and the network count attached to it.

    Never one number. "Not reached" over three hosts on one provider's network is a much weaker
    statement than "not reached" over three networks, and a reader who is handed only the
    vantage count cannot tell which they were given.
    """
    reports = card.vantage_reports
    if not reports:
        return (
            "One vantage reported on this endpoint and this run published no per-vantage "
            "breakdown, so the breadth of the agreement is unknown: "
            + ("it answered." if card.reachable else "it did not answer.")
        )
    reached = sum(1 for r in reports if r.reachable)
    networks = len({r.network for r in reports})
    return (
        f"Reached from {reached} of {len(reports)} reporting "
        f"{'vantage' if len(reports) == 1 else 'vantages'}, sitting on "
        f"{networks} {'network' if networks == 1 else 'networks'}. "
        "Vantages on one network are one network's view sampled several times."
    )


def _summary(card: Scorecard, base_url: str, generated_at: str) -> str:
    unobserved = card.grade == NOT_OBSERVED
    return f"""
<section class="evidence-card report-summary">
<p class="eyebrow">{"Status on this run" if unobserved else "Grade on this run"}</p>
<dl class="facts">
  <dt>{"Status" if unobserved else "Grade"}</dt><dd>{grade_badge(card.grade)}</dd>
  <dt>Category</dt><dd>{html.escape(KIND_LABELS.get(card.kind, card.kind))}</dd>
  <dt>Base URL observed</dt><dd><code>{html.escape(base_url)}</code></dd>
  <dt>Reach on this run</dt><dd>{html.escape(_reach_sentence(card))}</dd>
  <dt>Last answered</dt><dd>{last_answered_words(card)}</dd>
  <dt>Availability</dt><dd>{html.escape(card.availability or "not yet recorded")}</dd>
  <dt>Observed</dt><dd>{html.escape(generated_at)}</dd>
</dl>
</section>
"""


def _states_paragraph(card: Scorecard) -> str:
    ran, unanswered, unasked = _states(card)
    total = ran + unanswered + unasked
    return (
        f"<p>This report covers {total} checks. <strong>{ran}</strong> ran. "
        f"<strong>{unanswered}</strong> were asked from every reporting vantage and answered by "
        f"none. <strong>{unasked}</strong> were never asked, because nothing was retrieved for "
        "them to read. The three are different facts and none of them is a zero: a check that "
        "did not run publishes no number, no mark against this endpoint, and nothing below to "
        "act on.</p>"
    )


def _action_items(card: Scorecard) -> str:
    items = ""
    for dimension, finding, gap in recoverable(card):
        action = ACTIONS.get(finding.code)
        if action is None:
            continue
        items += (
            '<li class="report-action">'
            f"<h3>{html.escape(action)}</h3>"
            f'<p class="report-action-worth">Worth up to <strong>{gap}</strong> of the '
            f"{_dimension_scale(dimension)} points in {html.escape(dimension.title)}.</p>"
            f"<p>This run observed: {html.escape(finding.message)}</p>"
            '<p class="finding-links">'
            f'<a href="/how-we-grade/#{html.escape(finding.code)}">'
            f"{html.escape(finding.code)}: what this check is</a>"
            f'<a href="{html.escape(finding.citation)}" rel="nofollow">Spec ↗</a></p>'
            "</li>"
        )
    return items


def _notes_items(card: Scorecard) -> str:
    return "".join(
        '<li class="report-action report-note">'
        f"<h3>{html.escape(ACTIONS[finding.code])}</h3>"
        f'<p class="report-action-worth">Worth no points in either direction, in '
        f"{html.escape(dimension.title)}.</p>"
        f"<p>This run observed: {html.escape(finding.message)}</p>"
        '<p class="finding-links">'
        f'<a href="/how-we-grade/#{html.escape(finding.code)}">'
        f"{html.escape(finding.code)}: what this check is</a></p></li>"
        for dimension, finding in notes(card)
        if finding.code in ACTIONS
    )


def _actions_section(card: Scorecard) -> str:
    items = _action_items(card)
    note_items = _notes_items(card)
    ran, _, _ = _states(card)
    if not items:
        if not ran:
            lede = (
                "Nothing is listed here, because no check in this report ran. This project "
                "observed nothing about this endpoint on this run, and an absence is not a "
                "finding to act on."
            )
        else:
            lede = (
                "Nothing is listed here: every check that ran passed. Checks that did not run "
                "are in the section above, and they are not failures."
            )
        body = f"<p>{lede}</p>"
    else:
        body = (
            "<p>Ordered by the points each would recover. Every item below comes from a check "
            "that actually ran on this run; nothing derived from a check that did not run "
            "appears here. The three dimensions are weighted, so recovering points does not "
            'guarantee a different letter &mdash; <a href="/how-we-grade/">how we grade</a> '
            "gives the weights.</p>"
            f'<ol class="report-actions">{items}</ol>'
        )
    if note_items:
        body += (
            "<h3>Worth no points, and worth knowing</h3>"
            f'<ul class="report-actions">{note_items}</ul>'
        )
    return f"<h2>What would change this</h2>{body}"


def _disagreement_section(card: Scorecard) -> str:
    """The correction channel, said plainly, and strongest where this run reached nothing.

    An endpoint that answered nowhere is the case where this project is most likely to be
    wrong: the vantages it has are hosts on one provider's network, and a source-address rule,
    bot filter, geo rule or rate limit applied to that address space reaches every one of them
    at once and reads exactly like an endpoint being down. Re-probing the endpoints in that
    state from a residential network on 2026-09-12 returned a 2xx for two of them.
    """
    if not card.reachable:
        reports = card.vantage_reports
        networks = len({r.network for r in reports})
        where = (
            f"{len(reports)} {'vantage' if len(reports) == 1 else 'vantages'} on "
            f"{networks} {'network' if networks == 1 else 'networks'}"
            if reports
            else "the vantages it had"
        )
        opening = (
            f"This run was not answered from any of {where}. A rule applied to one network's "
            "address space -- a bot filter, a geo rule, a rate limit -- reaches every vantage "
            "on that network at once, and nothing here can tell that apart from an endpoint "
            "being down. If this endpoint answers from your own network, that difference is "
            "the finding, and this project would rather publish it than keep this page as it "
            "stands."
        )
    else:
        opening = (
            "Every number here describes two public documents at one moment, read from the "
            "vantages listed above. If it does not match what you serve, the difference is "
            "worth knowing."
        )
    return (
        f"<h2>If this does not match what you see</h2><p>{opening}</p>"
        '<p><a class="usa-link" href="/claim/">Correct or dispute this record</a>. There is no '
        "sign-in, no fee, and nothing to buy: this report is free to read, link, print and "
        "hand on, and this project collects nothing about the people who read it.</p>"
    )


def _provenance(card: Scorecard, verified: str) -> str:
    return f"""
<h2>Where this came from</h2>
<p>{html.escape(verified)}</p>
<p>The same run, as data:
<a href="/api/endpoint/{html.escape(card.endpoint_id)}.json">this endpoint's JSON</a>,
<a href="/endpoint/{html.escape(card.endpoint_id)}/capabilities/">what its CapabilityStatement
declares, resource by resource</a>, and
<a href="/history/{html.escape(card.endpoint_id)}/">every observation on record for it</a>,
with the dates it answered and the dates it did not.</p>
<div class="usa-alert usa-alert--info usa-alert--slim site-caveat"><div class="usa-alert__body">
<p class="usa-alert__text">This is an observational snapshot of a public, unauthenticated
surface. It is not an audit, a ranking of care quality, or a statement about anyone's
regulatory compliance. Grades are comparable within a category only. See
<a href="/how-we-grade/">how we grade</a>.</p>
</div></div>
"""


def report_page(
    card: Scorecard,
    *,
    base_url: str,
    verified: str,
    origin: str,
    generated_at: str,
) -> Page:
    """One endpoint's report: what was observed, what was not, and what would change it."""
    unobserved = card.grade == NOT_OBSERVED
    jsonld = json_ld(
        {
            "@context": "https://schema.org",
            "@type": "WebAPI",
            "name": card.name,
            "url": base_url,
            "documentation": f"{origin}/endpoint/{card.endpoint_id}/report/",
            "isAccessibleForFree": True,
        }
    )
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item">
<a href="/endpoint/{html.escape(card.endpoint_id)}/" class="usa-breadcrumb__link">
<span>{html.escape(card.name)}</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>Endpoint report</span></li>
</ol></nav>
<p class="eyebrow">Single-endpoint report</p>
<h1>{html.escape(card.name)}: endpoint report</h1>
<p class="lede">Everything this project observed about this one endpoint, everything it did
not observe, and what would change the result. Free to read, link, print and hand on; there is
nothing here behind a sign-in and nothing to buy.</p>
<p class="report-print-note">Built to print. Use your browser&rsquo;s print or
&ldquo;Save as PDF&rdquo; to keep a dated copy; the page carries its own address and
observation date.</p>
{_summary(card, base_url, generated_at)}
<h2>What was observed, and what was not</h2>
{_states_paragraph(card)}
{_dimension_sections(card)}
{vantage_rows(card)}
{_actions_section(card)}
{_disagreement_section(card)}
{_provenance(card, verified)}
{jsonld}
"""
    return Page(
        path=page_path(card.endpoint_id),
        title=f"{card.name}: FHIR endpoint report",
        description=(
            f"A free single-endpoint report for {card.name}: "
            + (
                "what this run did and did not observe, and why no grade is published."
                if unobserved
                else f"grade {card.grade}, every check that ran, every check that did not, and "
                "what would change the result."
            )
        ),
        body=body,
        priority="0.6",
    )


def pages_for(
    cards: Sequence[Scorecard],
    base_urls: dict[str, str],
    verifications: dict[str, str],
    origin: str,
    generated_at: str,
) -> list[Page]:
    """A report for every graded endpoint. Every endpoint gets one, including the ones nothing
    answered: an organization whose endpoint this run could not reach is the one most likely to
    want the page that says so in full."""
    return [
        report_page(
            card,
            base_url=base_urls.get(card.endpoint_id, ""),
            verified=verifications.get(card.endpoint_id, "verification record unavailable"),
            origin=origin,
            generated_at=generated_at,
        )
        for card in cards
    ]
