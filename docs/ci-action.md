# CI Action: check your own FHIR endpoint before it ships

Run the same grading this project publishes against **one** FHIR base URL inside a GitHub
Actions workflow, and fail the build when the endpoint's public discovery documents fall below
a grade you choose.

This is written for the operator of the endpoint being checked. Point it at your own base URL
in your own CI and catch a CapabilityStatement regression before it reaches anyone. The action
publishes nothing, writes to no shared record, and reads only the two documents this project
already reads: `/metadata` and `/.well-known/smart-configuration`. It never authenticates and
never touches patient data.

## What it does, and what it is not

It performs one request for each of the two documents from one runner, grades what came back
against [the published method](https://chelseakr.github.io/fhir-scorecard/how-we-grade/), and
exits non-zero if a threshold you set was not met.

It is not an audit, a compliance determination, or a statement about care quality. It describes
what a public document declared on one day, from one host on one network. A grade is comparable
within a `kind` only; the action never compares your endpoint to anyone else's, and there is no
input that would let it.

## Quick start

```yaml
name: FHIR endpoint check
on:
  pull_request:
  schedule:
    - cron: "0 8 * * *"

jobs:
  fhir:
    runs-on: ubuntu-latest
    steps:
      - uses: ChelseaKR/fhir-scorecard@<commit-sha>
        with:
          base-url: https://fhir.example.org/r4
          name: Example Health
          kind: payer
          min-grade: B
```

Reference the action by **commit SHA**. This project is pre-release: there is no release tag
yet, and pointing at one that does not exist is exactly the kind of claim it tries not to make.

## Inputs

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `base-url` | yes | | FHIR base URL. HTTPS only; the two discovery paths are appended. |
| `min-grade` | no | _(skip)_ | Fail if the measured grade is below this letter: A, B, C, D, or F. |
| `name` | no | the host | Display name in the report. |
| `kind` | no | `reference` | `reference`, `payer`, `payer_provider_directory`, `ehr`, or `provider`. Selects the interop expectations; grades are comparable within a kind only. |
| `expects` | no | `r4` | Declared-intent release the CapabilityStatement should carry: `stu3`, `r4`, or `r5`. A deliberately-R5 server is not marked down for not being R4. |
| `json` | no | runner temp file | Path for the complete result artifact. |
| `summary` | no | `true` | Write a plain-language result to the job summary. |
| `python-version` | no | `3.12` | Python used to run the bundled checker. |

Leave `min-grade` blank and the step is informational: it reports what it saw and always
passes.

## Exit codes

The check is also a plain CLI (`fhir-scorecard check <base-url>`), and the action passes its
status straight through.

| Code | Meaning |
|------|---------|
| `0` | The check ran, and every threshold the caller set was met. With no threshold set, this is the outcome whatever the grade was. |
| `1` | A threshold the caller set was not met. Only ever produced by a threshold the caller asked for. |
| `2` | Input error: a non-HTTPS base URL, an unknown `kind` or `expects`, an unwritable result path. Never a statement about the endpoint. |

The distinction in the last two rows is the point. Elsewhere in this project a finding is data
and never an exit code, because a run that observes something about a document someone else
published has not failed. A threshold is different: the caller supplied it, about their own
endpoint, and asked to be told.

## An endpoint that was not reached

If no document came back, the grade is the string `not observed`, never `F`. `F` means the
endpoint answered and what it published scored below the D threshold; `not observed` means this
run has nothing to say about what it publishes.

With `min-grade` set, a `not observed` result **fails** the step, because the threshold could
not be evaluated and a gate that cannot be evaluated must not report a pass. The annotation says
that in those words and does not report a letter. With no `min-grade`, it passes and simply
reports what happened.

## Outputs

| Output | Meaning |
|--------|---------|
| `grade` | `A`–`F`, or the string `not observed`. Do not compare `not observed` against a letter. |
| `observed` | `true` when the run retrieved the documents and produced a letter. |
| `reachable` | `true` when the endpoint answered this run's request for `/metadata`. |
| `passed` | `true` when the check ran and every threshold was met. |
| `result-json` | Path to the complete result, written **before** the threshold is applied, so it survives a failing gate. |

```yaml
      - id: fhir
        uses: ChelseaKR/fhir-scorecard@<commit-sha>
        with:
          base-url: https://fhir.example.org/r4
          min-grade: B
          json: artifacts/fhir-check.json

      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: fhir-check
          path: artifacts/fhir-check.json
```

`if: always()` keeps the evidence for the run that failed, which is the run you want it for.

## How it runs

A composite action: set up Python, create a virtualenv under the runner's temporary directory,
and install the checker bundled with this action release. The distribution has no runtime
dependencies, so nothing is resolved at run time and the code that runs is the code at the ref
you pinned. Release archives carry only the action runtime, not the curated registry, the
cohort files, or the captured fixtures.

## What it deliberately does not do

- **No history.** One observation is not a record of one, so the result carries no availability
  percentage, no first-seen date, and no drift events. Those come from the daily multi-vantage
  run that publishes the site.
- **No registry entry.** Every endpoint on the published site carries a record of how and when
  it was verified, and who it may be attributed to. A check has no such record to make, so it
  names the host you gave it and nothing else.
- **No publication.** Nothing is written to `data/`, no page is rendered, and no result reaches
  the site or the dataset.
