# Responsible-Tech Audits - fhir-scorecard

Instantiates the portfolio `RESPONSIBLE-TECH-FRAMEWORK.md`. First recorded: 2026-08-07
(portfolio standards conformance pass). Append-only, like ADRs: later audits extend this file.

**Status: declarations with pointers to enforced code, not a completed formal audit.** Every
applicability line below is filled in; narrative findings summarize what is already enforced in
code and published on the site.

## Applicability

- **A Ethics:** applies (this project grades named organizations in public; findings below)
- **B Bias:** applies (grading rubric design; findings below)
- **C Privacy:** applies (findings below; no personal data by design)
- **D Transparency:** applies (findings below)
- **E Accessibility:** applies (published static site; formal review not yet performed)
- **F Security:** applies (declarations below)
- **AI-EVAL:** applies to the optional narration layer only (ADR 0003). Grading is
  deterministic and calls no model; the MCP server is a read-only file reader over the published
  dataset and the retained specification pages and calls no model. `fhir-scorecard narrate`
  explains one published scorecard: every claim must quote the cited page and is withheld if the
  quote does not verify, the prompt forbids characterizing the organization or naming
  regulators, the output is labeled AI-generated and is not on the site, and
  `evals/ai/results/` records the measured grounding rate with provider, model, and commit.
- **I18N:** N/A - see `docs/I18N.md`.

## A. Ethics

**Findings:** the project publicly grades named payers, providers, and vendors, so the primary
ethical risks are (1) saying something false about an organization and (2) enabling misuse of
the probing pattern. Both are constrained in code and policy:

- Grades are deterministic and fail closed; every finding carries a citation to the FHIR R4 or
  SMART App Launch spec clause it rests on. There is no editorial scoring.
- Only public, unauthenticated discovery documents are fetched (`/metadata` and
  `/.well-known/smart-configuration`); the fetcher sends an identifying User-Agent with a
  contact address, uses HTTPS only, and makes one request per resource per run
  (`src/fhir_scorecard/fetch.py`). Scope and scheme are enforced on redirects too: a stock
  `urllib` opener follows a `Location` anywhere, including an `https` to `http` downgrade, so a
  server could have redirected `/metadata` to a patient search and had the request made.
  `DiscoveryRedirectHandler` refuses both, and `tests/test_probe_contract.py` proves it against
  a loopback server that records which requests actually arrived.
- A false "endpoint is dead" claim is treated as a defect: probing runs from three vantages and
  results are reconciled, because a single host is an unreliable narrator
  (`.github/workflows/pages.yml`, `src/fhir_scorecard/vantage.py`). Those three vantages are
  GitHub-hosted runner images sharing one provider's network, which the published copy says
  outright: they catch a host-local fault and cannot catch a rule applied to that provider's
  address space, so a failure from all three is published as "not reached from that network"
  rather than as an endpoint being down. A genuinely independent vantage remains an open item.
- Graded organizations have a public correction path: the site's claim page ("Add, correct, or
  remove an endpoint", `src/fhir_scorecard/site.py`).
- The site states plainly that grades are observational snapshots, not audits, rankings of care
  quality, or compliance determinations (README "Status"; methodology page).

**Open items:** none blocking; a dated re-review belongs with any registry-policy change.

## B. Bias

**Findings:** the ranking signal is a letter grade over named organizations, so rubric design
is the bias surface. Mitigations in place:

- Every dimension is a mechanical check against published spec requirements, applied
  identically to every registry entry; there are no per-organization adjustments.
- Provider Directory endpoints are graded on their own terms rather than penalized against
  Patient Access expectations (registry `kind` taxonomy).
- Unreachable endpoints are published with a stated reason instead of being dropped, so the
  dataset does not survivor-bias toward healthy endpoints. They are graded `not observed`, never
  `F`: a run that retrieved no document has nothing to say about what a named organization
  publishes, and `F` was carrying that meaning and "answered and scored badly" at the same time
  (`grading.py`, `letter`). *Corrected 2026-08-16; this line previously said an unreachable
  endpoint receives an F, which the code stopped doing and this file did not follow.*
- Selection bias is documented rather than hidden: `data/CANDIDATES.md` and
  `data/rejected.json` record what was tried and why entries were rejected, and
  `docs/payer-verifiability.md` writes up the asymmetry in who can be observed at all.

## C. Privacy

**Findings:** no personal data is collected, stored, or processed, by design.

- Inputs are server metadata documents from unauthenticated public URLs. The project never
  authenticates and never touches patient data. This used to read "enforced by having no code
  path that does", which was the wrong kind of assurance and was not even accurate: the code
  path was `urllib`'s default redirect handler, which follows a server's `Location` wherever it
  points. It is now enforced by an allowlist on redirect scheme and path
  (`DiscoveryRedirectHandler`) and covered by tests that fail if a request is made to anything
  but the two discovery documents.
- The published dataset contains organization names, base URLs, grades, findings, and
  timestamps only.
- The site is static GitHub Pages output; this repository adds no analytics or tracking of its
  own.

## D. Transparency

**Findings:** transparency is the product's core mechanism:

- Every finding links its spec citation; the methodology page documents what grades mean and
  what they do not.
- Registry entries ship only after live verification, with the method and date recorded in the
  entry; the loader refuses entries without a verification record
  (`src/fhir_scorecard/registry.py`).
- Capability drift and availability history are published (the `capability-history`
  branch, seeded from `data/history.json`), so a grade
  can be traced across time.
- The MCP server's `grading_method` tool returns the documented limits, so an assistant can be
  told what the numbers do not mean.

## E. Accessibility

**Findings:** the published site is static, semantic HTML with no client-side framework.

Half of what this section used to record as open is now closed, and the half that is still open
is the more important half. An automated gate exists: `fhir_scorecard.accessibility` runs twelve
mechanical rules over every built page, each naming the WCAG 2.2 Level A success criterion it
implements, and it blocks both a merge and a publish. **No formal assistive-technology review has
been performed.** That is still open, and the gate does not stand in for it: it decides only what
a static reader can decide from markup, and
[ADR 0004](adr/0004-accessibility-and-weight-gates-without-a-browser.md) lists what it cannot see
- colour contrast as rendered, focus order, visible focus, computed ARIA roles, reflow, and
whether an accessible name is any good. A page can satisfy every rule and be unusable with a
screen reader. Tracked in `ROADMAP.md`.

## F. Security

**Declarations (no blanks):**

- Supply chain: all GitHub Actions are pinned to full commit SHAs; workflows carry scoped
  `permissions:` blocks; Dependabot watches pip and github-actions ecosystems; `uv.lock` pins
  the dev dependency graph.
- Scanning: CodeQL (SAST, python + actions) and a full-history gitleaks secret scan run on
  push, pull request, and a weekly schedule (`.github/workflows/security.yml`); ruff runs with
  its security (`S`) rule set inside `make verify` on every change.
- Probing conduct: HTTPS only, conservative timeouts, one request per resource per run, an
  identifying User-Agent with a contact address; there is deliberately no MCP tool that can
  probe an arbitrary URL.
- Reporting: see `SECURITY.md` for the disclosure channel.
