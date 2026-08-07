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
- **AI-EVAL:** N/A - no LLM or model component. Grading is deterministic; the MCP server is a
  read-only file reader over the published dataset and calls no model.
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
  (`src/fhir_scorecard/fetch.py`).
- A false "endpoint is dead" claim is treated as a defect: probing runs from multiple vantages
  and results are reconciled, because a single network is an unreliable narrator
  (`.github/workflows/pages.yml`, `src/fhir_scorecard/vantage.py`).
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
- Unreachable endpoints receive an F with a stated reason instead of being dropped, so the
  dataset does not survivor-bias toward healthy endpoints.
- Selection bias is documented rather than hidden: `data/CANDIDATES.md` and
  `data/rejected.json` record what was tried and why entries were rejected, and
  `docs/payer-verifiability.md` writes up the asymmetry in who can be observed at all.

## C. Privacy

**Findings:** no personal data is collected, stored, or processed, by design.

- Inputs are server metadata documents from unauthenticated public URLs. The project never
  authenticates and never touches patient data (enforced by having no code path that does).
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
- Capability drift and availability history are published (`data/history.json`), so a grade
  can be traced across time.
- The MCP server's `grading_method` tool returns the documented limits, so an assistant can be
  told what the numbers do not mean.

## E. Accessibility

**Findings:** the published site is static, semantic HTML with no client-side framework.
No formal assistive-technology review or automated accessibility gate exists yet; that remains
open and is the honest state of this section. Tracked in `ROADMAP.md`.

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
