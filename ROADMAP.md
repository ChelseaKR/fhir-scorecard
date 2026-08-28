# Roadmap: from a working tool to a public service

The target shape is the one `gtfs-scorecard` already proves out: a live, indexable, citable data
service that a non-engineer can land on from a search and act on. This document is the plan to
get there, and the honest constraints on it.

## The constraint that governs everything

`gtfs-scorecard` earns organic search because **1,128 agencies means 1,128 indexable pages**,
each answering a real query ("is my transit agency's GTFS feed any good"). `fhir-scorecard` has
**45 endpoints** across **three cohort pages**. Forty-five pages is still not an SEO surface,
so the conclusion this section used to draw from a smaller number survives on the real one.

Where the numbers in this document come from: they are measured from `data/registry.json`,
`data/cohorts/*.json`, and the committed rosters under `data/frames/` and `data/cohorts/`, and
`tests/test_plan_evidence.py` recomputes every figure in this section from that data, so a
number here that drifts from the registry fails the build. Recompute, don't trust: this
document once argued from 19 endpoints while the registry held 30.

So the sequencing below is deliberate: build the page infrastructure early because it is cheap
and shapes everything after it, but understand that **search traffic is gated on registry
growth, and registry growth is gated on payers publishing base URLs**. That gate is now
measured with fixed denominators instead of anecdotes: across the three published cohorts -
California's 27 organizations, Texas's 15, Florida's 15 - **23 of 57 roster organizations
publish a base URL this project could verify from the organization's own documentation**, and
every one of the other 34 carries a dated exclusion record saying exactly what its review
found. That is a curation problem and partly an industry problem, not a build problem, and no
amount of markup fixes it.

The frame is now bigger than the review. The federal-marketplace roster committed at
`data/frames/qhp-landscape-py2026-individual-medical.csv` enumerates **176 state-issuer
organizations across 30 states**, of which the Texas and Florida cohorts have reviewed 30; the
other 146 are *not yet reviewed*, which is a statement about this project's progress and never
about what those issuers publish (`docs/SAMPLING-FRAME.md`). The denominator for a national
payer-side coverage tracker exists; what does not scale mechanically is the per-issuer review.

Two ways the registry can realistically grow:

1. **Systematic sourcing, a state at a time.** The frame machinery makes the denominator free;
   each state still costs a person finding and reading every issuer's developer portal. Texas
   and Florida each took a full curation wave (`data/CANDIDATES.md`, waves nine and ten). This
   is slow, mostly manual, and the only reliable method.
2. **Inbound correction.** A claim flow (phase 3) lets a payer add or fix their own entry. This
   is how `gtfs-scorecard` grows without the maintainer doing all the work, and it only becomes
   possible once the site is worth landing on.

Forty-five endpoints with a defensible method beats two hundred with a guessed registry. The
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
- [x] **Historical archive**: dated snapshots so availability and drift can be studied over time,
      which is the part nobody else has. The `capability-history` branch accrues one dated
      commit per day on which an observation changed, and the record is now browsable at
      `/history/`: an index with the window it covers, one page per endpoint listing every
      observation with its date, and `api/history/<id>.json` beside it. An endpoint with no
      observations says so rather than rendering a zero, and no rate is published below the
      14-observation floor
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

- [x] Custom domain with HTTPS and a stable canonical origin: `https://fhir.chelseakr.com` —
      DNS, the GitHub Pages domain with its certificate issued and HTTPS enforced, and the
      canonical origin in the site build, workflow, citation file and living docs. The old
      `chelseakr.github.io/fhir-scorecard` URLs redirect, so dated findings and ADRs keep the
      URLs they were written with
- [x] **Accessibility and transfer-size budgets** as merge gates. Lighthouse itself was not
      adopted and [ADR 0004](docs/adr/0004-accessibility-and-weight-gates-without-a-browser.md)
      says why: its score is a weighted average of a moving rule set, it is not
      deterministic, and it needs a browser and an npm toolchain in a package with no
      runtime dependencies. What ships instead is twelve mechanical rules over the built
      HTML, each naming the WCAG 2.2 Level A criterion it implements, plus two
      transfer-size budgets measured from the published site. The ADR lists what a browser
      would catch that this cannot, and the assistive-technology review stays open
