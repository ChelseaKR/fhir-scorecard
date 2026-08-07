# 0001. RELEASE-AND-VERSIONING-STANDARD: declare N/A (continuously published, not consumed downstream)

## Status

Accepted - 2026-08-07

## Context

RELEASE-AND-VERSIONING-STANDARD requires every repo to either produce releases (tags driving a
release workflow, CHANGELOG-correlated version bumps, published artifacts) or explicitly declare
N/A with a reason. There is no silent default.

fhir-scorecard's shipped artifact is the site and dataset at
`https://chelseakr.github.io/fhir-scorecard/`, rebuilt and republished daily from `main` by
`.github/workflows/pages.yml`. Nothing pins to a fhir-scorecard version: no package is published
to PyPI, no container image is distributed, no reusable action is exported, and no downstream
repo imports this one as a dependency. Consumers of the data cite the dataset (CITATION.cff)
and read the currently published files; the MCP server reads those same published files.

A local milestone tag `v0.1.0` (2026-08-05) exists as a history marker. It was never pushed,
never produced a release artifact, and nothing consumes it. CITATION.cff records the matching
`version`/`date-released` pair for citation purposes.

## Decision

Declare Release & Versioning N/A for this repository (continuously published; not consumed
downstream). Daily publication from `main` is the delivery model, not a gap in an
otherwise-release-based one. No tag-triggered release workflow is added.

The data itself is still versioned in the ways that matter to its consumers: every published
run is dated, capability drift is tracked against prior observations, and `data/history.json`
accrues availability history across runs.

## Consequences

- README's Standards Conformance table carries `Release & Versioning | N/A` citing this ADR.
- `CHANGELOG.md` is still required and kept (the Documentation Standard forbids marking it
  N/A); its entries are dated records of change rather than release notes.
- CITATION.cff continues to describe the dataset and may carry a milestone version.

## Revisit if

fhir-scorecard ever gains a downstream consumer that pins a version (a published package, a
container image, a versioned API contract, or another repo importing it). At that point the
full release control set, including the hardened tag-triggered release workflow, becomes
required.
