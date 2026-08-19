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
make sync                         # uv sync --locked: exactly the toolchain uv.lock pins
make verify                       # lock check, lint, format, strict typecheck, tests + coverage floor, audit
.venv/bin/fhir-scorecard grade --registry data/registry.json --out site/
```

Offline mode (no network) grades the discovery documents captured under `tests/fixtures/`, which
are real `/metadata` and SMART documents with a capture date on them, not hand-written examples:

```bash
.venv/bin/fhir-scorecard grade --offline \
  --fixtures tests/fixtures --registry tests/fixtures/registry.json --out .cache/offline-site
```

An offline run writes its availability history to `.cache/` unless you name a path, and refuses
to write into a history file a live run wrote. Fixture observations in the real availability
record would report a day nobody measured. See `tests/fixtures/README.md` for what each capture
covers and how to refresh one.

Check a single endpoint without the registry, the history, or the site. Nothing is published and
nothing under `data/` is written or read:

```bash
.venv/bin/fhir-scorecard check https://fhir.example.org/r4 --kind payer --min-grade B
```

With a `--min-grade` the command exits 1 when the measured grade is below it, which makes it a
build gate for the operator of the endpoint being checked. Without one it reports what it saw
and exits 0, because a finding about a published document is data, not a failure of the program
that read it. An endpoint no vantage reached is reported as `not observed`, never as `F`; with a
threshold set that fails the gate, on the stated ground that the threshold could not be
evaluated. The same command is packaged as a composite GitHub Action in `action.yml` —
see [docs/ci-action.md](docs/ci-action.md).

## What it observes, and what it never touches

Everything graded here is **public, unauthenticated surface**:

- `[base]/metadata` , the FHIR CapabilityStatement every server must expose
- `[base]/.well-known/smart-configuration` , SMART on FHIR discovery

This project **never accesses patient data, never authenticates, and never probes beyond the
public discovery surface**. One request per resource per probing run, an identifying User-Agent
with a contact address, HTTPS only, and conservative timeouts. HTTPS and the two-path scope hold
on every hop: a redirect pointing anywhere else is refused rather than followed, because a stock
`urllib` opener would happily have taken `/metadata` to a patient search or onto a plaintext
connection. That is a tested property, not a claim: `tests/test_probe_contract.py` runs the real
fetcher against a loopback server and fails if the second request is ever made. The site is
rebuilt on a schedule
and on demand, never on a commit, so a scheduled day costs an endpoint at most six requests: two
documents from each of three probing runs, and none from the run that publishes.

## Where it measures from

Every published grade reconciles probes from more than one vantage (`vantage.py`), on a
deliberately asymmetric rule: **one vantage reaching an endpoint proves it is reachable; one
vantage failing proves nothing.** That rule exists because a live payer endpoint was once
recorded as dead when a middlebox on the probing network intercepted TLS.

What the vantages are, precisely: **three GitHub-hosted runner images** (Ubuntu, macOS, Windows).
They are three hosts on one provider's network, not three independent networks, and nothing this
project publishes calls them that. Three hosts catch a fault local to one host or one TLS trust
store, which is the failure that prompted the mechanism. They cannot catch a source-address rule,
bot filter, geo rule, or rate limit applied to that provider's address space, because such a rule
reaches all three at once. A run where every vantage failed is therefore published as *not
reached from that network on that day*, with the reason, rather than as an endpoint being down.

Each vantage counts once. The publishing run makes no probe of its own and grades the documents
the probing runs retrieved (`--from-probes`); before that it re-probed under a label one artifact
already carried, and every card reported four vantages when three had reported. Adding a
genuinely independent vantage — a residential or other-provider runner posting a `probes-*.json`
— is an open item in [ROADMAP.md](ROADMAP.md), and until one exists the published wording stays
"one network."

## What it grades (v0.1)

| Dimension | What it asks |
|---|---|
| **Reachability** | Does `/metadata` answer, over HTTPS, with HTTP 2xx, in reasonable time? Whether what came back is a CapabilityStatement is a separate question, asked under transparency |
| **Capability transparency** | Does the CapabilityStatement say what the server runs (FHIR version, software, resources, interactions), or is it boilerplate? |
| **Interop readiness** | Are US Core / CARIN / Da Vinci canonicals declared in any of the five conformance elements R4 defines (`rest.resource.supportedProfile`, `rest.resource.profile`, `instantiates`, `imports`, `meta.profile`)? Is SMART discovery present? Is OAuth security declared? |

Grades are deterministic. Every finding carries a citation to the FHIR R4 or SMART App Launch
spec, and every finding describes a document this project actually retrieved.

An endpoint no vantage could reach is published as **not observed**, with the reason and the
vantages that tried, and is not graded: its content dimensions carry no score, and the checks
that read a CapabilityStatement do not run. It never drops out of the dataset, and it never
acquires findings about what its publisher did not publish. `F` means the opposite and only the
opposite: the endpoint answered, and what it declares falls short across the checks. The two used
to share a letter, and the site rendered both with one sentence about a network.

Between them sits a third case that real servers produce often: `/metadata` answers with HTTP 200
and the body is not a CapabilityStatement — an `OperationOutcome`, a sign-in page from an
authenticating gateway, a search `Bundle`. That endpoint is reachable and it is graded, because
answering that path with that document is a fact about it. But the checks that read *fields inside*
a CapabilityStatement have nothing to read, so they report the parse failure once (`T0`, `I0`) and
carry the weight they always carried, rather than reporting the parser's empty defaults as things
the publisher declined to declare. Same score, no invented claim.

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

An entry records **on what basis** it is listed and **when it was last checked**, as two separate
facts a machine can read:

- `verification.basis` is `live_capability` when a conformance document was retrieved, or
  `publisher_documented` when the organization publishes this base URL in its own materials and the
  document was not retrievable on that date. The second requires the `source` that printed the URL
  and what the probe `observed`, and such an endpoint publishes as `not observed` rather than `F`.
  It is listed on purpose: **an unreachable endpoint is a finding, not a reason to drop it**, and a
  registry pruned of its failures hides exactly what this project exists to detect.
- `verification.reverified` is a later dated re-check, never an overwrite of the curation date. An
  entry with no re-check block has not been re-checked, and its page says so in words. A stale date
  must not be able to read as a fresh one.

## Cohorts and the sampling frame

A cohort is a named view over the registry whose membership comes from a **public roster** rather
than from whatever was easy to find, which is what lets a hit rate mean anything: the denominator is
fixed before any probing starts. Every member either points at registry endpoints or carries an
exclusion with a reason, a review record, a date, and a source, so the plans that publish nothing
discoverable are part of the published result rather than an absence in it.

The rule that decides which organizations get looked for at all is written down in
[docs/SAMPLING-FRAME.md](docs/SAMPLING-FRAME.md), including what was considered and rejected as a
roster - among them the guessed-hostname method that produced 0 verified endpoints out of 18
probes and a hit rate that meant nothing.

The first is the **California payer cohort** at `/california/`: the Medi-Cal managed care plans DHCS
lists plus the Covered California qualified health plan issuers, deduplicated to 27 organizations.
Eight publish a base URL this project verified from their own documentation, on the dates in
`data/registry.json`; how many of those endpoints answered on any given day is a separate number,
measured by the run that generated the page and printed beside the curated one. Those endpoints
are required to exist by the federal CMS
Interoperability and Patient Access rule (CMS-9115-F), which is the only obligation this project
claims about them. The rule does not require a plan to print its base URL where an unregistered
visitor can read it; California's Data Exchange Framework runs through the DSA and QHIOs and
requires none of these surfaces; and CMS-0057-F's additional APIs are not in force until 2027 and
are not graded.

The second is the **Texas marketplace issuer cohort** at `/texas-marketplace/`, and it exists
because California's exchange is state-based. CMS-9115-F's qualified-health-plan prong
(45 CFR 156.221) reaches issuers on the *federally-facilitated* exchanges, so this cohort takes that
prong from the regulator's own file: every issuer selling an individual-market QHP on HealthCare.gov
in Texas for 2026, as enumerated by CMS/CCIIO's QHP Landscape PY2026 Individual Medical dataset -
13,013 plan-county rows, 18 HIOS issuer IDs, **15 issuer organizations**, frozen before any URL was
probed. Six publish a base URL this project could verify from their own documentation. Three of the
fifteen publish a Patient Access base URL and one of the three answers a stranger; six publish a
Provider Directory base URL - the surface the rule requires to be reachable *without*
authentication - and four of the six answer.

## Findings

`docs/findings/` holds dated write-ups of what pointing this tool at real endpoints produced,
with the evidence beside each one and every published figure recomputed from that evidence by
`tests/test_findings_evidence.py` rather than typed.

- [What 27 California health plans publish about their FHIR endpoints](docs/findings/2026-08-15-california-payer-cohort.md)
  (curation review of 2026-08-07): 8 of 27 organizations on a public roster publish a base URL
  this project could verify. The other 19 are split four ways rather than counted as one
  number, because a plan publishing nothing and a plan publishing a URL that returns 404 are
  different results.
- [One URL, three brands](docs/findings/2026-08-15-anthem-multi-tenant-attribution.md): three
  consecutive requests to one payer's documented base URL returned CapabilityStatements naming
  three different brands, and what that means for anyone building a payer endpoint registry.
  Re-checked 2026-08-19: that URL now answers 401, so the rotation is no longer observable from
  outside, while the shared Provider Directory address in the same document still answers and is
  stable. The re-check is a dated section on the write-up, not an edit to what was seen on 08-07.

Neither is a compliance determination, and both say so.

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
| `api/index.json` counts | `endpoints_listed` is how many endpoints the registry carries and the run graded; `answered_on_this_run` is how many answered a probe during it. Never one standing in for the other |
| `dataset.schema.json` | Column names, types, and meanings |
| `api/index.json` | Every endpoint with links to its detail and its page |
| `api/endpoint/<id>.json` | Full scorecard: dimensions, findings, citations, drift |
| `scorecards.json` | The complete graded payload in one file |
| `badge/<id>.svg` | Embeddable current-grade badge linking back to the endpoint evidence |

A read-only MCP server exposes the same data to an assistant:

```bash
fhir-scorecard mcp --site site
```

It reads only the published dataset files. There is deliberately no tool that probes an endpoint:
a model deciding to fetch arbitrary URLs is a much larger security surface than one reading a
file this project already publishes. Its `grading_method` tool returns the documented limits, so
an assistant can be told what the numbers do not mean.

## Status

v0.1.0-dev. Thirty-seven endpoints across payers, payer provider directories, a federal provider
API, EHR vendor sandboxes, and reference servers, in two curated cohorts (California payers, Texas
marketplace issuers). Thirty-three were verified from a retrieved conformance document; four are
listed on the organization's own publication of a base URL that does not answer, which is a finding
about the public record rather than a gap in this one. Registry curation continues one roster at a
time - see [docs/SAMPLING-FRAME.md](docs/SAMPLING-FRAME.md) - because payer base URLs are not
predictable from company names. Grades are observational snapshots of public surfaces, not audits,
rankings of care quality, or statements about any organization's compliance.

## Provenance

Personal open-source project, built on personal time and equipment, unaffiliated with any employer
or client, past or present. Built with AI assistance (Claude Code); every change passes the
`make verify` gate (ruff lint and format checks with security rules, mypy strict, pytest with a
branch-coverage floor, and a pip-audit of the locked dependency set).

License: Apache-2.0.

## Standards Conformance

Per the portfolio standards set. N/A rows are backed by a committed declaration or ADR; there
are no blank rows and no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` gates every change and CI runs that exact target (`ruff check` + `ruff format --check` on the standard's pinned select set, mypy strict, pytest with an 85% branch-coverage floor, `pip-audit --strict` over the locked set). `make verify` opens with `uv lock --check --offline` and `make sync` installs with `uv sync --locked`, so a lockfile drifted from `pyproject.toml` fails the build rather than being installed around (`--frozen`, which the control text names, does not compare the two at all); `.python-version` and `.pre-commit-config.yaml` pin the rest of the toolchain. Dev dependencies are a PEP 735 `[dependency-groups]` group, never an installable extra |
| Security & Supply-Chain | Applies: Actions pinned to full commit SHAs, scoped workflow permissions, CodeQL + full-history gitleaks + a pip-audit of the locked dependency set, all three on push, PR and a weekly schedule (`.github/workflows/security.yml`), Dependabot for pip and github-actions. No `\|\| true` and nothing muted: the audit blocks |
| CI/CD | Applies: `verify.yml` runs the same `make verify` gate as local; branch protection on `main` is pending (a live GitHub settings action left for the repo owner) |
| Observability | Applies (scoped): scheduled batch publisher, not a hosted runtime; run health is visible in Actions, and availability/drift history accrues on the `capability-history` branch, one commit per day on which something changed (the copy on `main` is the seed the first run started from, and is no longer updated) |
| Accessibility | Applies: static semantic HTML pages; formal assistive-technology review not yet performed (tracked in `docs/RESPONSIBLE-TECH-AUDITS.md` E) |
| Internationalization | N/A: findings quote English normative spec text for a specialist audience; no civic public-service workflow. `docs/I18N.md` |
| AI Evaluation | N/A: no LLM or model component; grading is deterministic and the MCP server only reads published files |
| Documentation | Applies: README, ROADMAP, CONTRIBUTING, SECURITY, CHANGELOG, CITATION.cff, ADRs (`docs/adr/`) |
| Quality & Metrics | Applies: deterministic findings tied to cited spec text; coverage floor enforced in CI; drift tracked across runs |
| Release & Versioning | Applies: the composite Action in `action.yml` is consumed as `ChelseaKR/fhir-scorecard@<tag>`, so a tag is a shipped interface. Releases are cut by dispatching `.github/workflows/release.yml` with an existing SSH-signed annotated SemVer tag; the shared authorize workflow verifies the signature against `.github/allowed_signers` and that the commit is an ancestor of `main`, `make verify` and the full-history secret scan re-run at that commit, and the build is attested (SLSA provenance) and attached to a GitHub Release whose notes are the matching CHANGELOG section. Tag, `pyproject.toml` and CHANGELOG versions must agree or the release fails. `docs/adr/0002-release-versioning-applies-action-export.md` supersedes `docs/adr/0001-release-versioning-na.md`; the site and dataset are still published daily from `main` and are not what a version names |
| Responsible-Tech Framework | Applies: `docs/RESPONSIBLE-TECH-AUDITS.md` (ethics, bias, privacy, transparency, accessibility, security declarations) |

## Support

This is independent, unpaid work. If it has been useful to you, you can
<a href='https://ko-fi.com/T6T6GMYTU' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi6.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