- [x] **SEO config validation** in CI: sitemap completeness, canonical correctness, JSON-LD
      validity, no orphan pages. Built as `fhir-scorecard audit-site`
      (`src/fhir_scorecard/audit.py`), run by the test suite against a site built from
      fixtures and by the publish workflow against the real build before it is uploaded.
      Its first run against a site carrying an organization page found twelve published,
      sitemapped `/org/` pages that nothing linked to
- [~] **Multi-vantage probing** so availability is not one network's opinion. This is now a known
      real failure: a live payer endpoint was recorded as dead because of TLS interception on the
      probing network. The reconciliation is built and each vantage is counted once, but the
      three vantages feeding it are GitHub-hosted runner images on one provider's network, so
      today it is one network's opinion sampled three times and every published sentence says so.
      Finishing this means **one genuinely independent vantage** — a residential or other-provider
      runner posting a `probes-*.json`, however irregular — after which the wording can change and
      not before
- [~] OpenSSF Scorecard and signed dataset releases still open; CodeQL, full-history secret
      scanning, dependency audit of the locked set, and SHA-pinned actions are in place
      (`.github/workflows/security.yml`)
- [x] `CHANGELOG.md` and semantic versioning with a real `v0.1.0` tag: cut 2026-08-16, released
      by `.github/workflows/release.yml` from an SSH-signed tag
      (`docs/adr/0002-release-versioning-applies-action-export.md`)

## Phase 5: the parts that make it worth citing

*Goal: say something nobody else is saying.*

- [x] **Availability leaderboard**, at `/availability/`. The floor is applied per endpoint
      rather than across the registry, because a registry-wide floor never arrives: each
      curation wave adds endpoints at zero observations and resets it. Measured on the
      live record on 2026-08-27, the floor puts 30 of 45 endpoints in the tables and
      names the other 15 with their counts. Ordering happens within a kind and never
      across one. See
      [ADR 0005](docs/adr/0005-a-leaderboard-that-publishes-what-meets-the-floor.md)
- [x] **Drift timeline** per endpoint: when did this payer change what it declares. On each
      endpoint's observation record at `/history/<id>/`, and in
      `api/history/<id>.json` as `declaration_changes`. Declarations an endpoint returns
      to are a separate list and a separate JSON array, never merged into the changes:
      one hostname in front of two backends is not a run of releases
- [ ] **Conformance-over-time report**, published monthly, with the write-up as its front door
- [~] **Coverage tracker**: which CMS-regulated payers have a *publicly checkable* endpoint at
      all, with the "documented but unreachable" and "no public URL found" populations counted
      separately and never merged. The denominator now exists - the national federal-marketplace
      roster under `data/frames/` (176 state-issuer organizations, 30 states) with per-state
      review status in `docs/SAMPLING-FRAME.md` - and adds a third population those two must
      never be merged with: *not yet reviewed*. The tracker page itself is still open

---

## The next three years

Phases 1 through 5 named the destination. This section is the route: the unchecked and
partial items above, sequenced into phases that each say what they deliver, what they wait
on, and what would tell a reader they are finished. It is written the way the rest of this
document is written - the items are the ones already argued for above, not new ambitions -
and it inherits every constraint stated there, including the one that governs: registry
growth is gated on payers publishing base URLs, and no phase below pretends otherwise.

Three years is the honest horizon because two of the phases cannot be hurried. A
conformance-over-time report needs time to have passed, and the frame's remaining states
need a person with a browser. Where a phase is blocked on something this project does not
control, it says so under its own heading rather than being dropped, so a later reader can
see the whole shape and not just the reachable part.

Each phase is one pull request. `make verify` is the gate for all of them.

### Phase 6: the site is a contract, and the contract is checked

*Delivers:* the **SEO config validation** item from phase 4, built as a first-class command
rather than a CI script. `fhir-scorecard audit-site <dir>` walks a built site and fails on:
a page the sitemap does not list; a sitemap entry no page answers; a canonical that does not
resolve to the page it sits on; a JSON-LD block that does not parse or that omits the fields
its `@type` promises; an internal link to a path the build never wrote; and a page no other
page links to. `make verify` runs it over a site built from committed fixtures, so a
generator change that orphans a page fails the build rather than the crawl.

