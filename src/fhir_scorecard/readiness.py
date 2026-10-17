"""The CMS-0057-F readiness report: what the two public files declare, before January 2027.

Payers and the vendors who build their APIs are preparing for the additional APIs CMS-0057-F
introduces. A reader preparing for that wants to know what a payer's public discovery files
already say about the machinery those APIs run on, and wants it without a verdict attached.
This page reports exactly that, from the observations the build already holds, and nothing more.

**It states only what the two public files show.** ``/metadata`` and
``/.well-known/smart-configuration`` are all this project requests. Every cell on the page is a
field of one of those documents, read by :mod:`fhir_scorecard.backend` (the same five-answer
vocabulary the endpoint pages use), or a count of those cells. Nothing is requested, exercised or
inferred, and an API that sits behind authentication cannot appear in either file.

**It grades nothing and decides nothing.** The APIs CMS-0057-F adds are not in force yet, so they
are not graded, and no letter, score, weight or finding on any other page is read from or changed
by this one. It makes no compliance determination: ``ROADMAP.md`` lists "compliance
determinations" under what this project does not do, and the page says so in its first screen.

**Regulatory statements are a closed list, and each one is checked against this repository.**
:data:`CLAIMS` holds every sentence the page says about the regulation, with the file and the
words in this repository that support it. ``tests/test_readiness.py`` fails if a quoted passage
stops being present in its file, and fails if the page prints a regulatory sentence that is not
in the list. None of them is checked against the rule text itself, because this repository does
not cite or retain the rule text (its only retained legal source is not CMS-0057-F), and the
page says that in the status column rather than letting it read as verified.

**Absence is not a negative.** A document no vantage retrieved, or one that could not be read,
is counted in its own column and never as "not declared". A SMART field the document does not
contain is reported as absent, not as a refusal: ``token_endpoint_auth_methods_supported`` is
OPTIONAL in the retained SMART App Launch page.

**No date is invented.** The page carries the date of the run it was built from, the word
"2027" only inside a quoted claim, and no other date.
"""

from __future__ import annotations

import html
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fhir_scorecard.backend import (
    ANSWERS,
    DECLARED,
    FIELD_ABSENT,
    NOT_LISTED,
    NOT_RETRIEVED,
    QUESTIONS,
    UNREADABLE,
    Answer,
    answers,
)
from fhir_scorecard.capability import CapabilityFacts, SmartFacts
from fhir_scorecard.grading import Scorecard
from fhir_scorecard.site import KIND_LABELS, Page

#: Site path of the report.
READINESS_PATH = "cms-0057-f"

#: The registry kinds this report covers. Payer-side surfaces only: an EHR vendor's sandbox or a
#: reference server is not a payer, and a count over them would say nothing to this reader.
PAYER_KINDS: tuple[str, ...] = ("payer", "payer_provider_directory")

#: Where the repository's own documents can be read, for the sources column.
REPO_BLOB = "https://github.com/ChelseaKR/fhir-scorecard/blob/main"

#: Every state a question can be in, in the order the summary table prints them. Equal to
#: :data:`fhir_scorecard.backend.ANSWERS`; a test holds the two together.
STATES: tuple[str, ...] = (DECLARED, NOT_LISTED, FIELD_ABSENT, UNREADABLE, NOT_RETRIEVED)

#: Short column labels for each question, keyed by :attr:`backend.Question.key`.
SHORT_LABELS: dict[str, str] = {
    "private_key_jwt": "private_key_jwt",
    "client_credentials": "client_credentials grant",
    "client_confidential_asymmetric": "client-confidential-asymmetric",
    "system_scopes": "system/ scopes",
    "export_operation": "export operation",
    "bulk_data_guide": "Bulk Data guide",
}

PROJECT_RULE = "This project's own rule, stated in its documents."
UNVERIFIED = (
    "Stated in this project's documents. The rule text is not cited or retained in this "
    "repository, so it has not been checked against the Federal Register or the eCFR."
)


@dataclass(frozen=True)
class Source:
    """A file in this repository and words that must appear in it."""

    path: str
    quote: str


@dataclass(frozen=True)
class Claim:
    """One sentence this page says about the regulation or about its own scope."""

    claim_id: str
    text: str
    status: str
    sources: tuple[Source, ...]


