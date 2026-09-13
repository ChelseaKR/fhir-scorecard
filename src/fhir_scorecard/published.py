"""The published-grade contract: what must be true of a grade on every surface that carries it.

`fhir_scorecard.audit` is the *site* contract and is written about structure - sitemaps,
canonicals, structured data, links, orphans. It examines every page a build wrote and says
nothing about any grade, which is full coverage of a property that is not the one that failed.
`fhir_scorecard.accessibility` and `fhir_scorecard.weight` are the same shape for their own
properties. This module is the fourth of those families and the one about the number a reader
came for, and `cli._cmd_audit_site` runs all four together so a publish cannot skip one.

**Why it is here and not bolted onto the site contract.** A grade's *correctness* is the
grader's business and is held by the tests that pin published cards. What no gate held was the
much weaker property that the grade a build publishes is *well formed* and is *the same grade on
every surface* - the CSV a reader downloads, the per-endpoint JSON, the API index, the
scorecards document, and the page a human actually looks at. That is a property of a built
directory, like every rule in the site contract, but it is a claim about data rather than about
markup, and putting data rules inside a structural checker is how a checker stops being
readable. So: a peer module, its own finding codes, one command running both.

Three published defects from 2026-09-12 are what the rules are drawn from, and each one reached
production because nothing examined the thing that was wrong:

* **A score no run measured, published as a zero.** Fourteen endpoints carried
  ``reachability_score: 0`` beside a named health insurer while no vantage had reached them.
  :data:`GRADE_PUBLISHED_WITHOUT_A_REACH` is that shape, read off the row.
* **A letter that went missing.** Eighteen endpoints published no letter at all while all three
  vantages held their CapabilityStatements. Its neighbouring impossible state -
  ``not observed`` over three scored dimensions, which :func:`fhir_scorecard.grading.letter`
  cannot produce - is :data:`GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT`.
* **Surfaces that could disagree and nothing that would notice.** The CSV's grade column was
  checked by nothing, anywhere, while its identity columns were checked on the live site every
  night. :data:`GRADE_SURFACES_DISAGREE` and :data:`GRADE_PAGE_DISAGREES_WITH_THE_DATA` close
  that, and the second one reads the rendered HTML rather than a JSON file that happens to feed
  it, because "visible on the card" is a claim about the card.

Two things this module deliberately does **not** do.

It does not decide whether a grade is *right*. Nothing here recomputes a score, and it could
not: the inputs are third-party documents retrieved once, on a day, from three networks. What
it decides is whether what was published is a shape the grader can produce and whether every
surface agrees. A build can satisfy every rule below and publish a wrong letter on all five
surfaces at once.

It does not hold its vocabulary by importing the generator's. :data:`LETTERS` and
:data:`NOT_OBSERVED_LITERAL` are spelled out here for the reason ``audit._ATOM_NAMESPACE`` is
spelled out there - a checker that agrees with the generator by construction cannot catch the
generator moving. ``tests/test_published_grades.py`` asserts the two agree, which is a test that
can fail rather than an import that cannot.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from fhir_scorecard.audit import SiteFinding, page_file

#: Every letter :func:`fhir_scorecard.grading.letter` can band to. Stated, not imported; see the
#: module docstring.
LETTERS: tuple[str, ...] = ("A", "B", "C", "D", "F")

#: What this project publishes in place of a letter when a run pinned none. Stated, not
#: imported, for the same reason.
NOT_OBSERVED_LITERAL = "not observed"

#: The dimension-score columns of ``dataset.csv``, in the order the dimensions are published.
SCORE_COLUMNS: tuple[str, ...] = ("reachability_score", "transparency_score", "interop_score")

#: Columns a row must carry before anything about a grade can be read from it. A CSV missing one
#: of these is reported as a check that could not run, never as a check that found nothing.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "endpoint_id",
    "grade",
    "reachable",
    *SCORE_COLUMNS,
    "failure_kinds",
    "vantages_reached",
    "vantages_reporting",
)

#: Every finding code this module can emit, with the one-line statement of what it means.
#: :func:`audit_published_grades` may return no code outside this map, which
#: ``tests/test_published_grades.py`` asserts, so a new rule cannot ship without a documented
#: name.
GRADE_CODES: dict[str, str] = {
    "GRADE_NOT_IN_THE_PUBLISHED_VOCABULARY": (
        "a published grade that is neither a letter this grader bands to nor the literal "
        "this project publishes when it pinned none"
    ),
    "GRADE_SCORE_IS_NOT_A_PERCENTAGE": (
        "a dimension score cell that is neither empty nor a whole number from 0 to 100"
    ),
    "GRADE_PUBLISHED_WITHOUT_A_REACH": (
        "a letter, or a dimension score, beside an endpoint no vantage reached. This is the "
        "2026-09-12 defect: a reachability score of 0 that no run measured, published against "
        "a named organization"
    ),
    "GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT": (
        "an endpoint published without a letter whose three dimensions all carry a score. "
        "grading.letter pins a band whenever every dimension is scored, so this row cannot "
        "have come from the grader"
    ),
    "GRADE_REACH_IS_NOT_COHERENT": (
        "a row whose reachability, failure condition and vantage counts cannot all be true at once"
    ),
    "GRADE_SURFACES_DISAGREE": (
        "one endpoint's grade or dimension scores differ between dataset.csv, "
        "api/endpoint/<id>.json, api/index.json and scorecards.json, or an endpoint one of "
        "them publishes is absent from another"
    ),
    "GRADE_PAGE_DISAGREES_WITH_THE_DATA": (
        "a rendered endpoint page shows a grade or a dimension score that is not what the "
        "published data for that endpoint says"
    ),
    "GRADE_NOTHING_EXAMINED": (
        "the published data needed to check any of the above was absent, empty or unreadable, "
        "so these rules examined nothing. A check that compares nothing must fail, not pass"
    ),
}

#: Where a dataset-level finding is located, as a site-relative path.
DATASET_CSV = "dataset.csv"
API_INDEX = "api/index.json"
SCORECARDS = "scorecards.json"


#: The dimension keys whose scores :data:`SCORE_COLUMNS` carry, in the same published order.
DIMENSION_KEYS: tuple[str, ...] = ("reachability", "transparency", "interop")


@dataclass(frozen=True)
class PublishedGrade:
    """One endpoint's grade as one surface published it, in one comparable representation.

    Scores are held as the strings the CSV uses - ``""`` for a dimension with no score, decimal
    digits otherwise - so surfaces that encode them differently compare without either side
    being re-derived at comparison time.

    ``scores`` is ``None``, and never ``()``, for a surface that does not publish scores at all:
    ``api/index.json`` carries a letter and nothing else. The two must stay distinguishable, or
    "this surface has no scores to compare" and "this surface published an empty score list"
    would be the same value, which is the absence-rendered-as-a-value shape this whole module is
    about.
    """

    grade: str
    scores: tuple[str, ...] | None = None


def _as_cell(score: object) -> str:
    """A JSON dimension score in the CSV's representation. ``None`` is an empty cell, never 0."""
    return "" if score is None else str(score)


