# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The site and dataset at
<https://chelseakr.github.io/fhir-scorecard/> are still republished daily from `main` and are
not versioned by these entries; what a version names is the composite GitHub Action and the
distribution behind it, which consumers pin by tag
(see `docs/adr/0002-release-versioning-applies-action-export.md`).

## [Unreleased]

Merged changes land here until the next tag.

### Fixed

- **Interop readiness no longer describes a document it could not read.** When a server answers
  `/metadata` with HTTP 200 and a body that is not a CapabilityStatement — an `OperationOutcome`,
  a sign-in page from an authenticating gateway, a search `Bundle`, an empty body — the two checks
  that read a CapabilityStatement (I1 profiles, I3 declared security) used to run anyway, against
  a parse result whose fields were dataclass defaults, and publish "no profile canonical declared
  in rest.resource.supportedProfile, rest.resource.profile, instantiates, imports, or meta.profile"
  and "no OAuth security service declared". I1's message names five elements as checked; none of
  them existed to check. Both rendered on the endpoint page beside a spec citation under a named
  organization, which is the exact shape of claim the project forbids itself and had already
  removed once, for unreachable endpoints, in the v0.1.0 line. `grade_transparency` had always
  handled the same input honestly with a single T0 finding; `grade_interop` now does the same with
  **I0**, which carries exactly the points I1 and the applicable I3 would have carried. **No
  dimension score and no letter changes**, in either direction, for any input: this corrects what
  the site says, never what it scores. SMART discovery is a separate retrieval and is still graded
  on its own evidence, so an endpoint whose CapabilityStatement is unreadable still gets credit for
  a SMART document it does publish. No endpoint in the current registry returns such a body, so
  nothing published today changes; the registry grows one payer developer portal at a time, and
  the shape is ordinary enough that it would have arrived.


- **One flapping endpoint no longer eats the drift log.** Measured on the published history at
  2026-08-19: 27 capability-change events across 30 endpoints, of which
  `la-care-provider-directory` held 8 - `software_version` moving between `5.4.1.13_edfx` and
  `5.4.1.11_edfx` and back, on 08-08 (twice), 08-11, 08-12, 08-13, 08-14, 08-15 and 08-16. That is
  one hostname in front of two backends, the same phenomenon as the "one URL, three brands"
  finding, and every event after the first carried no information while publishing "L.A. Care
  changed its declared capability" every other day. The event window holds 20, so roughly twelve
  more alternations would have evicted every genuine capability change this endpoint ever makes.
  Drift now distinguishes *advancing* to a declaration never served before, which is an event,
  from *returning* to one already on record, which is counted and dated once as an alternation and
  never appended again. `medplum`'s 13 real forward releases (5.1.27 through 5.1.30) are untouched,
  which is the test that matters: the distinction is returning versus advancing, not "changes a
  lot" versus "changes a little". A log written before the rule is rebuilt under it on its first
  run, by re-deriving the earlier fingerprints from the event lines themselves - each carries both
  sides of its transition - so la-care's 8 events become 1 event plus 7 counted returns without
  anything being invented. An event whose earlier state cannot be re-derived stops the walk and is
  left exactly as recorded.
- **A two-week-old registry check no longer reads as a fresh one.** `verification.date` was the
  only date an entry carried, so an endpoint curated once and never looked at again was
  indistinguishable on the page from one re-checked that morning. Re-checks are now their own
  dated record (`verification.reverified`), the curation date is preserved, and an entry with no
  re-check block says so in words on its page rather than by omission.