#: The whole of what the page says about the regulation. See the module docstring.
CLAIMS: tuple[Claim, ...] = (
    Claim(
        "apis",
        "CMS-0057-F adds Provider Access, Payer-to-Payer, and Prior Authorization APIs.",
        UNVERIFIED,
        (
            Source(
                "data/cohorts/california.json",
                "CMS-0057-F adds Provider Access, Payer-to-Payer, and Prior Authorization APIs",
            ),
            Source(
                "docs/SAMPLING-FRAME.md",
                "CMS-0057-F's Provider Access, Payer-to-Payer, and Prior Authorization APIs",
            ),
        ),
    ),
    Claim(
        "date",
        "Their compliance dates are in January 2027.",
        UNVERIFIED,
        (
            Source("data/cohorts/california.json", "with compliance dates in January 2027"),
            Source("docs/SAMPLING-FRAME.md", "are not in force until January 2027"),
        ),
    ),
    Claim(
        "ungraded",
        "Because they are not in force yet, this project does not grade them.",
        PROJECT_RULE,
        (
            Source("data/cohorts/california.json", "none of it is graded here"),
            Source("docs/SAMPLING-FRAME.md", "are not graded"),
        ),
    ),
    Claim(
        "no-determination",
        "This project makes no compliance determinations.",
        PROJECT_RULE,
        (Source("ROADMAP.md", "Compliance determinations"),),
    ),
    Claim(
        "listing",
        "Being listed in this project's frame or registry is not a compliance obligation.",
        PROJECT_RULE,
        (Source("docs/SAMPLING-FRAME.md", "Membership in a frame is not a compliance obligation"),),
    ),
)

#: What the page deliberately does not assert, because nothing in this repository establishes it.
NOT_ASSERTED: tuple[str, ...] = (
    "Which organizations the rule applies to.",
    "What each of the new APIs must do, or which implementation guides satisfy it.",
    "The compliance date of any individual API.",
    "Whether a field listed below is required by the rule. The fields are the ones this project "
    "already reads for SMART Backend Services and Bulk Data, because those are what its "
    "existing app-to-server block reports; none of them is mapped here to a requirement.",
    "Whether any API exists behind authentication. Neither public file can show that.",
)


def _doc_state(observed: bool, readable: bool) -> str:
    if not observed:
        return NOT_RETRIEVED
    return DECLARED if readable else UNREADABLE


def capability_state(facts: CapabilityFacts) -> str:
    """``declared`` stands for "read" here: a CapabilityStatement arrived and could be parsed."""
    return _doc_state(facts.observed, facts.parsed and facts.resource_type_ok)


def smart_state(smart: SmartFacts) -> str:
    """The same three-way split for the SMART document, where ``not_served`` is not retrieved."""
    if not smart.observed or smart.not_served:
        return NOT_RETRIEVED
    # ``{}`` is a document that was read and declares nothing; ``backend`` answers its fields as
    # absent, so counting it as unreadable here would make the two tables disagree.
    return _doc_state(True, smart.parsed or smart.empty_object)


@dataclass(frozen=True)
class Row:
    """One payer-side endpoint, as the two files describe it."""

    endpoint_id: str
    name: str
    kind: str
    capability: str
    smart: str
    fhir_version: str | None
    software: str | None
    answers: tuple[Answer, ...]

    def answer(self, key: str) -> Answer:
        return next(a for a in self.answers if a.question.key == key)


def rows(
    cards: Sequence[Scorecard],
    declared: Mapping[str, CapabilityFacts],
    smart_declared: Mapping[str, SmartFacts],
) -> list[Row]:
    """The payer-side rows, in a total order (kind, then name, then id) so a rebuild cannot
    reshuffle them. Raises, as the endpoint pages do, if the facts a card was built from were
    not kept: a row built without them would publish "not retrieved" for a document nobody
    checked.
    """
    built: list[Row] = []
    for card in cards:
        if card.kind not in PAYER_KINDS:
            continue
        facts, smart = declared.get(card.endpoint_id), smart_declared.get(card.endpoint_id)
        if facts is None or smart is None:
            raise ValueError(
                f"no SMART or CapabilityStatement facts were kept for {card.endpoint_id!r}"
            )
        is_read = capability_state(facts) == DECLARED
        built.append(
            Row(
                endpoint_id=card.endpoint_id,
                name=card.name,
                kind=card.kind,
                capability=capability_state(facts),
                smart=smart_state(smart),
                fhir_version=(facts.fhir_version or None) if is_read else None,
                software=(facts.software_name or None) if is_read else None,
                answers=answers(smart, facts),
            )
        )
    return sorted(built, key=lambda r: (PAYER_KINDS.index(r.kind), r.name.lower(), r.endpoint_id))


def tally(built: Sequence[Row]) -> dict[str, Counter[str]]:
    """For each question key, how many rows are in each state. The states always sum to
    ``len(built)``: every row has exactly one answer to every question."""
    out: dict[str, Counter[str]] = {q.key: Counter() for q in QUESTIONS}
    for row in built:
        for answer in row.answers:
            out[answer.question.key][answer.answer] += 1
    return out


def document_counts(built: Sequence[Row]) -> dict[str, Counter[str]]:
    return {
        "capability": Counter(r.capability for r in built),
        "smart": Counter(r.smart for r in built),
    }