def _percentage(cell: str) -> int | None:
    """``cell`` as a whole number from 0 to 100, or ``None`` when it is not one."""
    try:
        value = int(cell)
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def _count(cell: str) -> int | None:
    """``cell`` as a count, or ``None`` when it is not a whole number that could be one."""
    try:
        value = int(cell)
    except ValueError:
        return None
    return value if value >= 0 else None


def _grade_findings(row: Mapping[str, str], where: str, eid: str) -> list[SiteFinding]:
    grade = row["grade"]
    if grade in LETTERS or grade == NOT_OBSERVED_LITERAL:
        return []
    return [
        SiteFinding(
            "GRADE_NOT_IN_THE_PUBLISHED_VOCABULARY",
            where,
            f"{eid} publishes grade {grade!r}; the published vocabulary is "
            f"{', '.join([*LETTERS, NOT_OBSERVED_LITERAL])}",
        )
    ]


def _score_findings(row: Mapping[str, str], where: str, eid: str) -> list[SiteFinding]:
    return [
        SiteFinding("GRADE_SCORE_IS_NOT_A_PERCENTAGE", where, f"{eid}.{column} is {row[column]!r}")
        for column in SCORE_COLUMNS
        if row[column] != "" and _percentage(row[column]) is None
    ]


def _vantage_findings(
    row: Mapping[str, str], where: str, eid: str, *, reached: bool
) -> list[SiteFinding]:
    """The two published vantage counts, which are read together or not at all."""
    got = _count(row["vantages_reached"])
    reporting = _count(row["vantages_reporting"])
    if got is None or reporting is None:
        return [
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid} publishes vantages_reached={row['vantages_reached']!r} and "
                f"vantages_reporting={row['vantages_reporting']!r}; both are whole counts",
            )
        ]
    findings = []
    if got > reporting:
        findings.append(
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid} reports {got} vantage(s) reached out of {reporting} reporting",
            )
        )
    if not reached and got:
        findings.append(
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid} is published as not reached while {got} vantage(s) reached it",
            )
        )
    return findings


