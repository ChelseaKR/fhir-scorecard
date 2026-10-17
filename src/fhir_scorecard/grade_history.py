"""An append-only, dated record of the letter each run published, and what changed since.

``data/history.json`` retains availability and a capability fingerprint and has never retained a
grade, so no run could say whether an endpoint's letter moved
(``docs/adr/0008-grade-history.md``). This module is the separate record that can. It is a file
of its own, not a field in ``history.json``, for two reasons that are both about not changing
what already exists: the observation record keeps meaning exactly what its readers (the archive,
the feeds, ``/over-time/``) say it means, and a run that is not given this file behaves and
writes exactly as it did before.

**What a row is.** ``{"date": "YYYY-MM-DD", "grade": "B"}``: the letter the run published for
that endpoint on that date, or the literal ``not observed``, which is the vocabulary
``dataset.csv`` already uses. Nothing else is stored. No score, no finding, no vantage.

**Append-only, and what that allows.**

* A row for an earlier date is never edited or removed.
* A run on a date the file already has a row for replaces that one row, so a re-run the same day
  with the same inputs rewrites the same bytes and a re-run after a degraded first attempt
  replaces it with the better reading. :func:`record` is idempotent in that sense.
* A run dated *before* the newest row raises. Writing it would insert into the past.
* An endpoint that leaves the registry keeps its rows. An endpoint a run did not grade gets no
  row for that run: a missing row is not a ``not observed`` row.

**Deterministic.** The file is a function of the sequence of ``(date, endpoint, letter)`` it was
given. Nothing reads a clock; the caller passes the date, and keys are sorted on write.

**"Changed since the last run" is not the same as "different from the last row".**
:func:`change_for` compares today's letter with the most recent *earlier* row that carries a
letter. A run that observed nothing publishes no letter, and reading it as a fall would be the
conflation ``grading.NOT_OBSERVED`` exists to prevent, so such rows are skipped when looking
back and the date of the row actually compared against is always published, which makes a gap
visible rather than hidden. Comparison is against rows strictly before today's date, so re-running
a day cannot turn a change into "unchanged".

**Fail closed on read.** A file that exists and cannot be read, or holds a row this module could
not have written, raises. Dropping a malformed row and saving would destroy part of an
append-only record, which is the one thing it must not do.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard.drift import META_KEY
from fhir_scorecard.gate import GRADE_ORDER
from fhir_scorecard.grading import NOT_OBSERVED, Scorecard

SCHEMA_VERSION = 1

#: Directory of the per-endpoint record under ``api/``.
API_DIR = "grade-history"

#: Every value a row's ``grade`` can hold. Stated here, and held equal to the grader's by a test.
GRADES: tuple[str, ...] = (*GRADE_ORDER, NOT_OBSERVED)

_RANK = {letter: rank for rank, letter in enumerate(GRADE_ORDER)}

FIRST_RECORDED = "first_recorded"
UNCHANGED = "unchanged"
CHANGED = "changed"
NOT_OBSERVED_THIS_RUN = "not_observed_this_run"

#: Every status a ``grade_change`` object can carry, with the sentence that says what it means.
#: A renderer or consumer is held to this map by ``tests/test_grade_history.py``.
STATUSES: dict[str, str] = {
    FIRST_RECORDED: "no earlier run in this record published a letter for this endpoint",
    UNCHANGED: "the letter is the same as the one on the most recent earlier run that had one",
    CHANGED: "the letter differs from the one on the most recent earlier run that had one",
    NOT_OBSERVED_THIS_RUN: (
        "this run published no letter, so there is nothing to compare; this is not a fall"
    ),
}

IMPROVED = "improved"
DECLINED = "declined"

#: Said once on every surface that publishes a change, so nobody has to infer the comparison.
BASIS = (
    "Compared with the most recent earlier dated run that published a letter. A run that "
    "observed nothing publishes no letter and is skipped, never read as a fall; "
    "previous_date says which run was compared against, so a gap is visible. "
    "Grades are comparable within a kind only."
)

#: What the per-endpoint record says about itself.
RECORD_NOTE = (
    "One row per dated run that graded this endpoint, oldest first: the letter that run "
    "published, or 'not observed' where it published none. Rows are appended, never edited "
    "after their date. The record starts on the first run that wrote it and says nothing "
    "about any earlier day. A day with no row is a day no run wrote one, not a day the "
    "endpoint was not observed."
)


@dataclass(frozen=True)
class GradeRow:
    date: str
    grade: str


@dataclass(frozen=True)
class GradeChange:
    """What one run's letter did relative to the record before it."""

    status: str
    #: This run's published value: a letter, or ``not observed``.
    grade: str
    previous_grade: str | None = None
    previous_date: str | None = None
    #: ``improved`` or ``declined``, only where ``status`` is ``changed``.
    direction: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "status_text": STATUSES[self.status],
            "grade": self.grade,
            "previous_grade": self.previous_grade,
            "previous_date": self.previous_date,
            "direction": self.direction,
            "basis": BASIS,
        }


