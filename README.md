# fhir-scorecard

**A plain-language operational scorecard for publicly observable FHIR endpoints.**

CMS interoperability rules require regulated payers to stand up FHIR R4 APIs (Patient Access,
Provider Directory, and, under CMS-0057-F, more to come). Whether those endpoints are *actually
reachable, honestly documented, and interop-ready* is publicly observable today, and nobody grades
it in language a non-engineer can act on. This project does for FHIR endpoints what transit
data-quality tooling did for GTFS feeds: fetch what is public, run deterministic checks, and
publish a letter grade with a short, prioritized list of findings and spec citations.

## What it observes, and what it never touches

Everything graded here is **public, unauthenticated surface**:

- `[base]/metadata` , the FHIR CapabilityStatement every server must expose
- `[base]/.well-known/smart-configuration` , SMART on FHIR discovery

This project **never accesses patient data, never authenticates, and never probes beyond the
public discovery surface**. One request per resource per run, an identifying User-Agent with a
contact address, HTTPS only, and conservative timeouts.

## What it grades (v0.1)

| Dimension | What it asks |
|---|---|
| **Reachability** | Does `/metadata` answer, over HTTPS, with FHIR JSON, in reasonable time? |
| **Capability transparency** | Does the CapabilityStatement say what the server runs (FHIR version, software, resources, interactions), or is it boilerplate? |
| **Interop readiness** | Are US Core / CARIN profiles declared? Is SMART discovery present? Is OAuth security declared? |

Grades are deterministic and fail closed: an unreachable endpoint is an F with a reason, not a
gap in the data. Every finding carries a citation to the FHIR R4 or SMART App Launch spec.

## How this relates to Inferno and Lantern

- **Inferno** (ONC) is a conformance *test kit* run against a server you control, primarily for
  certification. This project runs no test suites and asserts no certification status; it grades
  the public operational surface of endpoints in the wild.
- **Lantern** (ONC) monitors FHIR endpoints of *certified EHRs* on the provider side. This
  project's target registry is the payer side, which has no equivalent public monitor.

Complementary, not competing. If you need conformance testing, use Inferno.

## Registry honesty

`data/registry.json` ships with a small set of **live-verified public reference servers** so the
tool is runnable out of the box. Payer endpoints are added only after live verification (fetch the
CapabilityStatement, confirm the publisher matches the claimed organization, record the method and
date in the entry). Unverified entries are never shipped. See `CONTRIBUTING.md`.

## The site

Every endpoint, organization, and category gets its own indexable page with a canonical URL,
description, and structured data, plus a sitemap and a methodology page that every finding links
into. See [ROADMAP.md](ROADMAP.md) for what a production public service still needs and, more
importantly, for the constraint that governs it: search traffic scales with registry size, and
registry size is gated on payers publishing base URLs.

## Use the data

| Artifact | What it is |
|---|---|
| `dataset.csv` | One row per endpoint, flat, with a documented schema |
| `dataset.schema.json` | Column names, types, and meanings |
| `api/index.json` | Every endpoint with links to its detail and its page |
| `api/endpoint/<id>.json` | Full scorecard: dimensions, findings, citations, drift |
| `scorecards.json` | The complete graded payload in one file |

A read-only MCP server exposes the same data to an assistant:

```bash
fhir-scorecard mcp --site site
```

It reads only the published dataset files. There is deliberately no tool that probes an endpoint:
a model deciding to fetch arbitrary URLs is a much larger security surface than one reading a
file this project already publishes. Its `grading_method` tool returns the documented limits, so
an assistant can be told what the numbers do not mean.

## Quick start

```bash
uv venv .venv && uv pip install -e '.[dev]'
make verify                       # lint, strict typecheck, tests with coverage floor
.venv/bin/fhir-scorecard grade --registry data/registry.json --out site/
```

Offline mode (no network, fixture-driven) exists for CI and demos:

```bash
.venv/bin/fhir-scorecard grade --offline --fixtures tests/fixtures --out site/
```

## Status

v0.1.0-dev. Seed registry contains reference servers only; payer registry curation is in
progress. Grades are observational snapshots of public surfaces, not audits, rankings of care
quality, or statements about any organization's compliance.

## Provenance

Personal open-source project, built on personal time and equipment, unaffiliated with any employer
or client, past or present. Built with AI assistance (Claude Code); every change passes the
`make verify` gate (ruff with security rules, mypy strict, pytest with a branch-coverage floor).

License: Apache-2.0.