def _reach_findings(row: Mapping[str, str], where: str, eid: str) -> list[SiteFinding]:
    """Whether the row's reachability, condition and vantage counts can all be true at once."""
    reachable = row["reachable"]
    if reachable not in {"true", "false"}:
        return [
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid}.reachable is {reachable!r}, which is neither 'true' nor 'false'",
            )
        ]
    reached = reachable == "true"
    kinds = row["failure_kinds"].split()
    findings = []
    if reached and kinds:
        findings.append(
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid} was reached and is filed under condition(s) {' '.join(kinds)}; a "
                f"reached endpoint is in no failure population",
            )
        )
    if not reached and not kinds and _count(row["vantages_reporting"]) not in {0, None}:
        findings.append(
            SiteFinding(
                "GRADE_REACH_IS_NOT_COHERENT",
                where,
                f"{eid} was reached by none of its {row['vantages_reporting']} reporting "
                f"vantage(s) and names no condition; only an endpoint nobody asked about has "
                f"no condition to name",
            )
        )
    return findings + _vantage_findings(row, where, eid, reached=reached)


def _measurement_findings(row: Mapping[str, str], where: str, eid: str) -> list[SiteFinding]:
    """The two pairings of a grade with a measurement that the grader cannot produce.

    Worth a finding rather than a warning for that reason: a row in either state did not come
    from :func:`fhir_scorecard.grading.letter`, so something downstream of it wrote the number.
    """
    findings = []
    scored = [column for column in SCORE_COLUMNS if row[column] != ""]
    if row["reachable"] == "false":
        if row["grade"] != NOT_OBSERVED_LITERAL:
            findings.append(
                SiteFinding(
                    "GRADE_PUBLISHED_WITHOUT_A_REACH",
                    where,
                    f"{eid} publishes grade {row['grade']!r} and no vantage reached it",
                )
            )
        findings += [
            SiteFinding(
                "GRADE_PUBLISHED_WITHOUT_A_REACH",
                where,
                f"{eid}.{column} is {row[column]!r} and no vantage reached it; an unmeasured "
                f"dimension publishes an empty cell, never a number",
            )
            for column in scored
        ]
    elif row["grade"] == NOT_OBSERVED_LITERAL and len(scored) == len(SCORE_COLUMNS):
        findings.append(
            SiteFinding(
                "GRADE_WITHHELD_OVER_A_COMPLETE_MEASUREMENT",
                where,
                f"{eid} publishes no letter while all three dimensions carry a score "
                f"({', '.join(f'{c}={row[c]}' for c in SCORE_COLUMNS)})",
            )
        )
    return findings


