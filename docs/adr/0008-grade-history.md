# 0008. An opt-in, append-only record of the letter each run published

## Status

Proposed - 2026-10-17. Pending the maintainer's sign-off. The code is merged-ready and inert:
nothing in the daily workflow passes the flag, so accepting this ADR is what turns recording on.
Rejecting it means removing one module, one flag and one test file.

## Context

`data/history.json` retains availability observations and a capability fingerprint. It has never
retained a grade, so no run can say whether an endpoint's letter moved since the last one.
`ROADMAP.md` phase 12 records this as an open decision, because "recording a grade per run would
be a change to the data the daily workflow persists". `/over-time/` states the limitation on the
page, and `tests/test_over_time.py::test_the_committed_history_really_does_not_retain_a_grade`
holds the statement true of `data/history.json`.

Deriving a past letter from the stored fingerprint would invent a measurement nobody took, so
the only honest source of "the letter on 2026-11-03" is a run that wrote it down on 2026-11-03.

## Decision

Add `src/fhir_scorecard/grade_history.py` and one `grade` flag, `--grade-history PATH`.

**A separate file, not a field in `history.json`.** The observation record keeps meaning exactly
what the archive, the feeds and `/over-time/` say it means, and its guard test stays true. A run
without the flag reads nothing, writes nothing, and publishes byte-identical output to a run
before this change (`tests/test_grade_history.py` holds that).

**A row is `{"date", "grade"}`**, where `grade` is a letter or the literal `not observed`, the
vocabulary `dataset.csv` already uses. No score, finding or vantage is stored. The file carries
`_meta.schema_version` (currently 1) and `_meta.mode`; a reader refuses a newer schema version,
and an `--offline` run refuses a record a live run wrote, for the reason `drift.ensure_mode`
gives.

**Append-only, with one stated exception.** Rows for earlier dates are never edited or removed.
A run on a date that already has a row replaces that one row, so a same-day re-run with the same
inputs rewrites identical bytes, and a re-run after a degraded first attempt can replace it. A
run dated before the newest row is refused before any probe is made. An endpoint that leaves the
registry keeps its rows, and an endpoint a run did not grade gets no row.

**Deterministic.** The file is a function of the `(date, endpoint, letter)` sequence it was given.
Keys are sorted, nothing reads a clock, and the date is the same `today` the observation record
uses.

**A malformed record stops the run.** A file that exists and cannot be read, or holds a row this
module could not have written, raises. Dropping the row and saving would destroy part of an
append-only record.

**"Changed since the last run" compares letters, and skips silence.** `change_for` compares this
run's letter with the most recent earlier row that has one. A run that observed nothing
publishes `not observed`, which is not a grade, so it is skipped when looking back and a
`not_observed_this_run` status is published instead of a fall. The date of the row compared
against is always published (`previous_date`), so a gap is visible. Comparison uses only rows
strictly before the run's date, so a re-run cannot turn a change into "unchanged". The four
statuses are `first_recorded`, `unchanged`, `changed` (with `direction` `improved` or
`declined`) and `not_observed_this_run`.

**Where it is published, only when a record was kept.**

- `api/endpoint/<id>.json` gains a top-level `grade_change` object (status, grade, previous
  grade and date, direction, and the sentence stating the comparison).
- `api/index.json` entries gain a compact `grade_change` and a `grade_history` URL, the latter
  only for a file this build wrote.
- `api/grade-history/<id>.json` lists every row for one endpoint, with a note saying the record
  starts on the first run that wrote it and says nothing about earlier days.

`dataset.csv`, `scorecards.json`, every page and every grade are unchanged. The key is absent,
not `null`, on a run that kept no record, because `null` or `unchanged` there would state a
comparison nobody made.

## Not decided here

- **Feeds.** The Atom feeds are documented as a pure view of `history.json` with exactly four
  event kinds, and the feed contract says they report no grade. Adding a fifth kind changes that
  contract, so grade changes are in the JSON API only.
- **`/over-time/` and the README sentence** that the record "has never retained a grade" describe
  `history.json` and stay true of it. If recording is switched on, those sentences should be
  revisited to say that a separate record starts on its first date.
- **Retention.** Rows are unbounded. Measured with 88 endpoints and one row per day, the
  file is about 2.2 MB per year in the repository's `indent=2` style. A cap would contradict
  "append-only", so none is imposed; compact serialization or run-length rows are options if size
  matters.
- **Rows for endpoints not graded** (removed from the registry, or disabled) are kept as is.

## Enabling it

Accepting this ADR needs a workflow change in `.github/workflows/pages.yml`, which this change
deliberately does not make. In the `grade` job:

1. Restore `grade-history.json` from the `capability-history` branch next to `history.json`,
   treating "branch exists but file absent" as a first run and an unreadable file as a failure,
   the way the existing restore step does.
2. Pass `--grade-history data/grade-history.json` to the **publishing** run only. The `probe`
   jobs grade one vantage each and must not write the record.
3. Extend the persist step to write `grade-history.json` into the same commit on
   `capability-history`.

`tests/test_workflow_shell_safety.py` and `tests/test_live_integrity_pending_publish.py` read
these workflows, so the change needs those to stay green.

## Consequences

- Once enabled, "which endpoints changed letter this week" becomes answerable from a static file,
  starting on the day the first row is written and not before.
- Until enabled, the flag is dead code on the daily path; its behaviour is covered by tests that
  call the command.
- The record is only as good as the publishing run's grade. A run that reconciles fewer vantages
  can publish `not observed` where another day published a letter; that shows as
  `not_observed_this_run`, never as a decline.
- A reversal is one module, one flag, the `grades=` parameter of `write_dataset`, and the test
  file. Nothing else reads the record.
