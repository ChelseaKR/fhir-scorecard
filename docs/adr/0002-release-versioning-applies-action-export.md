# 0002. RELEASE-AND-VERSIONING-STANDARD: applies (a reusable Action is now exported)

## Status

Accepted - 2026-08-16

Supersedes [0001](0001-release-versioning-na.md).

## Context

ADR 0001 declared Release & Versioning N/A on 2026-08-07. Its reasoning rested on four
statements of fact, all true when written: no package is published to PyPI, no container image
is distributed, **no reusable action is exported**, and no downstream repo imports this one as a
dependency.

On 2026-08-16, PR #31 (`a772034`) merged a composite GitHub Action to `main`: `action.yml`,
`action/render_result.py`, `docs/ci-action.md`, and the `fhir-scorecard check <base-url>`
command behind them. The third statement is now false, and the fourth follows from it.

The dependency is direct and version-pinned by construction, not by analogy:

- A consumer writes `uses: ChelseaKR/fhir-scorecard@<ref>` in their own workflow. GitHub
  downloads this repository's source archive at that ref onto their runner.
- `action.yml` then runs `pip install "${GITHUB_ACTION_PATH}"`, so the code executing in
  somebody else's build is this repository's code at the ref they named.
- `.gitattributes` carries `export-ignore` bounds that decide what is in that archive. Those
  bounds are now part of a shipped interface, resolved per ref.

A ref is a version pin. Today the only refs a consumer can name are `main`, which is not a
version, and a commit SHA, which pins immutably but tells them nothing about compatibility and
gives them no notes to read before moving. That is precisely the gap the standard exists to
close.

ADR 0001 anticipated this and named the consequence itself, under "Revisit if": a downstream
consumer that pins a version means "the full release control set, including the hardened
tag-triggered release workflow, becomes required."

Nothing about the site and dataset has changed. `pages.yml` still rebuilds and republishes
`https://chelseakr.github.io/fhir-scorecard/` daily from `main`, and consumers of the data still
cite the dataset and read the currently published files. Release & Versioning applies *in
addition to* continuous publication, not instead of it. ADR 0001 was correct on the facts it
had; it is superseded because the facts changed, not because it was wrong.

## Decision

Release & Versioning **applies** to this repository. The Action is the released artifact, a
GitHub Release at a signed tag is the release, and the tag is what consumers pin.

1. Add `.github/workflows/release.yml`, modelled on the sibling `ChelseaKR/ctdl-validate`
   workflow and sharing its trust boundary:
   - `workflow_dispatch` with a required existing-tag input, never firing on tag push. A tag
     push is not a review; a dispatch against an already-reviewed tag is.
   - The shared `ChelseaKR/.github` `release-authorize.yml` reusable workflow, SHA-pinned,
     verifies the tag is annotated, is stable SemVer `vX.Y.Z`, is SSH-signed by a principal in
     this repository's `.github/allowed_signers`, and selects a commit that is an ancestor of
     `main`. It returns immutable identifiers that every later job checks out by.
   - The merge-blocking gates re-run at the tagged commit before anything is built: `make
     verify`, the gate of record, and the full-history secret scan. A prior green checkmark on a
     pull request is not evidence about the tagged tree.
   - The build is cache-free, attests SLSA build provenance, and takes its release notes from
     the matching `CHANGELOG.md` section. Tag, `pyproject.toml` version, and CHANGELOG heading
     must agree or the release fails.
2. Add `.github/allowed_signers`, which the shared authorize workflow requires and which makes
   the set of identities permitted to author a release explicit and reviewable in-tree.
3. Version `pyproject.toml` in step with the tags. CHANGELOG sections become release notes,
   which is what the Documentation Standard already required them to be able to be.
4. Cut `v0.1.0` as the first release. The `[0.1.0]` CHANGELOG section dated 2026-08-05, and the
   local milestone tag ADR 0001 recorded, never produced a release artifact, were never pushed,
   and were never consumable, so `0.1.0` is unclaimed and reusing it publishes nothing under a
   number that already meant something else. `[Unreleased]` folds into it.

## Consequences

- README's Standards Conformance table changes `Release & Versioning` from `N/A` citing ADR 0001
  to `Applies`, citing this ADR.
- `docs/ci-action.md` stops telling consumers there is no release tag and pins its examples to
  a real one. Commit-SHA pinning remains documented as the stricter option, which is what this
  repository asks of its own dependencies.
- Releasing now costs a signed annotated tag, a version bump, `uv lock`, and a CHANGELOG
  section that can stand as release notes. That is the price of being a dependency.
- CITATION.cff continues to describe the dataset. Its `version`/`date-released` now track the
  released tag rather than a local marker.
- The daily Pages publication is untouched. Two delivery models now run side by side, and the
  README says which artifact each one delivers.

## Revisit if

The Action is withdrawn and no other version-pinned consumer remains, at which point the
question ADR 0001 answered would be live again and would need answering on the facts of that
day. A move to publish the distribution on PyPI or to distribute a container image does not
require revisiting this decision, only extending the workflow's publish targets under it.