- **The probe contract is now enforced on redirects, not only on the first request.** `README.md`,
  `SECURITY.md`, `docs/RESPONSIBLE-TECH-AUDITS.md` and the site's own "what we do to your servers"
  panel all promise that this project never authenticates, never requests patient data, and never
  probes beyond `/metadata` and `/.well-known/smart-configuration`. Nothing enforced that.
  `fetch_json` built a stock `urllib.request.build_opener()`, whose `HTTPRedirectHandler` follows
  a server's `Location` wherever it points: a graded endpoint answering `/metadata` with
  `302 Location: /Patient?_count=50` would have had that request issued, its body read and parsed
  as a CapabilityStatement, fingerprinted into the availability history, and uploaded as a probe
  artifact. The same handler follows an `https` to `http` downgrade and copies every request
  header onto the plaintext hop, so "HTTPS is enforced before any connection is attempted (fail
  closed)" covered the first hop only. `DiscoveryRedirectHandler` now refuses any redirect target
  that is not one of the two discovery paths over HTTPS, and caps the hop budget at 3 rather than
  urllib's 10. A refusal is reported as a retrieval error with its reason, so the endpoint is
  published as `not observed` rather than graded on a document nobody should have fetched.
- **The landing page no longer contradicts itself about an endpoint that answered with nothing.**
  "Not observed" and "did not answer" are nearly the same set and are not the same set: an
  endpoint that answers `/metadata` with an empty body, or that a peer vantage reached without
  retrieving the document, is reachable and still has nothing to grade. The headline counted all
  ungraded endpoints as non-answering, so a single such endpoint produced the sentence "1 is how
  many answered /metadata ... 1 was not observed on this run and is not counted as answering",
  about itself, in one breath. The two reasons are now reported separately and the second says
  what actually happened.
- **`make lint`, `make format` and `make typecheck` now cover the code that ships.**
  `action/render_result.py` runs on a consumer's runner on every
  `uses: ChelseaKR/fhir-scorecard@<tag>`, and all three targets were scoped to `src` and `tests`,
  so they printed "All checks passed!" and "Success: no issues found" without opening it. Nothing
  was wrong with the file; the gates could not have said so either way.

### Added

- **T0 and I0 are defined on the methodology page.** Every finding code renders as a link to
  `/how-we-grade/#<code>`, and T0 has been emitted since v0.1 with no entry there, so it linked to
  an anchor that did not exist. Both are documented now, and a test drives grading over a matrix
  that reaches every branch and asserts that the set of codes the grader can emit is exactly the
  set the page defines — in both directions, so an undocumented code and a documented check that
  no longer runs each fail the build.

- **`docs/SAMPLING-FRAME.md`**: the rule that decides which organizations this project goes
  looking for, written down before the looking. Every cohort's membership comes from a public
  roster published by somebody else, retrieved on a stated date, and fixed before any base URL is
  probed; members that publish nothing are carried as exclusions; members that publish an address
  that does not work are a different finding from members that publish nothing. It also records
  what was considered and rejected as a roster, including the guessed-hostname method that
  produced 0 verified endpoints out of 18 probes and a hit rate that meant nothing.
- **A registry entry may now be listed on the organization's own publication of a base URL that
  did not answer.** `verification.basis` is `live_capability` (a conformance document was
  retrieved) or `publisher_documented` (the organization publishes this base URL in its own
  materials and the document was not retrievable on that date). The second requires `source` and
  `observed` - where the address was published and what the probe actually saw - and the loader
  refuses it without them. Such an endpoint is graded every day like any other and publishes as
  `not observed` rather than `F`. Listing it is the point: a registry that drops the endpoints
  that fail publishes a cohort pruned of exactly the failures this project exists to detect, and
  an entry that stays listed keeps being probed from every vantage and can be corrected, which an
  entry that was dropped cannot.
- `drift_alternations` on the endpoint API payload and the endpoint page, kept out of
  `drift_events` so a consumer counting capability changes counts changes. Dataset
  `schema_version` moves to 2, which also adds the `verification_basis` and `reverified_date`
  columns to `dataset.csv`.
- `tests/test_alternation.py`: the la-care and medplum event logs reproduced verbatim from the
  published history, because the rule has to tell them apart and the only convincing evidence that
  it does is the data that motivated it.
