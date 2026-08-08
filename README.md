# fhir-scorecard

**A plain-language operational scorecard for publicly observable FHIR endpoints.**

CMS interoperability rules require regulated payers to stand up FHIR R4 APIs (Patient Access,
Provider Directory, and, under CMS-0057-F, more to come). Whether those endpoints are *actually
reachable, honestly documented, and interop-ready* is publicly observable today, and nobody grades
it in language a non-engineer can act on. This project does for FHIR endpoints what transit
data-quality tooling did for GTFS feeds: fetch what is public, run deterministic checks, and
publish a letter grade with a short, prioritized list of findings and spec citations.

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

Attribution follows the publisher's own words and never a URL path segment. Where a vendor runs a
multi-tenant payer platform the conformance document often names the vendor, or nobody at all, and
attribution then rests on the **plan** publishing the base URL on its own site, which the entry's
verification record says outright. Where only a vendor or a path segment connects a server to a
plan, the endpoint is excluded instead.

## Cohorts

A cohort is a named view over the registry whose membership comes from a **public roster** rather
than from whatever was easy to find, which is what lets a hit rate mean anything: the denominator is
fixed before any probing starts. Every member either points at registry endpoints or carries an
exclusion with a reason, a review record, a date, and a source, so the plans that publish nothing
discoverable are part of the published result rather than an absence in it.

The first is the **California payer cohort** at `/california/`: the Medi-Cal managed care plans DHCS
lists plus the Covered California qualified health plan issuers, deduplicated to 27 organizations.
Eight publish a base URL that answers. Those endpoints are required to exist by the federal CMS
Interoperability and Patient Access rule (CMS-9115-F), which is the only obligation this project
claims about them. The rule does not require a plan to print its base URL where an unregistered
visitor can read it; California's Data Exchange Framework runs through the DSA and QHIOs and
requires none of these surfaces; and CMS-0057-F's additional APIs are not in force until 2027 and
are not graded.

## The site

Every endpoint, organization, category, and cohort gets its own indexable page with a canonical URL,
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

## Status

v0.1.0-dev. Thirty verified endpoints across payers, payer provider directories, a federal provider
API, EHR vendor sandboxes, and reference servers, including a curated California payer cohort. Payer
registry curation continues one developer portal at a time, because payer base URLs are not
predictable from company names. Grades are observational snapshots of public surfaces, not audits,
rankings of care quality, or statements about any organization's compliance.

## Provenance

Personal open-source project, built on personal time and equipment, unaffiliated with any employer
or client, past or present. Built with AI assistance (Claude Code); every change passes the
`make verify` gate (ruff with security rules, mypy strict, pytest with a branch-coverage floor).

License: Apache-2.0.

## Standards Conformance

Per the portfolio standards set. N/A rows are backed by a committed declaration or ADR; there
are no blank rows and no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` gates every change (ruff with security rules, mypy strict, pytest with an 85% branch-coverage floor); `uv.lock`, `.python-version`, and `.pre-commit-config.yaml` pin the toolchain |
| Security & Supply-Chain | Applies: Actions pinned to full commit SHAs, scoped workflow permissions, CodeQL + full-history gitleaks (`.github/workflows/security.yml`), Dependabot for pip and github-actions |
| CI/CD | Applies: `verify.yml` runs the same `make verify` gate as local; branch protection on `main` is pending (a live GitHub settings action left for the repo owner) |
| Observability | Applies (scoped): scheduled batch publisher, not a hosted runtime; run health is visible in Actions, and availability/drift history accrues in `data/history.json` |
| Accessibility | Applies: static semantic HTML pages; formal assistive-technology review not yet performed (tracked in `docs/RESPONSIBLE-TECH-AUDITS.md` E) |
| Internationalization | N/A: findings quote English normative spec text for a specialist audience; no civic public-service workflow. `docs/I18N.md` |
| AI Evaluation | N/A: no LLM or model component; grading is deterministic and the MCP server only reads published files |
| Documentation | Applies: README, ROADMAP, CONTRIBUTING, SECURITY, CHANGELOG, CITATION.cff, ADRs (`docs/adr/`) |
| Quality & Metrics | Applies: deterministic findings tied to cited spec text; coverage floor enforced in CI; drift tracked across runs |
| Release & Versioning | N/A: continuously published site/dataset with no downstream version consumer. `docs/adr/0001-release-versioning-na.md` |
| Responsible-Tech Framework | Applies: `docs/RESPONSIBLE-TECH-AUDITS.md` (ethics, bias, privacy, transparency, accessibility, security declarations) |

## Support

This is independent, unpaid work. If it has been useful to you, you can
<a href='https://ko-fi.com/T6T6GMYTU' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi6.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