*Depends on:* nothing.

*Done when:* every defect class above has a test that builds a site carrying exactly that
defect and asserts the audit fails on it, and the audit passes on the real offline build.
A check that cannot be shown failing does not ship.

### Phase 7: accessibility and page weight, as gates rather than intentions

*Delivers:* the **accessibility budget** half of phase 4, and the transfer-size budget the
README's Performance row currently declines to claim. Both run over the built site in the
same walk phase 6 established. The accessibility gate checks the mechanically checkable
subset - document language, one `h1`, no skipped heading level, alt text on every image,
labels for every control, `aria-*` that references an id present on the page, unique ids,
a skip link whose target exists, and unique page titles - with each check naming the WCAG
2.2 success criterion it implements. It does not replace the assistive-technology review,
which stays open in `docs/RESPONSIBLE-TECH-AUDITS.md`, and the ADR says which parts of an
axe or Lighthouse run it does not reproduce, so nobody reads a green gate as a full audit.

*Depends on:* phase 6, for the site walk and the fixture-built site.

*Done when:* each check fails against a page carrying its defect and passes on the real
build; the page-weight budget is a committed number measured from the current build rather
than a round figure; and `docs/RESPONSIBLE-TECH-AUDITS.md` section E states exactly which
half of it this closed.

### Phase 8: the archive surface

*Delivers:* the open half of phase 2's **historical archive**. The `capability-history`
branch has accrued one commit per day on which an observation changed since 2026-08-05;
none of it is browsable. This phase renders it: an archive index, a per-endpoint
availability record with its observation dates, and `api/history/<id>.json` beside the
existing per-endpoint API file, all built from the same `history.json` the daily run
writes.

*Depends on:* phase 6, so the new pages enter the sitemap, canonical and orphan checks the
moment they exist.

*Done when:* an endpoint with observations renders them; an endpoint with none says so in
words rather than rendering an empty record; the archive pages pass the phase 6 and phase 7
gates; and every figure on them is recomputed from `history.json` by a test.

### Phase 9: the drift timeline

*Delivers:* phase 5's **drift timeline**. `drift.py` already records what changed, when it
was first seen, and when a declaration returned to a state it held before. The endpoint page
shows the latest state and nothing else. This phase publishes the sequence: what this
publisher declared, when it changed, and what changed, on the endpoint page and in the
archive.

*Depends on:* phase 8.

*Done when:* an endpoint whose declarations have changed shows every recorded event in
order; an endpoint with no events says that rather than showing an empty list; a returned
state reads as a return and not as a new change; and no timeline entry exists that
`history.json` does not carry.

### Phase 10: the availability leaderboard, floored

*Delivers:* phase 5's **availability leaderboard**, under the condition phase 5 attached to
it: the 14-observation floor. The floor is not met across the registry today and will not be
met by writing code, so this phase builds the mechanism and the honest empty state together.
An endpoint below the floor is named as below the floor and never given a rate; the
population below the floor is published beside the ranked one and never merged into it.

*Depends on:* phase 8.

*Done when:* with the committed history the page ranks exactly the endpoints at or above the
floor and names the rest; a fixture in which nothing meets the floor renders the empty state
instead of a one-row leaderboard; and a test asserts no rate is ever printed for an endpoint
below the floor.

### Phase 11: the coverage tracker

*Delivers:* the open page of phase 5's **coverage tracker**. The denominator exists: 176
state-issuer organizations across 30 states under `data/frames/`, with per-state review
status in `docs/SAMPLING-FRAME.md`. The page counts four populations and never merges any
two of them: publishes a base URL this project verified; publishes one that does not answer;
publishes none that a review could find; and not yet reviewed, which is a fact about this
project rather than about an issuer.

*Depends on:* the committed frame and cohort files, which exist.

*Done when:* every number on the page is recomputed from `data/frames/` and `data/cohorts/`
by a test rather than typed; a test fails if a reviewed and an unreviewed population are
ever summed into one figure; and the page states the reviewed fraction of the frame in the
same breath as any rate it prints.