def audit_rows(rows: Sequence[Mapping[str, str]], *, where: str = DATASET_CSV) -> list[SiteFinding]:
    """Every way the rows of a published dataset break the grade contract, in a stable order.

    Pure: it reads the rows it is handed and no file. ``tools/verify_live_site.py`` runs it over
    the CSV the deployment serves and :func:`audit_published_grades` over the CSV a build has
    just written, so a row a reader downloads today and a row CI produced are held to one set of
    rules rather than to two that can drift apart.
    """
    if not rows:
        return [
            SiteFinding("GRADE_NOTHING_EXAMINED", where, "carries no rows, so no grade was read")
        ]
    absent = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if absent:
        return [
            SiteFinding(
                "GRADE_NOTHING_EXAMINED",
                where,
                f"carries no {', '.join(absent)} column, so nothing about a grade could be read",
            )
        ]
    findings: list[SiteFinding] = []
    for row in rows:
        eid = row["endpoint_id"] or "(a row with no endpoint_id)"
        findings += _grade_findings(row, where, eid)
        findings += _score_findings(row, where, eid)
        findings += _reach_findings(row, where, eid)
        findings += _measurement_findings(row, where, eid)
    return findings


def rows_as_published(rows: Iterable[Mapping[str, str]]) -> dict[str, PublishedGrade]:
    """The grade each CSV row publishes, keyed by endpoint id."""
    return {
        row["endpoint_id"]: PublishedGrade(
            row["grade"], tuple(row[column] for column in SCORE_COLUMNS)
        )
        for row in rows
        if row.get("endpoint_id")
    }


def _across_two(
    reference: str,
    name: str,
    surfaces: Mapping[str, Mapping[str, PublishedGrade]],
    eid: str,
) -> list[SiteFinding]:
    want = surfaces[reference].get(eid)
    got = surfaces[name].get(eid)
    if want is None:
        return [
            SiteFinding(
                "GRADE_SURFACES_DISAGREE", name, f"publishes {eid}, which {reference} does not"
            )
        ]
    if got is None:
        return [
            SiteFinding(
                "GRADE_SURFACES_DISAGREE", name, f"does not publish {eid}, which {reference} does"
            )
        ]
    findings = []
    if want.grade != got.grade:
        findings.append(
            SiteFinding(
                "GRADE_SURFACES_DISAGREE",
                name,
                f"{eid} is grade {got.grade!r} here and {want.grade!r} in {reference}",
            )
        )
    # Only where both surfaces publish scores. `None` means this surface carries none, which is
    # a different fact from carrying an empty list and must not read as a disagreement.
    if want.scores is not None and got.scores is not None and want.scores != got.scores:
        findings.append(
            SiteFinding(
                "GRADE_SURFACES_DISAGREE",
                name,
                f"{eid} scores {got.scores} here and {want.scores} in {reference}",
            )
        )
    return findings


def surface_differences(surfaces: Mapping[str, Mapping[str, PublishedGrade]]) -> list[SiteFinding]:
    """Every endpoint two named surfaces publish differently, or that only one of them publishes.

    ``surfaces`` maps a surface's site-relative path to what it published, keyed by endpoint id.
    The first entry is the reference every other is compared against, and the caller passes
    ``dataset.csv`` first because that is the file a reader downloads.
    """
    names = list(surfaces)
    if len(names) < 2:
        return []
    reference, *others = names
    findings: list[SiteFinding] = []
    for name in others:
        for eid in sorted(set(surfaces[reference]) | set(surfaces[name])):
            findings += _across_two(reference, name, surfaces, eid)
    return findings


#: Elements :class:`_EndpointPageReader` keeps a stack of. All four are non-void, so a stack
#: built from them cannot be corrupted by a ``<meta>`` or an ``<img>`` that never closes.
_TRACKED_TAGS = frozenset({"section", "div", "span", "strong"})


