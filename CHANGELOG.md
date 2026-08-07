# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Because the shipped artifact is a
continuously rebuilt site and dataset (see `docs/adr/0001-release-versioning-na.md`), entries
here are dated records of change, not version-tagged release notes.

## [Unreleased]

### Added

- Portfolio standards conformance pass: security scanning workflow (CodeQL SAST plus a
  full-history gitleaks secret scan), Dependabot configuration, `uv.lock`, `.python-version`,
  pre-commit configuration, CODEOWNERS, ADR log (`docs/adr/`), i18n declaration
  (`docs/I18N.md`), responsible-tech audit record (`docs/RESPONSIBLE-TECH-AUDITS.md`), this
  CHANGELOG, and a Standards Conformance section in the README.

### Changed

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
