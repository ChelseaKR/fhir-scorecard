# PR triage, 2026-08-28

Nine open pull requests and one open issue, triaged against `main` at `c587169`.

Method: every PR read in full (`gh pr view`, `gh pr diff`, base branch, `mergeStateStatus`,
CI conclusion **and the annotation behind it**). Diffs were read statically from local branches,
which were confirmed to match the PR head SHAs exactly, so nothing here rests on a stale
checkout. No test suite was run. Every number below that could be recomputed from committed
data was recomputed; the "verified vs taken on trust" section at the end says which.

## Summary table

| PR | Title | Base | What it does | Real merge state | Recommendation |
|---|---|---|---|---|---|
| 51 | Bump `codeql-action/analyze` 4.37.7 to 4.37.8 | `main` | One SHA pin in `security.yml` | `verify` **green**; CodeQL **genuinely failed** (not starved) | **merge, back to back with #52** |
| 52 | Bump `codeql-action/init` 4.37.7 to 4.37.8 | `main` | One SHA pin in `security.yml` | `verify` **green**; CodeQL **genuinely failed** (not starved) | **merge, back to back with #51** |
| 55 | Accessibility and weight budgets | `main` | Twelve mechanical HTML rules + two transfer-size budgets, wired into the publish gate | All checks green | **needs work** (two WCAG over-citations) |
| 56 | Archive surface | `main` | `/history/` index, per-endpoint record pages, `api/history/*.json` | All checks green | **merge** (after the #55 fix lands beneath it) |
| 57 | Drift timeline | `main` | Per-endpoint declaration timeline, returns counted apart from changes | All checks green | **needs work - blocking defect** |
| 58 | Availability leaderboard | `main` | Per-kind availability tables above a 14-observation floor | All checks green | **needs work** (inherits #57) |
| 59 | Coverage tracker page | `main` | `/coverage/`, 176 state-issuers into four populations | All checks green | **needs work - blocking defect** |
| 60 | Conformance over time | `main` | `/over-time/`, month-by-month record | All checks green | **needs work** (inherits #57, #59) |
| 61 | Dataset snapshots | `main` | `snapshot` / `verify-snapshot` CLI, dated dataset copy + manifest | All checks green | **needs work** (inherits #57, #59); then **merge as the single landing** |

One open issue, **#49**, is **already fixed on `main`** and should be closed. See the last section.

## The stack, and the thing that is not true about it

Every one of the nine PRs targets `main`. **No PR is based on another PR's branch, so no PR would
be auto-closed if some other PR's base branch were merged and deleted.** The bodies of #55 to #61
say they were stacked on #54, and they were; #54 merged as `c587169` and all seven were correctly
retargeted to `main` before I looked. That hazard has already been handled.

What *is* true is subtler and matters more:

```
main c587169
  |
  +-- #55 feat/accessibility-and-weight-budgets   [A]
  +-- #56 feat/archive-surface                    [A'][B]
  +-- #57 feat/drift-timeline                     [A"][B'][C]
  +-- #58 feat/availability-leaderboard           [A"'][B"][C'][D]
  +-- #59 feat/coverage-tracker-page              [.. ][.. ][.. ][D'][E]
  +-- #60 feat/conformance-over-time              [.. ][.. ][.. ][.. ][E'][F]
  +-- #61 feat/dataset-snapshots                  [.. ][.. ][.. ][.. ][.. ][F'][G]
```

Seven **parallel** branches, each carrying a rebased copy of all the work before it. Verified:
`git merge-base --is-ancestor` is false for all 42 ordered pairs, yet `git diff` between
consecutive tips shows only the increment, and `git diff 4a6b77d 2df7570` (the two copies of
#55's commit) is **empty**. So the stack is *content*-linear and *commit*-parallel.

Three consequences:

1. **#61 already contains all seven features.** Merging it lands the whole series.
2. **Merging them in sequence conflicts at every step.** `git merge-tree` on each consecutive
   pair reports `CONFLICT (content)` in `CHANGELOG.md` and `src/fhir_scorecard/cli.py` - both
   sides inserted at the same anchor and git cannot see that one is a superset. Six trivial
   but manual resolutions.
3. **Merging #61 will not auto-close #55 to #60.** Their commits are not ancestors of the
   result, and never become so. Checked against a simulated merge: the merge base of each of
   them and the merged `main` is still `c587169`, so GitHub keeps showing each one its **full**
   diff, not an empty one - #55 still reads as 11 files and 963 insertions. `git merge-tree`
   against that merged `main` exits 1 for all six, so they read as *conflicting*, which is even
   less like "already merged" than an empty diff would be. All six must be closed by hand with a
   note saying they landed via #61.

**Defects propagate down the stack the same way features do.** #57 carries a blocking defect, so
#58, #59, #60 and #61 all carry it. #59 carries a second one, so #60 and #61 carry that too.
There is no prefix of this stack past #56 that is clean.

## Per-PR findings

### #51 and #52 - the dependabot pair, and why both are red

Both fail `CodeQL analyze (python)` and `CodeQL analyze (actions)`. **This is not billing
starvation.** I checked the annotation and the step count, per the prior:

- Job ran **9 steps** over 27 seconds. Steps 1 to 3 succeeded; step 4, `analyze`, failed.
- Annotation on #51: `Loaded a configuration file for version '4.37.7', but running version '4.37.8'`
- Annotation on #52: the exact mirror, `'4.37.8'` loaded, `'4.37.7'` running.
- Plus, on both: `Not all workflow steps that use github/codeql-action actions use the same version.`

The cause is structural, not a defect in either PR. `security.yml` pins `codeql-action/init` and
`codeql-action/analyze` to the same commit. Dependabot treats them as two dependencies and opens
two PRs, so **each PR in isolation makes the two versions disagree, and CodeQL refuses**. Both PRs
are individually correct and individually red, and neither can be green alone.

Note the ordering trap: **merging either one alone leaves `main` red**, because `main` then has
one action at 4.37.7 and the other at 4.37.8. This already happened once - #43 (analyze) merged
before #41 (init) at the 4.37.6 to 4.37.7 bump.

They *can* be merged: the active ruleset `protect-main` requires only the `verify` check, and
`verify` is **success** on both. CodeQL is not a required check.

**Recommendation for both: merge, back to back in one sitting**, either order, accepting that
`main` is briefly red between them. Then apply the `dependabot.yml` grouping fix described in
the last section so this stops recurring on every codeql-action release.

### #55 - accessibility and weight budgets

Adds `fhir_scorecard.accessibility` (twelve mechanical rules over built HTML) and
`fhir_scorecard.weight` (a per-page byte budget and a shared-subresource budget), and wires both
into the `audit-site` command that gates the daily publish.

**Correct in the parts that matter, with one citation problem that matters in this repo more
than it would in most.**

What holds up: the CI exit path is real. A finding reaches
`cli.py:530-532`, which returns 1, which `raise SystemExit(main())` propagates, which fails the
`pages.yml` audit step. There is no `continue-on-error`, no `|| true`, no pipe and no `shell:`
override, the step sits *before* `upload-pages-artifact`, and `deploy: needs: grade` carries no
`if:`. A failing gate means nothing is published. The wrapper-swallowing hazard is ruled out.
The `_VOID` parser fix is real and complete: all thirteen WHATWG void elements are listed, and
the fix is two-sided (both `handle_starttag` and `handle_endtag` return before touching depth).
Every one of the twelve rules has a single-mutation test asserting the *whole* finding list, and
the reference page genuinely satisfies all twelve rather than being silently vacuous on any.

**The defect: two rules cite a WCAG criterion that does not say what the rule says.**

- `A11Y_HEADING_LEVEL_SKIPPED` cites "WCAG 2.2 SC 1.3.1, Level A". WCAG does not require
  sequential heading levels; 1.3.1 requires programmatically-determined structure to match
  presented structure. axe-core tags `heading-order` as `best-practice`, deliberately not `wcag2a`.
- `A11Y_NO_MAIN_LANDMARK` cites "SC 1.3.1 and SC 2.4.1, Level A". No Level A criterion requires a
  `<main>` or `role="main"`. 2.4.1 Bypass Blocks is satisfied by a skip link to any target.
  axe-core likewise tags `landmark-one-main` as `best-practice`.

This is the same failure mode the PR *correctly* avoided twice: `A11Y_TITLE_NOT_UNIQUE` and
`A11Y_DUPLICATE_ID` are already labelled "this project's rule, not a criterion", and the PR is
careful to note that SC 4.1.1 Parsing was removed in WCAG 2.2 and is not cited. Two rules did not
get the same treatment. Nothing is fabricated - every criterion number and title that appears is
real and correctly levelled - but two rules wear a number that does not require them.

**Needs work, narrowly:** relabel those two as project rules the way the other two already are,
and update the "twelve, each naming a criterion" sentence in `README.md`, `CHANGELOG.md`,
`ROADMAP.md`, ADR 0004 and `pages.yml:139`. That makes it **eight** criterion-backed rules and
four project rules, not the ten and four written here first: twelve rules, two already labelled
as this project's own, two more moving across.

Two more worth fixing while there, neither blocking:

- **`audit_weight` is blind to a path-carrying origin.** `audit.py` strips the origin path prefix
  before resolving a reference; `weight.py` has no equivalent and `audit_weight(root)` takes no
  origin, and `cli.py:526` does not pass one. Under a project-page origin - a shape
  `tests/test_site_audit.py:349` explicitly supports and tests - every subresource resolves to a
  missing file, both budgets bound nothing, and the run reports `WEIGHT_SUBRESOURCE_MISSING`
  everywhere. It fails loud rather than passing quietly, and the live origin has no path, so it
  does not bite today.
- The "both budgets were measured, not chosen" line in the CHANGELOG and README overstates the
  page budget. 655,360 against a measured 624,450 is genuinely measured; 65,536 against a measured
  23,306 is a chosen round power of two, which `weight.py:11-18` says honestly in its own words.
  The summary prose is broader than the module's.

### #56 - archive surface

`/history/` index, `/history/<id>/` record pages, `api/history/<id>.json`. Correct.

The floor mechanism is genuinely load-bearing and the named hazard is **ruled out**: the tests
parametrize on `[1, MIN_OBSERVATIONS_TO_REPORT - 1]` and pair with `days = MIN_OBSERVATIONS_TO_REPORT`,
so the boundary is exercised at exactly 13 and exactly 14, not at values far apart. `answered_percent`
is `None` if and only if `observed < 14`, on a single code path, so a real 0% over 14 or more
observations still publishes `0` rather than `null` - the conflation is avoided in both directions.
The below-floor arithmetic has no off-by-one. I re-derived the PR's "move the constant" proof by
hand and both directions land exactly as claimed.

Two caveats, neither blocking:

- **The index lede overclaims against the storage layer.** It publishes "every result is kept.
  This is the record, not a summary of it", while `drift.py:46` sets `_MAX_OBSERVATIONS = 120` and
  `drift.py:388` evicts beyond it. The record page compounds this by telling readers "A day with
  no row is a day nothing was recorded", which will be false for every evicted day. The real
  record began 2026-08-05, so this becomes live in early December 2026.
- The "no zero" refusal is enforced on the record page but not the index, where a never-observed
  endpoint still renders `0`, `0`. The last column contextualises it, so this is a scope-of-claim
  problem rather than a misleading page.

### #57 - drift timeline. **Blocking defect.**

The good part is genuinely good: `declaration_returns` is read straight from `entry["alternations"]`
and `declaration_changes` from `entry["events"]`. Nothing re-implements or second-guesses
`drift._apply_alternation_rule`, so the two cannot disagree - there is no second opinion. Undated
events are dropped *before* counting, so the count and the list cannot disagree either.

**The defect: an endpoint whose CapabilityStatement was never once read is told its declaration
has not changed.**

```python
if not record.changes and not record.returns:
    observed = (
        "No change to what this endpoint declares has been recorded."
        if record.observations
        else "Nothing has been observed for this endpoint yet, so nothing could have changed."
    )
```

The predicate is `record.observations`, not `record.answered`. There are three empty states, not
two, and the third is routed into the wrong one. An endpoint probed daily that never answered has
no fingerprint, therefore no events, and so is published as having a stable declaration.

I confirmed this against the live record on `origin/capability-history` rather than taking it on
report. Of 45 endpoints, **five have observations and zero answered, and carry no `fingerprint`
key at all**:

| Endpoint | Observations | Answered | Has fingerprint |
|---|---:|---:|---|
| `avmed-provider-directory` | 8 | 0 | no |
| `christus-provider-directory` | 9 | 0 | no |
| `hcsc-provider-directory` | 9 | 0 | no |
| `imperial-patient-access` | 9 | 0 | no |
| `wellpoint-patient-access` | 9 | 0 | no |

Five pages would each carry a claim about what a publisher declares, derived from zero successful
reads of that declaration. **This is the same invariant as the commit at the head of `main`** -
"An endpoint that answered with nothing was published as though it had never answered (#50)" - in
a different surface. A real absence is being published as a positive finding.

The fix is one predicate and a third sentence; `record.answered` is already on the dataclass.

Two more, both real:

- **The "never dated unknown" refusal is one-sided.** It is honoured for change dates and violated
  for returns: `drift._apply_alternation_rule` can write the literal string `"unknown"` as a date,
  `archive._returns` accepts it (it only checks `isinstance(..., str)`), and the page renders
  "unknown: returned 3 times to a declaration first observed unknown".
- **`test_the_timeline_carries_nothing_the_history_does_not` is weaker than its billing.** It never
  calls `record_page`, so nothing constrains what is *rendered*; and for returns it skips
  `state_first_seen`, which is exactly the field that can carry supplied content -
  `state_first_seen=str(record.get("state_first_seen", "an earlier run"))` renders the literal
  phrase "first observed an earlier run", which is supplied, not sourced. The one test whose stated
  job is to catch supplied content exempts the field that supplies it.

Incidentally the PR body **understates its own case**: `la-care-provider-directory` has two
alternation groups (9 returns and 8), so merging returns into changes would read as 18 releases,
not the 10 the body claims.

### #58 - availability leaderboard

Correct on its own merits; inherits #57.

Claim 1 verified against the live record: 45 endpoints over 21 dates, **30 at or above the floor
and 15 below**, matching ADR 0005, the README and the CHANGELOG exactly. Ordering is deterministic
under ties (`-rate, -observed, name.lower()`, over 45 distinct names). The refusal publishes `None`,
not `0`. ADR 0005's three rules are all actually in the code. The named "data too far apart" hazard
is **ruled out** - the boundary tests run at exactly 14 and exactly 13.

I separately checked the committed-data test, `test_the_floor_excludes_something_in_the_data_this_repository_ships`.
The seed is **19 endpoints at exactly 2 observations each**, so on its own that test only proves the
floor is above 2. Its docstring is honest about this and points at the synthetic boundary tests for
the other side, which do exist and do run at the boundary. Worth knowing: because the boundary tests
are written relative to the constant, no test pins the value **14** itself; the absolute-fixture
index tests confine it to roughly (3, 40]. That is a reasonable design, not a defect.

One test-strength nit: `assert "%" not in body.split("Why fourteen")[0].replace("100%", "")` strips
precisely the value the fixture would produce if the floor leaked, so that assertion cannot fail for
the regression it is named for. The sibling test at 3 observations does cover it.

### #59 - coverage tracker page. **Blocking defect.**

The headline arithmetic is **right, and I recomputed all of it from committed data rather than
trusting the body**:

- `data/frames/qhp-landscape-py2026-individual-medical.csv`: 176 rows, 176 distinct
  `(state_code, issuer_name)` pairs, **30 distinct states**, but only **109 distinct issuer names**.
- `texas-marketplace.roster.csv` and `florida-marketplace.roster.csv`: 15 rows each, **all 30 present
  in the national frame, none missing**, no overlap between them as `(state, name)` pairs.
- 176 - 30 = **146 not yet reviewed**. Confirmed.

The 109-versus-176 gap also confirms the PR's central design point: a name-only join really would be
badly wrong, because a national carrier appears once per state it sells in. And the counts are
genuinely **derived**, not typed - the hazard I most expected here is **ruled out**.
`tests/test_coverage.py` rebuilds the classification from `data/registry.json`, `data/cohorts/` and
the frame CSV and asserts `counts(orgs)[NOT_YET_REVIEWED] == 146` against that recomputation, so
adding a cohort fails loudly rather than going stale.

**The defect: the member lookup is keyed on issuer name alone, across all cohorts, so one state's
review is published under another state's heading.**

```python
members = {
    member.roster_name: member
    for cohort in cohorts
    for member in cohort.members
    if member.roster_name
}
```

The `(state, issuer_name)` key is applied only to *reviewed-ness*, one line later. The member whose
prose gets published is looked up by name alone. I verified from committed data that **three
`roster_name` values appear in both the Texas and Florida cohorts**: `Cigna Healthcare`,
`Molina Healthcare`, `UnitedHealthcare`. `load_cohort_dir` sorts by filename, so
`texas-marketplace.json` loads last and overwrites Florida's members.

For `Molina Healthcare` the two cohorts hold **materially different** exclusion reasons - Texas's is
a developer portal that prints no hostname; Florida's is an Azure API Management instance operated
by Cognizant TriZetto naming "FL - Molina Healthcare". The Florida row would publish **Texas's**
review text. Florida's actual finding is never published anywhere.

This is precisely the failure the module's own docstring says the join key exists to prevent:
"would have published an Alabama issuer's status on the strength of reading a Texas issuer's
developer portal."

Severity today is bounded - all three colliding pairs land in the same population from either side,
so 13/2/15/146 are correct and the headline is correct. But one published sentence mis-attributes a
review, and it is not hypothetical: the frame contains `OH,Molina Healthcare` and
`OH,UnitedHealthcare`, and a `feat/ohio-marketplace-cohort` branch already exists. The first cohort
that lands with a differing outcome for a shared name flips the **population**, not just the prose.

No test catches it: the existing collision test only checks that names shared *outside* TX and FL
stay unreviewed, and the detail test asserts only that `org.detail` is non-empty, never that it came
from the right cohort. Fix: key the lookup on `(state, roster_name)`.

The `cohort.py` change is 14 lines, purely additive, and does not regress the published California,
Texas or Florida pages: `roster_name` was already in the committed data on `main`, and `data/` is
byte-identical between `main` and this tip. #59 only teaches the parser to read what was there.

One prose imprecision: the claim that a name-only join "credited 23 states" is 21 on a literal
reading (23 counts TX and FL themselves).

### #60 - conformance over time

Correct with one substantive caveat; inherits #57 and #59.

The refusal is real and is a control, not prose. `test_the_committed_history_really_does_not_retain_a_grade`
reads the **committed** `data/history.json` and fails the build if a grade or score key appears.
I verified that independently: every key anywhere in the file is
`{events, fingerprint, first_seen, last_seen, observations}` plus the fingerprint's own eight
declaration fields, and an observation is `{date, up}`. **No grade, no score, no letter, anywhere.**
The page's claim that it cannot report grade movement is honest and grounded. (The test iterates
top-level keys only, so a grade added inside `fingerprint` would slip past; cheap to make recursive.)

Month derivation is `date[:7]` string slicing with no `datetime` and no `now()` anywhere in the
module, so lexicographic sort handles December-to-January by construction. The fixture straddles a
month boundary. Determinism is pinned by a byte-for-byte regeneration test.

**The caveat: months are derived from observations only, and observations are the one thing that
gets evicted.** The month list comes from `record.observations`, but `entered` comes from
`first_seen`, and changes and returns come from the event log. `drift.py` bounds observations at 120
and never advances `first_seen`. After roughly 120 daily runs every endpoint's `first_seen` falls
permanently outside the retained window, and "Entered the record" renders "No endpoint entered the
record this month" in every section forever, with nothing on the page saying so. A change dated in an
evicted month vanishes from this report while `/history/` still shows it - and the page points the
reader at `/history/` as "the evidence these counts are drawn from". Not live today (max 2
observations per entry, one month, zero orphans), but it is the same class of silent loss that #61
handles correctly with its `missing` list.

Two smaller ones: the most recent month may be still in progress and the page never says so; and
**`ROADMAP.md` Phase 12 still promises "which endpoints changed grade", "what the graded population
looked like at the start of the window" and "what share of the registry was observable throughout"** -
none of which ship, and the first of which the page now explicitly refuses. The Phase 5 checklist item
was updated; the Phase 12 section was not.

### #61 - dataset snapshots

Correct with caveats; inherits #57 and #59. **This is the branch to land, once the inherited defects
are fixed**, because it contains all seven features.

**The hazard I most expected is genuinely closed.** Manifest verification is bidirectional: forward
(every manifest entry has a file of the right size and digest) and reverse (every file on disk is
named by the manifest, via `on_disk - set(recorded)`). The trivial-verify trap is closed too - an
empty manifest is explicitly not a pass. The corruption tests are real: flipped bit, truncation,
deleted file, smuggled extra file, missing manifest, unparseable manifest, and one test that computes
a digest with `hashlib` directly rather than through the module.

The honesty claim holds. The repo emits nothing signature-shaped: no `signed_by`, no attestation
block, no `.sig`. The stated reason checks out - `.github/allowed_signers` really exists with a real
`ssh-ed25519` principal, and `release.yml` really is `workflow_dispatch`-only and really does verify
the tag signature against it. And the docs do **not** overclaim: they never say "tamper-evident" and
`verify` is scoped in words to "every way `snapshot` disagrees with its own manifest".

Caveats:

- The reverse check excludes on **basename** (`p.name != MANIFEST_NAME`), so a file at
  `api/endpoint/manifest.json` would evade extra-file detection. Practically unreachable; one-line fix.
- `DATASET_FILES` is a hardcoded tuple, and the only test that walks it iterates the tuple itself, so
  it is tautological. The list is **complete today** (audited against everything the build writes),
  but a future `api/coverage.json` would be silently omitted with no test noticing. The missing
  control is "every non-page machine-readable file in the built site appears in the manifest".
- `--date` is unvalidated; a snapshot can be dated `"yesterday"`.
- Reproducibility is "byte-identical when rebuilt **from one built site**", which is what the module
  and README say correctly. `ROADMAP.md` Phase 13's "regenerable byte-for-byte from committed inputs"
  is looser than what holds, because `generated_at` is a wall-clock string embedded in every
  dataset file, so hashes move on every publish even when no endpoint data moved.

## Regeneration steps

**None required.** Explicitly checked:

- `git diff --name-only feat/coverage-tracker-page feat/dataset-snapshots -- .github/ data/` is empty.
- `snapshot` and `verify-snapshot` are on-demand CLI subcommands. **No workflow calls them** - I
  grepped `.github/` on the #61 tip and found nothing. No first snapshot needs creating, and no
  committed manifest or fixture goes stale.
- `/history/`, `/availability/`, `/coverage/` and `/over-time/` are all computed at publish time
  inside `_write_site`. Nothing new is persisted.
- The only `pages.yml` change in the whole stack is #55's, and #56 to #61 leave it untouched.

The one thing to know: **the site is published on a schedule, not on merge.** The new pages appear at
the next daily run (17:14 UTC) or on a manual `workflow_dispatch`. Worth dispatching one deliberately
after the merge and watching it, because the audit step gates the deploy and the live build is far
larger than any build the suite exercises.

On that risk specifically: I checked whether the new pages are ever gated against realistic data, and
they are. `test_the_coverage_page_satisfies_every_gate` builds with `--registry data/registry.json`
(the live 45-endpoint registry) plus cohorts and asserts `audit_site`, `audit_accessibility` and
`audit_weight` all return `[]`. #55's own positive controls use only the thin 3-endpoint fixture build
with no cohorts, so at #55 alone the gate coverage is thin; by #59 it covers the real registry.

I also checked the opposite hazard - that a later PR quietly loosened an earlier one's gate to fit new
pages. **It did not.** `accessibility.py`, `weight.py`, `audit.py` and `pages.yml` are untouched by
#56 through #61, `MAX_PAGE_BYTES` and `MAX_SHARED_SUBRESOURCE_BYTES` are byte-identical at #55 and
#61, and `MIN_OBSERVATIONS_TO_REPORT` is 14 on `main` and on every branch in the stack.

## Safe order of operations

The stack cannot be landed as it stands: #57 and #59 carry blocking defects, and because the branches
are cumulative, everything downstream carries them too.

Sequential merging is the wrong shape here - six manual conflict resolutions in `CHANGELOG.md` and
`cli.py`, for a series where the last branch already contains the first six. Fix forward on the tip
instead.

1. **Merge #52 and #51, back to back**, in either order. Nothing depends on them and they are
   independent of the feature stack. `main` is red on CodeQL between the two merges; that is expected
   and self-resolving.
2. **Apply the `dependabot.yml` grouping fix** (see below) so the split bump stops recurring.
3. **Fix three things on `feat/dataset-snapshots` (#61):**
   - the #57 empty-state predicate - `record.answered` rather than `record.observations`, plus a third
     sentence for "probed, never answered". One line and one string.
   - the #59 member lookup key - `(state, roster_name)` rather than `roster_name`. One expression.
   - the #55 WCAG relabelling - two rules moved from "criterion" to "this project's rule", and the
     "twelve, each naming a criterion" sentence corrected in five files.
4. **Merge #61 alone.** It contains all seven features. One merge, one CI cycle, no conflict
   resolution.
5. **Close #55, #56, #57, #58, #59 and #60 by hand**, noting they landed via #61. They will **not**
   auto-close - their commits are rebased copies and are not ancestors of the result - and each
   will keep showing its full diff against an unchanged merge base, and will report a merge
   conflict, neither of which reads as "already merged".
6. **Dispatch `pages.yml` manually** and watch the audit step, rather than waiting for 17:14 UTC.
7. **Close issue #49**, which is already fixed (below).

If you would rather review in smaller pieces than one 4,448-line merge, the only clean prefix is
**#55 (after its relabelling) then #56**. Everything from #57 on needs the fixes first. Merging #55
then #56 sequentially costs one trivial conflict resolution.

## Issue #49 - already fixed, should be closed

The issue reports that `collapse_by_vantage` and `reconcile` select a borrowed document with
`if p.capability`, a truthiness check that cannot tell "never retrieved" (`None`) from "retrieved and
empty" (`""`).

**This was fixed by PR #50, merged as `db25db7`, which is on `main` now.** Both call sites use
`is not None`, with comments explaining the exact distinction, and `cli._grade_from_probes` gates on
`consensus.capability is not None` rather than on the encoded body's truthiness - which is the fix
the issue itself suggested.

I ran the issue's own reproduction against `main`:

```
reconcile -> reachable=True capability=''
all-None  -> reachable=True capability=None
```

The empty document survives reconciliation and stays distinguishable from no document. No open PR
touches `vantage.py`, and none needs to. **Close it.**

## Defects on `main` that no open PR addresses

### 1. Dependabot's `pip` ecosystem has never produced a single PR

`.github/dependabot.yml` declares `package-ecosystem: "pip"` against a project whose lockfile is
`uv.lock`, whose `[project] dependencies` is empty, and whose entire dependency surface is a PEP 735
`[dependency-groups]` block - a format the `pip` ecosystem does not read. `make verify` opens with
`uv lock --check`, so a bump that did not also update `uv.lock` would fail anyway.

The evidence is not speculative: **all twelve dependabot PRs ever opened on this repository are
`github-actions`. Zero are `pip`**, across roughly three and a half weeks of a weekly schedule, during
which pytest, mypy, ruff, pip-audit and hypothesis all had releases. The README's Security row says
"Dependabot for pip and github-actions", which reads as coverage of both halves; the half covering
what the README itself calls "the whole dependency surface" is inert.

**I have not applied this fix.** The correct ecosystem for a `uv.lock` project is `uv`, but I am
offline and cannot check GitHub's current list of supported `package-ecosystem` values against
published documentation, and guessing at an identifier is exactly what this repository's own
conventions forbid. Confirm the identifier, then change `pip` to it.

### 2. Split codeql-action bumps are guaranteed to be born red - **fixed in the working tree**

Described under #51/#52 above. Dependabot opens `init` and `analyze` as separate PRs; CodeQL requires
them to be the same version; so every codeql-action release produces two red PRs and a window where
`main` itself is red. It has already happened once, at 4.37.6 to 4.37.7.

I added a `groups:` block to `.github/dependabot.yml` so the two bump together in one PR. Grouped
updates are long-established dependabot syntax, so unlike the `uv` question above this one carries no
guesswork.

### 3. The README says branch protection is pending, and it is not - **fixed in the working tree**

The CI/CD row reads: "branch protection on `main` is pending (a live GitHub settings action left for
the repo owner)". Verified via the API: ruleset **`protect-main` exists, is `active`, targets
`refs/heads/main`, and requires the `verify` status check**. The row was stale, and no PR in the stack
touches it - #55 to #61 rewrite the Accessibility and Performance rows but leave this one alone.

Corrected to describe what is actually configured, including the fact that `verify` is the only
required check, which is *why* the two dependabot PRs are mergeable while red.