- `tests/test_probe_contract.py`: the never-authenticate, never-touch-patient-data,
  never-leave-the-two-paths promise as tests that can fail. Two of them run the real fetcher
  against a loopback HTTP server that records which requests actually arrived, because an
  assertion about a request that was *not* made is worth little unless something was listening.
  One test pins the stock-opener behaviour that was replaced, so the delta stays documented.
- `tests/test_shipped_code_is_gated.py`: reads the Makefile, `pyproject.toml` and the pre-commit
  config and fails if any Python file in the archive a consumer downloads falls outside them.

## [0.1.0] - 2026-08-16

First release. The previously-untagged `[0.1.0]` section dated 2026-08-05 and everything
since have been folded together here: the milestone tag of that date was local, was never
pushed, and produced no artifact, so `0.1.0` had never named anything a consumer could
obtain. This is the first tag that does.

### Added

- Deterministic grading of public FHIR discovery surfaces (`/metadata` and
  `/.well-known/smart-configuration`) with letter grades, spec-cited findings, and fail-closed
  handling of unreachable endpoints.
- Curated, live-verified endpoint registry spanning payers, providers, EHR vendors, and
  reference servers, with a rejected-candidates log and a quarterly recheck workflow.
- Indexable site pages per endpoint, organization, and category, plus a methodology page,
  sitemap, and structured data.
- Dataset exports (`dataset.csv`, `dataset.schema.json`), a static JSON API, and a complete
  `scorecards.json` payload.
- Read-only MCP server over the published dataset files.
- Multi-vantage probing with cross-vantage reconciliation, capability drift tracking, and
  daily availability history.
- CI verify gate (ruff with security rules, mypy strict, pytest with a branch-coverage floor)
  and daily grade-and-publish workflow to GitHub Pages.
- **A single-endpoint check and the composite GitHub Action that packages it** (`fhir-scorecard
  check`, `gate.py`, `action.yml`, `action/render_result.py`,
  [docs/ci-action.md](docs/ci-action.md)). It grades one FHIR base URL from its own two public
  discovery documents and, when the caller sets `--min-grade`, exits non-zero below it. The
  intended user is the operator of the endpoint being checked, gating their own build; the
  command publishes nothing, opens no history, renders no page, and reads nothing under `data/`.
  Registry-free on purpose: every listed endpoint carries a record of how and when it was
  verified and who it may be attributed to, a one-off check has no such record to make, and
  synthesizing one would put a verification claim in the artifact that nobody performed. So the
  result names the host the caller supplied and nothing else, and it claims no availability,
  because one observation is not a record of one.
- **Exit codes, documented for the first time and pinned by a test.** `0` ran and met every
  threshold the caller set, `1` a threshold the caller set was not met, `2` input error. `1` is
  new to this project and is reachable only through a threshold somebody asked for: a finding
  about a document remains data everywhere else. An endpoint no vantage reached stays
  `not observed` in the artifact, the Action outputs, the job summary, and the annotation, and
  fails a set threshold on the stated ground that the threshold could not be evaluated rather
  than by being scored an `F`.
- **Two gates on the Action itself.** `tests/test_ci_action.py` drives a deliberately bad input
  through the same code path the Action calls and asserts the non-zero exit, then drives the
  Action's own shell contract against a host reserved never to resolve and asserts that the
  result renderer still runs and the step still exits `1`. `test_workflow_shell_safety.py` now
  scans `action.yml` alongside the workflows, because its shell block runs on a consumer's
  runner, which is the one place a missing `pipefail` would fail quietly in someone else's
  build. A `.gitattributes` export bound keeps the archive a consumer downloads on every
  `uses:` to the Action runtime rather than the curated registry, cohorts, and fixtures.