@dataclass(frozen=True)
class Published:
    """What one run knows about grade history, in the shape the dataset writer reads."""

    changes: Mapping[str, GradeChange]
    rows: Mapping[str, tuple[GradeRow, ...]]


def _valid_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _rows_of(entry: object, endpoint_id: str) -> list[dict[str, str]]:
    """The rows of one ledger entry, or raise saying exactly what is wrong with it."""
    if not isinstance(entry, dict) or not isinstance(entry.get("grades"), list):
        raise ValueError(f"grade history for {endpoint_id!r} is not an object with a grades list")
    rows: list[dict[str, str]] = []
    previous = ""
    for row in entry["grades"]:
        ok = (
            isinstance(row, dict)
            and set(row) == {"date", "grade"}
            and _valid_date(row["date"])
            and row["grade"] in GRADES
        )
        if not ok:
            raise ValueError(f"grade history for {endpoint_id!r} holds a row this tool cannot read")
        if row["date"] <= previous:
            raise ValueError(
                f"grade history for {endpoint_id!r} is not in strictly increasing date order "
                f"at {row['date']}"
            )
        previous = row["date"]
        rows.append(row)
    return rows


def load(path: Path) -> dict[str, Any]:
    """Read the record, or ``{}`` for a first run, or raise saying why this run must not go on.

    An absent file is a first run. A file that exists and cannot be read is not, for the reason
    :func:`fhir_scorecard.drift.load_history` gives: treating it as empty would start a new
    record and overwrite the one that could not be read.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(
            f"{path} exists but is not readable JSON ({exc}); refusing to continue rather than "
            "start a new record over one this run could not read"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} holds {type(raw).__name__}, not an object; refusing to continue")
    meta = raw.get(META_KEY)
    version = meta.get("schema_version") if isinstance(meta, dict) else None
    if isinstance(version, int) and version > SCHEMA_VERSION:
        raise ValueError(
            f"{path} is schema version {version} and this tool reads up to {SCHEMA_VERSION}; "
            "refusing to rewrite a record it does not fully understand"
        )
    for key, entry in raw.items():
        if key != META_KEY:
            _rows_of(entry, key)
    return raw


def ensure_mode(ledger: dict[str, Any], *, offline: bool) -> None:
    """Refuse to mix fixture runs and live runs in one record, as ``drift.ensure_mode`` does.

    A fixture run writes letters for endpoints nobody probed. There is no override flag, because
    an override is the thing that gets passed in a hurry.
    """
    mode = "offline" if offline else "live"
    meta = ledger.get(META_KEY)
    recorded = meta.get("mode") if isinstance(meta, dict) else None
    if isinstance(recorded, str) and recorded != mode:
        raise ValueError(
            f"this grade record was written by a {recorded} run and this is a {mode} run; "
            f"point --grade-history at a scratch path rather than mixing {recorded} and {mode} "
            "grades in one record"
        )
    ledger[META_KEY] = {"mode": mode, "schema_version": SCHEMA_VERSION}


def save(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refuse_past_dates(ledger: Mapping[str, Any], today: str) -> None:
    """Raise if any endpoint already holds a row dated after ``today``.

    Called at the start of a run, before any probe, and again by :func:`record` before it
    changes anything, so the append-only rule holds for a caller that skipped the first.
    """
    if not _valid_date(today):
        raise ValueError(f"{today!r} is not a YYYY-MM-DD date")
    for endpoint_id, entry in ledger.items():
        if endpoint_id == META_KEY:
            continue
        rows = _rows_of(entry, endpoint_id)
        if rows and rows[-1]["date"] > today:
            raise ValueError(
                f"grade history for {endpoint_id!r} already holds a row dated "
                f"{rows[-1]['date']}, after this run's date {today}; refusing to write into "
                "the past"
            )


def rows_for(ledger: Mapping[str, Any], endpoint_id: str) -> tuple[GradeRow, ...]:
    """The rows one endpoint holds, oldest first. Empty where there are none."""
    entry = ledger.get(endpoint_id)
    if entry is None:
        return ()
    return tuple(GradeRow(r["date"], r["grade"]) for r in _rows_of(entry, endpoint_id))


def change_for(earlier: Iterable[GradeRow], current: str) -> GradeChange:
    """What ``current`` did relative to ``earlier``, which must be rows before this run's date."""
    if current == NOT_OBSERVED:
        last = _last_letter(earlier)
        return GradeChange(
            NOT_OBSERVED_THIS_RUN,
            current,
            previous_grade=last.grade if last else None,
            previous_date=last.date if last else None,
        )
    last = _last_letter(earlier)
    if last is None:
        return GradeChange(FIRST_RECORDED, current)
    if last.grade == current:
        return GradeChange(UNCHANGED, current, last.grade, last.date)
    direction = IMPROVED if _RANK[current] > _RANK[last.grade] else DECLINED
    return GradeChange(CHANGED, current, last.grade, last.date, direction)


