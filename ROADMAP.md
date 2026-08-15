# Roadmap: from a working tool to a public service

The target shape is the one `gtfs-scorecard` already proves out: a live, indexable, citable data
service that a non-engineer can land on from a search and act on. This document is the plan to
get there, and the honest constraints on it.

## The constraint that governs everything

`gtfs-scorecard` earns organic search because **1,128 agencies means 1,128 indexable pages**,
each answering a real query ("is my transit agency's GTFS feed any good"). `fhir-scorecard` has
**19 endpoints**. Nineteen pages is not an SEO surface, it is a pamphlet.

So the sequencing below is deliberate: build the page infrastructure early because it is cheap
and shapes everything after it, but understand that **search traffic is gated on registry
growth, and registry growth is gated on payers publishing base URLs**. Seven of nine payer
organizations with a documented URL verified; the other fifteen candidate organizations had no
public URL to try. That is a curation problem and partly an industry problem, not a build
problem, and no amount of markup fixes it.

Two ways the registry can realistically grow:

1. **Systematic sourcing.** CMS-regulated payers number in the hundreds. Each one's developer
   portal has to be found and read. This is slow, mostly manual, and the only reliable method.
2. **Inbound correction.** A claim flow (phase 3) lets a payer add or fix their own entry. This
   is how `gtfs-scorecard` grows without the maintainer doing all the work, and it only becomes
   possible once the site is worth landing on.

Nineteen endpoints with a defensible method beats two hundred with a guessed registry. The
project's credibility is the asset; the page count follows it, not the other way round.

---

## Phase 1: indexable surface — done

*Goal: every endpoint and organization is a real URL a search engine can find and a person can
read.*

- [x] **Per-endpoint pages** at `/endpoint/<id>/`, each with its grade, findings with spec
      citations, availability, drift history, and verification provenance
- [x] **Per-organization pages** at `/org/<slug>/` for organizations with more than one surface
      (Cigna already has Patient Access plus Provider Directory)
- [x] **Kind index pages** at `/payers/`, `/ehr/`, `/reference/`, since "payer FHIR API status"
      is the query that should land somewhere useful
- [x] `sitemap.xml`, `robots.txt`, canonical URLs, and per-page titles and meta descriptions
      written from the data rather than templated boilerplate
- [x] **JSON-LD**: `Dataset` on the index, `WebAPI` / `Organization` on endpoint pages
- [x] **Methodology page** at `/how-we-grade/` explaining every finding code and its citation,
      linked from every finding
- [x] Static-site generation stays deterministic and dependency-free, same as the grader

## Phase 2: it is a dataset, not just a page — mostly done

*Goal: the data is reusable, citable, and machine-consumable by someone who never visits.*

- [x] `dataset.csv` at a stable URL with a Table Schema style `dataset.schema.json`
- [x] `CITATION.cff` so the dataset can be cited
- [x] A read-only **API surface** (`/api/index.json`, `/api/endpoint/<id>.json`) as static files
- [x] **MCP server** (`fhir-scorecard mcp`), read-only over the published dataset, with a
      `grading_method` tool that returns the documented limits so an assistant can be told what
      the numbers do not mean
- [ ] **Historical archive**: dated snapshots so availability and drift can be studied over time,
      which is the part nobody else has
- [ ] A monthly dated dataset release, signed

## Phase 3: participation

*Goal: the people who own these endpoints can correct and extend the record.*

- [x] **Claim / correction flow**: a structured issue template that captures a base URL plus
      evidence, feeding the same verification the registry already enforces in code
- [x] **"Add your endpoint"** page written for payer developer-relations staff, not for engineers
- [x] **Per-endpoint status badge** an owner can embed, linking back to current findings
- [x] Published **SECURITY.md** and a stated non-adversarial posture:
      this measures public surfaces and will remove or correct anything on request with evidence
- [x] Published **CODE_OF_CONDUCT.md** for project participation

## Phase 4: production hardening

*Goal: it runs unattended and its own quality is enforced, not asserted.*

- [ ] Custom domain (`fhirscorecard.org` or similar) with HTTPS and a stable canonical origin
- [ ] **Lighthouse and accessibility budgets** as merge gates, matching the 100-accessibility bar
      held elsewhere in the portfolio
- [ ] **SEO config validation** in CI: sitemap completeness, canonical correctness, JSON-LD
      validity, no orphan pages
- [~] **Multi-vantage probing** so availability is not one network's opinion. This is now a known
      real failure: a live payer endpoint was recorded as dead because of TLS interception on the
      probing network. The reconciliation is built and each vantage is counted once, but the
      three vantages feeding it are GitHub-hosted runner images on one provider's network, so
      today it is one network's opinion sampled three times and every published sentence says so.
      Finishing this means **one genuinely independent vantage** — a residential or other-provider
      runner posting a `probes-*.json`, however irregular — after which the wording can change and
      not before
- [ ] OpenSSF Scorecard, CodeQL, dependency and secret scanning, SHA-pinned actions
      (partly done), signed dataset releases
- [ ] `CHANGELOG.md` and semantic versioning with a real `v0.1.0` tag

## Phase 5: the parts that make it worth citing

*Goal: say something nobody else is saying.*

- [ ] **Availability leaderboard** once the 14-observation floor is met across the registry
- [ ] **Drift timeline** per endpoint: when did this payer change what it declares
- [ ] **Conformance-over-time report**, published monthly, with the write-up as its front door
- [ ] **Coverage tracker**: which CMS-regulated payers have a *publicly checkable* endpoint at
      all, with the "documented but unreachable" and "no public URL found" populations counted
      separately and never merged

---

## Explicitly not doing

- **Authenticated probing.** Registering for API access to check conformance would produce better
  data and destroy the project's entire premise, which is that the public surface is observable
  by the public.
- **Compliance determinations.** The grades describe observable properties. "Not compliant" is a
  regulator's word and will not appear.
- **Bilingual (EN/ES) delivery**, for now. It is correct for a resident-facing service like
  `gtfs-scorecard`; this audience is payer developer-relations and health-IT engineers, who are
  not the population that language access serves. Revisit if a resident-facing view is added.
- **Inflating the registry.** No guessed URLs, no unverified entries, no counting sandboxes as
  production. Three verified servers were held out of the registry for a full commit because
  grading them correctly required version-aware scoring first.

## Sequencing note

Phases 1 and 2 are mostly mechanical and can proceed now. Phase 3 only pays off once phase 1
makes the site worth landing on. Phase 4's multi-vantage work is the highest-value item in the
whole document, because it fixes a measurement error already known to have produced a wrong
public claim. Phase 5 needs calendar time to accumulate observations and cannot be rushed.
