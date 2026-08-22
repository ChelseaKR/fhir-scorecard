# 0003. AI narration outside the graded path, with citations verified against retained spec pages

## Status

Accepted - 2026-08-21

## Context

This project publicly grades named payers, providers, and vendors, and its ethics finding
(`docs/RESPONSIBLE-TECH-AUDITS.md` §A) names the primary risk: saying something false about an
organization. Two design properties hold that risk down today. Grades are deterministic and
fail closed, and every finding carries a citation to the FHIR R4, SMART App Launch, or US Core
page it rests on. The audit document also records "AI-EVAL: N/A - no LLM or model component",
and the MCP server "only reads published files".

What a finding row cannot do is explain itself to someone who does not already know what a
CapabilityStatement is. `T3: 28 resource types declared` with a link to a 900-kilobyte
specification page is accurate and unhelpful to the reader the site is for. A language model
can write that explanation, and left alone it will also invent requirements, name regulators,
and call an organization compliant or not, which is exactly the false statement the ethics
finding is about.

The portfolio's `permit-bearings` and `mrf-honest` repositories settled, the same day, a shape
that keeps the line: the model narrates, the retained source text is the evidence, and a program
verifies every quoted citation before a sentence is shown. This ADR adopts that shape here,
written for this codebase and its four cited pages.

## Decision

Add an optional narration layer, `fhir_scorecard.ai`, that explains one published scorecard
and is kept outside the graded path by construction:

- **Inputs only.** It reads a record from the published dataset (`site/scorecards.json`). It
  never calls grading, and nothing in grading, the report, the site, the composite Action, or
  the MCP server's existing tools imports it. Grades are unchanged by its existence.
- **A corpus of the four cited pages.** `corpus/SOURCES.json` retains FHIR R4 `http.html` and
  `capabilitystatement.html`, the SMART App Launch conformance page, and the US Core home page,
  with URL, retrieval date, and SHA-256. HL7 publishes them under CC0; they are reproduced
  unmodified.
- **Claims must quote, and quotes must verify.** The model is shown only passages from the page
  each finding cites, ranked lexically by a per-code hint and the finding message. Every claim
  must cite passage IDs with verbatim quotes; the layer checks each quote against the whole
  retained page after typography, case, and whitespace folding, with a minimum length. A claim
  with any citation that does not verify is withheld and counted.
- **Describes documents, never the organization.** The prompt forbids characterizing the
  organization, naming regulators, or predicting enforcement, and the output label says the
  narration describes what an endpoint published on one day and is not an audit or a compliance
  statement. A "not observed" finding is explained as a check that did not run.
- **Provider through the public SDK, credential from the environment.** Anthropic API or Amazon
  Bedrock via the `anthropic` package in an optional `ai` extra; the standard-library-only
  boundary of everything that ships in the Action is unchanged because nothing there imports it.
- **Measured, not asserted.** `python -m fhir_scorecard.ai.eval` narrates the published
  scorecards and reports claims generated, shown, and withheld, with provider, model, prompt
  version, date, and commit recorded; a test refuses a result file without that provenance.
- **One deterministic MCP tool, no model.** `cited_passages` returns each finding with the
  verbatim passages of the page it cites, so an MCP client that explains a grade can quote the
  specification rather than recall it. The server still calls no model.
- **A CLI, not a site feature.** `fhir-scorecard narrate` prints the narration. The published
  site shows no AI prose; putting narration on a public page about a named organization would
  need its own ADR covering review and labeling.

## Consequences

- `README.md` and `docs/RESPONSIBLE-TECH-AUDITS.md` stop saying "no LLM or model component"
  and say instead that grading and the site have none, and that an optional narration layer
  exists outside them with the controls above.
- The locked dependency set gains the `ai` extra; `make sync` and `make audit` cover it.
- A narration can be wrong while every citation verifies: it can quote a true passage in
  support of a mistaken sentence. The verifier bounds fabricated citations; it does not bound
  misreadings. No person has reviewed the prompt, the passages it selects, or the Spanish
  output.
- Narration is non-deterministic run to run. Grades are not.

## Alternatives considered

- **Keep the N/A.** Honest, and leaves the reader alone with the specification. Rejected for
  the reader's sake, with the boundary above as the price.
- **Let the model read the CapabilityStatement itself.** Rejected: that is a model in the graded
  path, and a model's reading of a document about a named organization is the risk this
  project exists to avoid.
- **Add a model-backed MCP tool.** Rejected: the server's promise is "only reads published
  files"; `cited_passages` keeps that promise and gives a client the text it needs.