- **Curated cohorts** (`cohort.py`, `data/cohorts/`), a named view over the registry whose
  membership comes from a public roster rather than from whatever was easy to find. Each member
  either points at registry endpoints or carries an exclusion with a reason, a review record, a
  date, and a source; the loader refuses a member that carries neither or both, and refuses any
  endpoint reference the registry does not stand behind. A cohort file that fails validation fails
  the build rather than publishing a membership list with members quietly missing.
- **The California payer cohort** at `/california/`, the first one. Membership is the DHCS Medi-Cal
  managed care plan roster plus the Covered California qualified health plan issuer list,
  deduplicated to **27 organizations**, seven of which run in both programs. **Eight published a
  base URL this project verified from their own documentation, on the dates recorded in the
  registry; 11 endpoints entered it.** How many of those answer on a given day is a separate,
  measured number, published beside the curated one. The other nineteen are listed with the
  reason they could not be, because for a cohort whose membership is public and finite, the gap is
  the finding. Every endpoint here is required by the federal CMS Interoperability and Patient
  Access rule (CMS-9115-F); the page says so, and says equally plainly that publishing a base URL
  where an unregistered visitor can read it is not required by that rule, that California's Data
  Exchange Framework requires none of these surfaces, and that CMS-0057-F is not in force until
  2027 and is not graded.
- **Eleven California payer endpoints** in the registry (19 → 30): Patient Access and Provider
  Directory APIs for Inland Empire Health Plan, Sharp Health Plan and Santa Clara Family Health
  Plan; Patient Access for Kaiser Permanente's health plan and Health Plan of San Mateo; Provider
  Directory for L.A. Care, Central California Alliance for Health, and Community Health Group.
- **The attribution rule gets a second leg, stated rather than assumed.** Three of the eleven answer
  with a document that names nobody: two report `publisher: Not provided` on a vendor-controlled
  host, and Community Health Group's serves the Da Vinci Plan-Net implementation guide's own
  CapabilityStatement verbatim, publisher `HL7 Financial Management Working Group`. They are listed
  because the **plan's own site** publishes the base URL, which is the organization putting its name
  behind the address. That is different from the Opala case, where only the vendor connected server
  to plan, and it is still not attribution from a URL path segment, which remains forbidden. Each
  verification record says outright that the conformance document does not name the plan.
- **A finding that makes the multi-tenant problem worse than previously recorded.** Elevance
  publishes one production base URL per brand. Three consecutive fetches of the Anthem Blue Cross
  URL returned documents identical apart from `id` and `date` whose `implementation.url` named
  `AnthemBlueCrossBlueShield`, then `AnthemBlueCross`, then `Wellpoint`. `implementation.url` was
  the one field that could distinguish tenants on a multi-tenant platform; on this platform it is
  not stable across requests to a fixed URL, so it distinguishes nothing. The endpoint answered and
  is deliberately **not listed**.
- **Seven newly documented rejections** in `data/rejected.json`, so the quarterly re-probe covers
  them: the Centene corporate host that three California plans document and that does not resolve,
  Partnership HealthPlan's two production URLs (404 and 401), Kern Family Health Care's two (404),
  and Health Plan of San Joaquin's (401). These are publisher-documented addresses that do not
  work, which is a different finding from publishing nothing.
- **`docs/findings/`, and the first two write-ups in it.**
  `2026-08-15-california-payer-cohort.md` publishes what the California curation review of
  2026-08-07 found: 8 of 27 organizations listed, 19 not, and the 19 split four ways rather
  than counted as one number, because "publishes nothing" and "publishes a URL that returns
  404" are different results. `2026-08-15-anthem-multi-tenant-attribution.md` writes up the
  three-brands-one-URL observation on its own, as a limit on what any outside observer can
  establish about a multi-tenant payer platform. Both refuse a compliance reading explicitly.