def _last_letter(rows: Iterable[GradeRow]) -> GradeRow | None:
    lettered = [row for row in rows if row.grade != NOT_OBSERVED]
    return lettered[-1] if lettered else None


def record(ledger: dict[str, Any], cards: Iterable[Scorecard], today: str) -> Published:
    """Append ``today``'s letter for each card, mutating ``ledger``; returns what changed.

    See the module docstring for the append-only rules. Raises, before changing anything, if
    any endpoint already holds a row dated after ``today``, including an endpoint not graded in
    this run: the rule is about the record, not about the cards in hand.
    """
    refuse_past_dates(ledger, today)
    cards = list(cards)
    existing = {card.endpoint_id: list(rows_for(ledger, card.endpoint_id)) for card in cards}

    changes: dict[str, GradeChange] = {}
    published: dict[str, tuple[GradeRow, ...]] = {}
    for card in cards:
        rows = existing[card.endpoint_id]
        earlier = [row for row in rows if row.date < today]
        changes[card.endpoint_id] = change_for(earlier, card.grade)
        rows = [*earlier, GradeRow(today, card.grade)]
        published[card.endpoint_id] = tuple(rows)
        ledger[card.endpoint_id] = {"grades": [{"date": r.date, "grade": r.grade} for r in rows]}
    return Published(changes=changes, rows=published)


def record_json(endpoint_id: str, name: str, rows: Iterable[GradeRow]) -> str:
    """``api/grade-history/<id>.json``: a pure function of the rows, with no build stamp."""
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "endpoint_id": endpoint_id,
            "name": name,
            "note": RECORD_NOTE,
            "rows": [{"date": row.date, "grade": row.grade} for row in rows],
        },
        indent=2,
        sort_keys=True,
    )
