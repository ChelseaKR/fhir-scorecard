# 0009. The readiness report says only what two files show, and sources every statement about the rule

## Status

Accepted - 2026-10-17

## Context

Payers and API vendors are preparing for the additional APIs CMS-0057-F introduces. The site
already holds what each payer-side endpoint's `/metadata` and SMART discovery documents declare,
including the app-to-server block (`backend.py`) that reports SMART Backend Services and Bulk
Data fields. A report over those observations is useful to that reader, and it is also the kind
of page where an unsupported sentence about a regulation does the most damage.

This repository does not cite or retain the CMS-0057-F rule text. Its statements about the rule
are in `README.md`, `docs/SAMPLING-FRAME.md` and the cohort files' notes, and they have not been
checked against the Federal Register or the eCFR. `docs/compliance-bundle-plan.md` describes the
rule differently from those files (it lists the Provider Directory API among the additions, where
the others name the Provider Access API), which is a reason not to repeat either wording beyond
what the files agree on.

## Decision

Add `/cms-0057-f/`, built on every publish by `src/fhir_scorecard/readiness.py`, under four rules.

**It states what two public files show and nothing else.** Every cell is an answer
`backend.answers` already produces from the two documents, or a count of those answers. Nothing is
requested or inferred. Five states are counted per question and never merged: declared, not listed,
absent, could not be read, not retrieved. A document that was not retrieved is never counted as a
field that was not declared.

**It grades nothing and decides nothing.** No letter, score, weight, finding or listing is read
from or changed by the page. The APIs the rule adds are not in force yet and are not graded. It
makes no compliance determination, consistent with `ROADMAP.md`.

**Statements about the regulation are a closed list.** `readiness.CLAIMS` holds each sentence, a
status, and the file and words in this repository that support it. A test fails if a quoted
passage stops being present in its file, and fails if a sentence naming the rule appears on the
page outside the list. The status is either "this project's own rule" or "stated in this project's
documents, not checked against the rule text"; no entry claims verification against the rule, and
a test fails if a primary source is ever cited in the documents, so the status gets revisited
then. The page also lists what it does not assert: which organizations the rule applies to, what
each API must do, the date of any individual API, and whether any field listed is required.

**No date is invented.** The page carries the date of the run it was built from and the words
"January 2027" inside a quoted claim. A test rejects any other year or month-and-day.

## Consequences

- The report can be built from the same run as everything else, with no new request.
- It does not say which fields the rule requires, so it cannot say a payer is ready or not. It
  reports declared fields, and a reader decides what they mean for the API they are building.
- Replacing an "unverified" status needs a retained source for the rule, with a retrieval date
  and hash, in the way `corpus/SOURCES.json` retains the HL7 pages. That is a separate change.
- The disagreement between `docs/compliance-bundle-plan.md` and the other documents about which
  API the rule adds is not resolved here. This page uses only what `README.md`,
  `docs/SAMPLING-FRAME.md` and the cohort notes agree on.