- **The outcome classification the write-up counts from**
  (`docs/findings/2026-08-15-california-payer-cohort.json`). The cohort file records how far a
  review went (`basis`) and what it found (`reason`, free text), and free text cannot be
  counted; `ROADMAP.md` phase 5 requires the "documented but unreachable" and "no public URL
  found" populations to be counted separately and never merged. Each record carries the
  verbatim clause of the committed reason that decided its outcome. Assigned by reading what
  was already committed: no endpoint was probed and no review record was changed.
- **`tests/test_findings_evidence.py`**, which recomputes every published figure from
  `data/cohorts/california.json`, `data/registry.json` and the classification, so a number in a
  findings document cannot drift from its evidence. It also fails the build if a write-up names
  an organization that is not on the roster, if it claims more probing independence than three
  runners on one provider's network support, or if it uses a regulator's word.
- Portfolio standards conformance pass: security scanning workflow (CodeQL SAST plus a
  full-history gitleaks secret scan), Dependabot configuration, `uv.lock`, `.python-version`,
  pre-commit configuration, CODEOWNERS, ADR log (`docs/adr/`), i18n declaration
  (`docs/I18N.md`), responsible-tech audit record (`docs/RESPONSIBLE-TECH-AUDITS.md`), this
  CHANGELOG, and a Standards Conformance section in the README.
- **A hardened release pipeline, because exporting the Action made this repository a
  dependency** (`.github/workflows/release.yml`, `.github/allowed_signers`). It is
  `workflow_dispatch` against an already-existing tag, never a trigger on tag push: pushing a
  tag is not a review. The shared `ChelseaKR/.github` authorize workflow verifies the tag is
  annotated, stable SemVer, SSH-signed by a principal listed in-tree, and on a commit that is an
  ancestor of `main`; `make verify` and the full-history gitleaks scan then re-run at that
  commit rather than the pull request's earlier checkmark being taken as evidence; every job
  asserts that the tree it checked out is that commit before it does anything with it, so the
  release path cannot test one tree and ship another; the build is
  cache-free, carries a SLSA provenance attestation, and is attached to a GitHub Release whose
  notes are this file's matching section. Tag, `pyproject.toml` and `CHANGELOG.md` versions must
  agree or the release fails, and three tests now catch that disagreement on the pull request
  instead of on the release run.

### Fixed

