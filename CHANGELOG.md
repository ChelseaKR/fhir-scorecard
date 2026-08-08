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
  deduplicated to **27 organizations**, seven of which run in both programs. **Eight publish a base
  URL that answers; 11 endpoints entered the registry.** The other nineteen are listed with the
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
- Portfolio standards conformance pass: security scanning workflow (CodeQL SAST plus a
  full-history gitleaks secret scan), Dependabot configuration, `uv.lock`, `.python-version`,
  pre-commit configuration, CODEOWNERS, ADR log (`docs/adr/`), i18n declaration
  (`docs/I18N.md`), responsible-tech audit record (`docs/RESPONSIBLE-TECH-AUDITS.md`), this
  CHANGELOG, and a Standards Conformance section in the README.

### Fixed

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