### Phase 12: conformance over time

*Delivers:* phase 5's **conformance-over-time report**, in the half a program can produce:
the computed sections of a dated report - what the graded population looked like at the
start of the window, what it looks like now, which endpoints changed grade, which changed
what they declare, and what share of the registry was observable throughout. The editorial
front door phase 5 asks for is the maintainer's to write; this phase delivers the numbers it
would be written around, and says in the document that the prose is not generated.

*Depends on:* phases 8 and 9.

*Done when:* the report generator produces a dated report from `history.json` and the graded
payload with no figure typed by hand; a window containing no change says so rather than
printing an empty table; and the report is regenerable byte-for-byte from committed inputs.

### Phase 13: dated dataset snapshots, and the signature that is not this project's to make

*Delivers:* the deterministic half of phase 2's **monthly dated dataset release**: a
snapshot builder that assembles the published dataset for a stated date into one dated
directory with a manifest of content hashes, reproducible from committed inputs.

*Blocked, and it stays blocked here:* the release itself. `.github/workflows/release.yml`
publishes from an SSH-signed annotated tag verified against `.github/allowed_signers`. Only
the holder of that key can sign one. A workflow written to publish an unsigned snapshot
would be a release path that skips the control every other release in this repository
passes, so this phase stops at the artifact and leaves the signing and the tag to the
maintainer.

*Done when:* the snapshot is byte-identical across two runs from the same inputs, its
manifest hashes verify, and the release step is documented as a maintainer action rather
than automated around.

### Phase 14: an independent vantage

*Blocked on hardware this project does not have.* Phase 4 already states the cost of the gap
and the exact thing that closes it: one runner on a network that is not the CI provider's,
posting a `probes-*.json` the publishing run can read. The reconciliation is built and each
vantage is counted once; what is missing is a machine. No amount of code changes the
sentence the site publishes, and until such a runner exists the published wording stays
"one network."

*Unblocked by:* a residential or other-provider host, however irregular its schedule, and a
decision about how it authenticates its artifact.

### Phase 15: the frame, reviewed a state at a time

*Blocked on curation, which is a person's work by design.* 146 of the frame's 176
state-issuer organizations are not yet reviewed. `docs/SAMPLING-FRAME.md` and
`CONTRIBUTING.md` both require that an entry rest on a document a person retrieved and read,
with the publisher established from the organization's own materials; the same documents
record that guessed hostnames produced 0 verified endpoints out of 18 probes. Automating
this would produce plausible, wrong entries, which is the one failure this project cannot
absorb.

*Unblocked by:* maintainer curation waves, one state at a time, at the pace
`data/CANDIDATES.md` already documents.

### Sequencing

| Order | Phase | Depends on | Done when |
|---|---|---|---|
| 1 | 6, site contract audit | nothing | every defect class fails the audit in a test |
| 2 | 7, accessibility and weight budgets | 6 | each check shown failing; budget measured, not guessed |
| 3 | 8, archive surface | 6 | history browsable; empty history says so |
| 4 | 9, drift timeline | 8 | every event rendered in order; returns read as returns |
| 5 | 10, availability leaderboard | 8 | floor enforced; below-floor population never given a rate |
| 6 | 11, coverage tracker | frame data | four populations, none merged, all recomputed |
| 7 | 12, conformance over time | 8, 9 | dated report regenerable byte-for-byte |
| 8 | 13, dataset snapshots | 12 | snapshot reproducible; release left to the key holder |
| - | 14, independent vantage | a runner off this network | blocked; not scheduled |
| - | 15, frame review | maintainer curation | blocked; proceeds a state at a time |

Phases 6 and 7 are infrastructure for everything after them and come first for that reason,
not because they are the most interesting. Phases 8 through 12 are the ones that make the
project worth citing, and each is worth less without the gates in front of it: a history
page nobody can find and no gate checks is not an archive.

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

What remains unchecked in phases 2, 4 and 5 is sequenced into dated phases under
[The next three years](#the-next-three-years), which also names the two items that are
blocked on something outside this repository and says what would unblock each.