class _EndpointPageReader(HTMLParser):
    """Read the grade and the dimension scores a rendered endpoint page shows a human.

    A reader of the markup, not of the generator. Every other row assertion in this repository
    reads a JSON file; this one reads the HTML, because a build that says the right thing in
    ``api/endpoint/<id>.json`` and the wrong thing on the page is exactly the failure the phrase
    "visible on the card" exists to rule out, and a JSON-only assertion cannot see it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        #: Text of the hero badge, or ``None`` when the page renders no hero grade.
        self.badge: str | None = None
        #: ``(title, value, unscored)`` per dimension meter, in document order.
        self.meters: list[tuple[str, str, bool]] = []
        self._open: list[tuple[str, frozenset[str], str]] = []
        self._sink: list[str] | None = None
        self._badge: list[str] = []
        self._title: list[str] = []
        self._value: list[str] = []
        self._unscored = False
        self._in_meter = False
        self._seen_title = False

    def _inside(self, name: str) -> bool:
        return any(name in classes for _tag, classes, _role in self._open)

    def _role_for(self, tag: str, classes: frozenset[str]) -> str:
        if (
            tag == "div"
            and "dimension-meter" in classes
            and not self._in_meter
            and self._inside("score-overview")
        ):
            return "meter"
        if tag == "span" and "grade" in classes and self._inside("hero-grade"):
            return "badge"
        if tag == "span" and not classes and self._in_meter and not self._seen_title:
            return "title"
        if tag == "strong" and self._in_meter:
            return "value"
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _TRACKED_TAGS:
            return
        classes = frozenset((dict(attrs).get("class") or "").split())
        role = self._role_for(tag, classes)
        if role == "meter":
            self._in_meter = True
            self._unscored = "dimension-meter-unscored" in classes
            self._title, self._value, self._seen_title = [], [], False
        elif role == "badge":
            self._badge = []
            self._sink = self._badge
        elif role == "title":
            self._seen_title = True
            self._sink = self._title
        elif role == "value":
            self._sink = self._value
        self._open.append((tag, classes, role))

    def handle_endtag(self, tag: str) -> None:
        if tag not in _TRACKED_TAGS:
            return
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                role = self._open.pop(index)[2]
                self._close(role)
                return

    def _close(self, role: str) -> None:
        if role in {"badge", "title", "value"}:
            self._sink = None
        if role == "badge":
            self.badge = "".join(self._badge).strip()
        elif role == "meter":
            self.meters.append(
                ("".join(self._title).strip(), "".join(self._value).strip(), self._unscored)
            )
            self._in_meter = False

    def handle_data(self, data: str) -> None:
        if self._sink is not None:
            self._sink.append(data)


def read_endpoint_page(html: str) -> tuple[str | None, list[tuple[str, str, bool]]]:
    """The hero grade and the dimension meters one rendered endpoint page shows."""
    reader = _EndpointPageReader()
    reader.feed(html)
    reader.close()
    return reader.badge, reader.meters


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cards(root: Path) -> dict[str, PublishedGrade]:
    """What each ``api/endpoint/<id>.json`` published, keyed by endpoint id."""
    published: dict[str, PublishedGrade] = {}
    for path in sorted((root / "api" / "endpoint").glob("*.json")):
        payload = _read_json(path)
        record = payload.get("endpoint") if isinstance(payload, dict) else None
        if not isinstance(record, dict) or not isinstance(record.get("endpoint_id"), str):
            continue
        published[record["endpoint_id"]] = PublishedGrade(
            str(record.get("grade")),
            tuple(_as_cell(record.get(column)) for column in SCORE_COLUMNS),
        )
    return published


def _index(root: Path) -> dict[str, PublishedGrade]:
    """What ``api/index.json`` published. It carries a letter and no dimension scores."""
    payload = _read_json(root / API_INDEX)
    listed = payload.get("endpoints") if isinstance(payload, dict) else None
    if not isinstance(listed, list):
        return {}
    return {
        entry["endpoint_id"]: PublishedGrade(str(entry.get("grade")))
        for entry in listed
        if isinstance(entry, dict) and isinstance(entry.get("endpoint_id"), str)
    }


def _scorecards(root: Path) -> dict[str, PublishedGrade]:
    """What ``scorecards.json`` published, with the scores read by key rather than by position.

    By key on purpose: reading them positionally would make a reordered dimension list compare
    equal to a correct one for two of the three columns, which is a disagreement this rule
    exists to see.
    """
    payload = _read_json(root / SCORECARDS)
    listed = payload.get("scorecards") if isinstance(payload, dict) else None
    if not isinstance(listed, list):
        return {}
    published: dict[str, PublishedGrade] = {}
    for record in listed:
        if not isinstance(record, dict) or not isinstance(record.get("endpoint_id"), str):
            continue
        dimensions = record.get("dimensions")
        by_key = (
            {d["key"]: d.get("score") for d in dimensions if isinstance(d, dict) and "key" in d}
            if isinstance(dimensions, list)
            else {}
        )
        published[record["endpoint_id"]] = PublishedGrade(
            str(record.get("grade")),
            tuple(
                _as_cell(by_key[key]) if key in by_key else "(no such dimension)"
                for key in DIMENSION_KEYS
            ),
        )
    return published


def _page_findings(root: Path, eid: str, card: PublishedGrade) -> list[SiteFinding]:
    """Does the page a reader opens show what the published data for that endpoint says?"""
    where = page_file(f"endpoint/{eid}")
    try:
        html = (root / "endpoint" / eid / "index.html").read_text(encoding="utf-8")
    except OSError:
        return [
            SiteFinding(
                "GRADE_PAGE_DISAGREES_WITH_THE_DATA",
                where,
                f"{eid} is published as grade {card.grade!r} and has no page",
            )
        ]
    badge, meters = read_endpoint_page(html)
    findings = []
    if badge != card.grade:
        findings.append(
            SiteFinding(
                "GRADE_PAGE_DISAGREES_WITH_THE_DATA",
                where,
                f"the page shows {badge!r} and the published data says {card.grade!r}",
            )
        )
    cells = card.scores or ()
    if len(meters) != len(cells):
        return [
            *findings,
            SiteFinding(
                "GRADE_PAGE_DISAGREES_WITH_THE_DATA",
                where,
                f"the page renders {len(meters)} dimension meter(s) and the published data "
                f"carries {len(cells)}",
            ),
        ]
    for cell, (title, shown, unscored) in zip(cells, meters, strict=True):
        if cell == "" and not unscored:
            findings.append(
                SiteFinding(
                    "GRADE_PAGE_DISAGREES_WITH_THE_DATA",
                    where,
                    f"the page scores {title} as {shown!r} and the published data does not "
                    f"score it at all",
                )
            )
        elif cell != "" and (unscored or shown != cell):
            findings.append(
                SiteFinding(
                    "GRADE_PAGE_DISAGREES_WITH_THE_DATA",
                    where,
                    f"the page shows {title} as {shown!r} and the published data says {cell!r}",
                )
            )
    return findings


def audit_published_grades(root: Path) -> list[SiteFinding]:
    """Every way the grades a build published break the contract, in a stable order.

    An empty list means every rule in :data:`GRADE_CODES` holds over this build. It does not
    mean a grade is right; the module docstring says what this deliberately does not decide.
    """
    try:
        text = (root / DATASET_CSV).read_text(encoding="utf-8")
    except OSError:
        return [
            SiteFinding(
                "GRADE_NOTHING_EXAMINED",
                DATASET_CSV,
                "was not written, so no published grade was read",
            )
        ]
    rows = list(csv.DictReader(io.StringIO(text)))
    findings = audit_rows(rows)
    if any(f.code == "GRADE_NOTHING_EXAMINED" for f in findings):
        return findings
    cards = _cards(root)
    if not cards:
        return [
            *findings,
            SiteFinding(
                "GRADE_NOTHING_EXAMINED",
                "api/endpoint",
                "carries no readable per-endpoint record, so no surface could be compared "
                "against another",
            ),
        ]
    findings += surface_differences(
        {
            DATASET_CSV: rows_as_published(rows),
            "api/endpoint": cards,
            API_INDEX: _index(root),
            SCORECARDS: _scorecards(root),
        }
    )
    for eid in sorted(cards):
        findings += _page_findings(root, eid, cards[eid])
    return sorted(findings, key=lambda f: (f.where, f.code, f.detail))