_READ_STATES = (DECLARED, NOT_LISTED, FIELD_ABSENT)


def by_software(built: Sequence[Row]) -> list[tuple[str, list[Row]]]:
    """Rows grouped by the software name the CapabilityStatement declares, largest group first.

    The name is what the document says and not who operates the server: a multi-tenant platform
    reports the platform, and some documents name nobody. Rows whose CapabilityStatement was not
    read have no declared name and are grouped apart, never under a guess.
    """
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in built:
        if row.capability != DECLARED:
            groups["\0 CapabilityStatement not read"].append(row)
        else:
            groups[row.software or "\0 No software named"].append(row)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0].lower()))
    return [(name.removeprefix("\0 "), members) for name, members in ordered]


def _e(value: str) -> str:
    return html.escape(value)


def _state_cell(row: Row, key: str) -> str:
    return _e(ANSWERS[row.answer(key).answer])


def _summary_table(built: Sequence[Row]) -> str:
    counted = tally(built)
    head = "".join(f'<th scope="col">{_e(ANSWERS[s])}</th>' for s in STATES)
    body = "".join(
        f'<tr><th scope="row">{_e(q.asks)}</th>'
        + "".join(f"<td>{counted[q.key][s]}</td>" for s in STATES)
        + "</tr>"
        for q in QUESTIONS
    )
    return (
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        'aria-label="Declared fields, counted by answer">'
        '<table class="usa-table usa-table--striped"><caption>How many of the '
        f"{len(built)} payer-side endpoints give each answer. Each row sums to {len(built)}; "
        "the columns are never merged.</caption>"
        f'<thead><tr><th scope="col">Question</th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _endpoint_table(kind: str, members: Sequence[Row]) -> str:
    label = KIND_LABELS.get(kind, kind)
    head = "".join(f'<th scope="col">{_e(SHORT_LABELS[q.key])}</th>' for q in QUESTIONS)
    lines = []
    for row in members:
        version = (
            _e(row.fhir_version)
            if row.fhir_version
            else _e(
                "not stated"
                if row.capability == DECLARED
                else ANSWERS[NOT_RETRIEVED]
                if row.capability == NOT_RETRIEVED
                else "could not be read"
            )
        )
        cells = "".join(f"<td>{_state_cell(row, q.key)}</td>" for q in QUESTIONS)
        lines.append(
            f'<tr><th scope="row"><a href="/endpoint/{_e(row.endpoint_id)}/">{_e(row.name)}</a>'
            f"</th><td>{version}</td>{cells}</tr>"
        )
    return (
        f"<h3>{_e(label)}</h3>"
        f'<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        f'aria-label="{_e(label)}">'
        f'<table class="usa-table usa-table--striped"><caption>{_e(label)}: what each '
        "endpoint's two public files declare</caption>"
        '<thead><tr><th scope="col">Endpoint</th><th scope="col">FHIR version declared</th>'
        f"{head}</tr></thead><tbody>{''.join(lines)}</tbody></table></div>"
    )


def _software_table(built: Sequence[Row]) -> str:
    head = "".join(f'<th scope="col">{_e(SHORT_LABELS[q.key])}</th>' for q in QUESTIONS)
    lines = []
    for name, members in by_software(built):
        cells = []
        for q in QUESTIONS:
            states = Counter(m.answer(q.key).answer for m in members)
            read = sum(states[s] for s in _READ_STATES)
            cells.append(f"<td>{states[DECLARED]} of {read}</td>")
        lines.append(
            f'<tr><th scope="row">{_e(name)}</th><td>{len(members)}</td>{"".join(cells)}</tr>'
        )
    return (
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        'aria-label="Declared fields by software named">'
        '<table class="usa-table usa-table--striped"><caption>For each software name a '
        "CapabilityStatement declares: how many endpoints name it, and for each question how "
        "many declared it out of how many documents could be read for it</caption>"
        '<thead><tr><th scope="col">Software named</th><th scope="col">Endpoints</th>'
        f"{head}</tr></thead><tbody>{''.join(lines)}</tbody></table></div>"
    )


def _claims_table() -> str:
    lines = []
    for claim in CLAIMS:
        sources = "; ".join(
            f'<a href="{REPO_BLOB}/{_e(s.path)}" rel="nofollow"><code>{_e(s.path)}</code></a>'
            for s in claim.sources
        )
        lines.append(
            f"<tr><td>{_e(claim.text)}</td><td>{_e(claim.status)}</td><td>{sources}</td></tr>"
        )
    return (
        '<div class="usa-table-container--scrollable" tabindex="0" role="region" '
        'aria-label="Statements about the regulation and where they come from">'
        '<table class="usa-table usa-table--striped"><caption>Every statement this page makes '
        "about the regulation or about its own scope</caption>"
        '<thead><tr><th scope="col">Statement</th><th scope="col">Status</th>'
        f'<th scope="col">Where this repository says it</th></tr></thead><tbody>{"".join(lines)}'
        "</tbody></table></div>"
    )


def _documents_sentence(built: Sequence[Row]) -> str:
    counts = document_counts(built)

    def phrase(name: str, counter: Counter[str]) -> str:
        return (
            f"{name}: {counter[DECLARED]} read, {counter[UNREADABLE]} retrieved and not "
            f"readable, {counter[NOT_RETRIEVED]} not retrieved."
        )

    return (
        f"{phrase('CapabilityStatement', counts['capability'])} "
        f"{phrase('SMART discovery document', counts['smart'])}"
    )


def page(
    cards: Sequence[Scorecard],
    declared: Mapping[str, CapabilityFacts],
    smart_declared: Mapping[str, SmartFacts],
    *,
    as_of: str,
    origin: str,
) -> Page:
    """The report as a page. A pure function of its arguments: ``as_of`` is the date of the run
    the cards came from, passed in, and nothing here reads a clock."""
    built = rows(cards, declared, smart_declared)
    by_kind: dict[str, list[Row]] = defaultdict(list)
    for row in built:
        by_kind[row.kind].append(row)
    if built:
        data = (
            f"<h2>What the files declare, counted</h2><p>{_e(_documents_sentence(built))} A "
            "document that was not retrieved, or arrived and could not be read, is counted in "
            "its own column and is never read as a field the publisher left out.</p>"
            f"{_summary_table(built)}"
            "<h2>Each endpoint</h2>"
            + "".join(_endpoint_table(kind, by_kind[kind]) for kind in PAYER_KINDS if by_kind[kind])
            + "<h2>By the software the CapabilityStatement names</h2>"
            "<p>This is what each document says, not who operates the server. A multi-tenant "
            "platform reports the platform, some documents name nobody, and endpoints whose "
            "CapabilityStatement was not read are grouped apart.</p>"
            f"{_software_table(built)}"
        )
    else:
        data = (
            "<h2>Nothing to count</h2><p>This build lists no payer-side endpoint, so there is "
            "nothing to report.</p>"
        )
    not_asserted = "".join(f"<li>{_e(item)}</li>" for item in NOT_ASSERTED)
    body = f"""
<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">
<li class="usa-breadcrumb__list-item">
<a href="/" class="usa-breadcrumb__link"><span>Home</span></a></li>
<li class="usa-breadcrumb__list-item usa-current" aria-current="page">
<span>CMS-0057-F readiness report</span></li>
</ol></nav>
<p class="eyebrow">Readiness report</p>
<h1>CMS-0057-F readiness report</h1>
<p class="lede">What {len(built)} payer-side FHIR endpoints say in their two public discovery
files, for payers and API vendors preparing for CMS-0057-F. Data from the run of {_e(as_of)}.</p>
<div class="usa-alert usa-alert--warning"><div class="usa-alert__body">
<h2 class="usa-alert__heading">Scope limits</h2>
<ul>
<li>This is not a compliance determination, and it is not a grade. Nothing here changes any
grade, score or weight elsewhere on this site.</li>
<li>The additional APIs are not graded here. The statements further down say why, and say how
well that is sourced.</li>
<li>Only two public files were read for each endpoint, <code>/metadata</code> and
<code>/.well-known/smart-configuration</code>. Nothing was requested beyond them and no API was
exercised.</li>
<li>A field a file does not contain is reported as absent. It is not a refusal and not a
failure.</li>
<li>Listing here says nothing about whether an organization is subject to the rule.</li>
</ul>
</div></div>
{data}
<h2>What this page says about the regulation</h2>
<p>These statements are the only ones on this page about the rule. Each is taken from this
project's own documents. Where the status says the rule text has not been checked, that is
literal: this repository does not cite or retain the Federal Register notice or the eCFR text,
so the statement is the project's, not a quotation of the rule.</p>
{_claims_table()}
<h2>What this page does not assert</h2>
<ul>{not_asserted}</ul>
<h2>Where the data comes from</h2>
<p>The same observations feed each endpoint's page and its
<code>api/endpoint/&lt;id&gt;.json</code> file, in the block named
<code>app_to_server</code>. The questions and their citations are the ones on
<a href="/how-we-grade/">how we grade</a> and on each endpoint page. To check a row, open the
endpoint, or fetch its two public files yourself.</p>
"""
    return Page(
        path=READINESS_PATH,
        title="CMS-0057-F readiness report for payer FHIR endpoints",
        description=(
            f"What {len(built)} payer-side FHIR endpoints declare in their two public discovery "
            "files, with scope limits and the sources for every statement about the rule. Not a "
            "compliance determination."
        ),
        body=body,
        priority="0.6",
    )