- **Three named AUTO gates were documented and never ran** (#26).
  - **CQ-04, formatting.** `make lint` was `ruff check src tests` and nothing else, so
    `ruff format --check` never ran. `make format` runs it now and is part of `make verify`.
    Turning it on reformatted 28 of 31 files, and it needed the standard's own `ignore = ["E501"]`,
    because `ruff format` cannot split a string literal and four of them land past column 100.
    The select set is now the standard's pinned list exactly, which added `W` and `SIM`; both
    were already clean.
  - **CQ-11, dependency audit.** Nothing checked this dependency set for known vulnerabilities,
    anywhere. `make audit` exports the locked set and runs `pip-audit --strict` over it, and
    `security.yml` runs the same target on push, PR and the weekly schedule. `--strict` so a
    dependency that could not be audited fails rather than being skipped past. No `|| true`.
    First run: no known vulnerabilities.
  - **CQ-27**, dev dependencies moved from `[project.optional-dependencies]` to a PEP 735
    `[dependency-groups]` group, so the linters and type-checker cannot ship as an extra.
- **CQ-09: the committed lockfile was decorative, and the standard's own remedy would have left
  it that way** (#26). `verify.yml` installed with `pip install -e '.[dev]'`, which re-resolves
  from PyPI and ignores `uv.lock` entirely, so the lockfile could drift indefinitely and the
  toolchain gating a merge was never the one the lockfile described. CI now installs with
  `make sync`.
  - That target runs **`uv sync --locked`, not `uv sync --frozen`**, and `make verify` now
    opens with a `lock-check` target running `uv lock --check --offline`. The control text says
    `--frozen` and calls it a lockfile-drift check; measured here on uv 0.12.1, it is not one.
    Against a deliberately drifted `pyproject.toml`: `uv lock --check --offline` exits 1,
    `uv sync --locked` exits 1, and `uv sync --frozen` exits **0** having installed the stale
    set, because `--frozen` means install from the lock without consulting the manifest. A
    drift check that passes on a drifted lock is not a check.
  - `lock-check` is deliberately the **first** prerequisite of `verify`: a later target that
    resolved dependencies would repair the lockfile it was meant to be checked against and
    then pass. It writes nothing and reaches no network. Nothing in this repository invokes a
    bare `uv run`, which performs exactly that implicit repair.
- **The quarterly re-probe reported "nothing revived" for every way it could fail.**
  `recheck.yml` ran `fhir-scorecard recheck | tee report.txt` under the default `bash -e`
  shell, which sets `-e` but not `pipefail`, so the pipeline's exit status was tee's and was
  always 0. A crashed re-probe left a truncated `report.txt`, `grep -q "NOW ANSWERS"` found
  nothing in it, the step set `revived=false`, and the workflow went green having checked
  nothing. `recheck` returns 2 when it cannot load `data/rejected.json`, and that 2 was
  discarded the same way: a malformed candidate file would have reported no revivals every
  quarter, indefinitely, which is precisely the rot the workflow's own comment says it exists
  to prevent. Every multi-line `run:` block now declares `shell: bash` and opens with
  `set -euo pipefail`, including the gitleaks step, whose checksum verification is also a
  pipeline.
  - `tests/test_workflow_shell_safety.py` fails the build if a `run: |` block omits either.
    It also asserts it found workflows and blocks to check, because a scan over nothing
    passes trivially and that is the same class of bug.
- **The README's offline command named a directory that did not exist** (#6). `tests/fixtures`
  was in the Quick start and in no commit, so `--offline --fixtures tests/fixtures` failed to load
  a fixture for every endpoint, exited 0, wrote a complete site in which every named organization
  was ungraded, and appended a `{"up": false}` observation for all thirty of them to the real
  `data/history.json`, on a date that had none.
  - **The fixtures exist now**: real `/metadata` and SMART documents captured 2026-08-14 from CMS
    Blue Button 2.0, the ONC Inferno reference server, and the Oracle Health open sandbox, with a
    `tests/fixtures/README.md` recording where each came from, when, what it exercises, and how to
    refresh it. They are the parser's first test against documents real servers publish rather
    than against hand-written ones, and Oracle's missing SMART document is part of the capture:
    the live server answers 404 there, so I2 fails offline exactly as it does in production.
    `tests/fixtures/registry.json` lists those three, so the documented command grades a complete
    registry rather than a registry of missing files.
  - **`--offline` chooses scratch paths.** Without an explicit `--history` it writes to
    `.cache/offline-history.json`, and without an explicit `--cohorts` it loads none, because the
    shipped cohorts reference registry ids a fixture registry does not carry.
  - **The mode guard can fire on the committed file.** `ensure_mode()` refuses to mix fixture and
    live observations in one history file, and `data/history.json` now carries the `_meta` stamp it
    compares against, which a file predating the guard could not. There is deliberately no
    override: the fix is a scratch path, which is now the default.
- **I1 said "no recognized interoperability profiles declared" after reading one element** (#5).
  `capability.py` collected profile strings from `rest[].resource[].supportedProfile` and nothing
  else, and the failure message was an absolute claim worth 40 of 100 interop points. It now reads
  every element R4 gives a server to declare conformance in — `rest.resource.supportedProfile`,
  `rest.resource.profile` (including an STU3-shaped `{"reference": ...}`), `instantiates`,
  `imports`, and `meta.profile` — and the message names the element a declaration was found in, or
  names all five when none carries one. Where declarations exist but none is US Core, CARIN or Da
  Vinci, it says how many were found and where, rather than that none exist.
  - Measured while writing this (one `/metadata` request each, 2026-08-14): **CMS Blue Button 2.0
    declares `rest.resource.profile` on all three of its resources**, an element the parser had
    never read. The values are base FHIR StructureDefinitions, so its I1 stays negative — but it
    is now a negative that was earned by looking. **Aetna** declares no profile canonical anywhere,
    which the old message was right about and overstated.
  - New **I4**, worth zero points in either direction and shown only when I1 finds no declaration:
    when `implementation.description`, `title` or `name` names US Core, CARIN or Da Vinci in prose,
    the card says so and says that adding `rest.resource.supportedProfile` entries would make the
    claim machine-readable. `implementation_description` was parsed on every run and read by
    nothing; it is the field Aetna's own registry entry was verified from.
  - Findings worth no points now render as neutral notes rather than as a ✓ or a ✗, which also
    fixes the Provider Directory "not applicable" findings reading as passes.
  - The drift fingerprint still counts `supportedProfile` alone, so widening what I1 reads does not
    report a capability change no server made.
- **Headline numbers now count what their words say they count** (#4). "Answering" was doing two
  jobs: on the cohort page it came from the curation files (a count of rows somebody wrote down,
  in the present tense, on a page regenerated daily), and the endpoint count it sat beside came
  from the registry. Every published headline now says which kind of number it is:
  - The landing page reads **"N endpoints listed"** and **"N answered on this run"**, with a
    sentence underneath saying exactly that one is a registry count and the other is a probe
    result, plus how many endpoints were not observed and therefore not counted as answering.
  - The cohort page publishes four figures rather than three: organizations reviewed, those that
    published a base URL this project could verify (a dated curation record, said in those words),
    endpoints listed, and **how many of those answered on the run that generated the page**. The
    listed count is now derived from the graded cards rather than from ids in the curation file,
    so it can never exceed the rows in the table beneath it.
  - `api/index.json` carries `endpoints_listed` and `answered_on_this_run` beside the existing
    `count`, so the distinction survives into the dataset a citation would use.
- **An unreachable endpoint published four findings about what the payer had not published**
  (#2). `capital-bluecross` on 2026-08-05 is the measured case: R1 named the cause as ours
  ("likely a vantage-local interception, not an endpoint fault") and the four findings beneath it
  said the CapabilityStatement was unparseable, that no interoperability profiles were declared,
  that SMART discovery was absent, and that no OAuth security service was declared, each with a
  spec citation. `data/history.json` in this repository recorded 37 declared profiles, 28 resource
  types and OAuth declared for that endpoint on that date. All four were false, produced by
  `parse_capability(b"")` being read downstream as a fact about the server rather than as no data.
  - `CapabilityFacts` and `SmartFacts` now carry `observed`, which separates a document that
    arrived and could not be parsed (an observation of the endpoint) from a document that was
    never retrieved (an observation of nothing). Only the first is graded.
  - A dimension nobody could observe carries `score=None`, not 0, and exactly one neutral finding
    (`NR`) that says nothing was retrieved. The site draws no bar, the CSV leaves the cell empty,
    and `/how-we-grade/` documents `NR`.
- **`F` meant two opposite things, and the site rendered both with one sentence about a network**
  (#2). `letter()` returned `F` for an endpoint nobody could reach and for a reachable endpoint
  scoring below 60, and every `F` on the site read "could not be reached from this vantage point"
  — which was a sentence about this project's network printed under a card whose own reachability
  score was 100. Now: an unreachable or undocumented run publishes the status **`not observed`**,
  with a sentence keyed on what actually happened, and `F` publishes "answers publicly, and what
  it declares falls short across the graded checks". The badge for a not-observed endpoint says
  so instead of stamping an F on it, `dataset.schema.json` documents the new value and says which
  of the two is a statement about the endpoint, and the MCP `grading_method` tool tells an
  assistant not to characterize a not-observed record as a low grade.
- **One vantage was counted twice, and three runner images were described as several networks**
  (#3). The publishing job made its own live probe under `github-actions/ubuntu-latest` and then
  merged the artifact written under the same label, and `reconcile()` did not dedupe: every card
  published "reachable from all 4 vantage(s)" on days when three vantages reported, R2 called it a
  median across four, and ubuntu's latency carried double weight in a median whose bands are 3s
  and 8s. Three fixes, none of which is a deletion of the claim:
  - `collapse_by_vantage()` reduces duplicate labels to one observation before anything is counted
    or averaged, so a vantage counts once whatever the merge contains.
  - `Consensus` now carries `networks` alongside `vantages`, derived from the `<network>/<host>`
    label convention, and every published sentence uses it. What CI has is three GitHub-hosted
    runner images on one provider's network: three hosts, one network. The site, README, SECURITY
    and the responsible-tech record now say that in those words, and a run where every vantage
    failed publishes "not reached from that network on that day, and this run cannot separate that
    from an endpoint being down" instead of "unreachable from all 3 vantage(s)". A genuinely
    independent vantage is named in ROADMAP as what would let the wording change.
  - `grade --from-probes` grades the documents the probing runs retrieved and makes no request of
    its own, which removes the duplicate at its source and takes a quarter off every endpoint's
    daily request count.
- **The published request budget understated a working day by more than an order of magnitude**
  (#3). "At most two unauthenticated GET requests per run" was true per run and read as a daily
  promise to payer operations staff; the publish workflow also triggered on every push to `main`,
  and on 2026-08-05 it ran fifteen times, so each registry endpoint took roughly 48 requests that
  day. Publishing is now scheduled and manual only, the publishing run probes nothing, and
  `/claim/`, the README and SECURITY.md state the per-day figure a payer's ops team would actually
  measure: at most six requests per endpoint on a scheduled day.
- **Organization pages were named after whichever endpoint came first**, so "Cigna Patient Access
  API" headed a page that also lists Cigna's provider directory. The heading is now the leading
  words all of the group's endpoints share. Latent until now; the California cohort tripled the
  number of organizations with more than one surface, which made it visible. URLs and slugs are
  unchanged and a test pins that.
- **Tests ran from the repository root, so relative CLI defaults reached the real curation files.**
  An offline run against a fixture registry loaded the shipped cohort and failed on endpoint ids
  the fixture registry does not have, and a run without `--history` would have appended fixture
  observations to the live availability record. Every test now runs from a throwaway directory.

### Changed

- **Release & Versioning went from N/A to applies, and ADR 0001 is superseded by
  [ADR 0002](docs/adr/0002-release-versioning-applies-action-export.md).** ADR 0001 declared the
  standard N/A on the stated ground that, among other things, "no reusable action is exported."
  Merging the composite Action made that clause false the same day, and ADR 0001's own
  "Revisit if" section had already named the consequence. A consumer's `uses:
  ChelseaKR/fhir-scorecard@<ref>` downloads this repository's tree and `pip install`s it into
  their build, which is a version-pinned dependency by construction. ADR 0001 is marked
  superseded rather than edited: it was correct on the facts it had. The README's Standards
  Conformance row and `docs/ci-action.md`, which told consumers no release tag existed, follow.
- A first probe of Kern Family Health Care's endpoints failed at TLS and was nearly logged as an
  endpoint fault, which is the Capital Blue Cross error of 2026-08-05 in a new costume. It was this
  vantage: the Python trust store here lacks the Sectigo root that server chains to. Re-probed with
  a client that carries the root, the endpoint returns a plain 404, which is what the candidate log
  and `rejected.json` record. The 404 is the endpoint's; the TLS failure was ours.
- Raised dev tooling floors: `ruff>=0.15` (was `>=0.6`) and `mypy>=1.18` (was `>=1.11`). The
  `make verify` gate passes unchanged under ruff 0.16.2 and mypy 2.3.0.
