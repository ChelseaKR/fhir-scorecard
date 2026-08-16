# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Because the shipped artifact is a
continuously rebuilt site and dataset (see `docs/adr/0001-release-versioning-na.md`), entries
here are dated records of change, not version-tagged release notes.

## [Unreleased]

### Added

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

- A first probe of Kern Family Health Care's endpoints failed at TLS and was nearly logged as an
  endpoint fault, which is the Capital Blue Cross error of 2026-08-05 in a new costume. It was this
  vantage: the Python trust store here lacks the Sectigo root that server chains to. Re-probed with
  a client that carries the root, the endpoint returns a plain 404, which is what the candidate log
  and `rejected.json` record. The 404 is the endpoint's; the TLS failure was ours.
- Raised dev tooling floors: `ruff>=0.15` (was `>=0.6`) and `mypy>=1.18` (was `>=1.11`). The
  `make verify` gate passes unchanged under ruff 0.16.2 and mypy 2.3.0.

## [0.1.0] - 2026-08-05

Milestone tag `v0.1.0` (local history marker; no published release artifact).

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
