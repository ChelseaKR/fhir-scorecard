# Narration grounding evaluation (ADR 0003)

`python -m fhir_scorecard.ai.eval --records site/scorecards.json --output evals/ai/results/<date>-narration-<provider>-<model>.json`
narrates every published scorecard and counts, per endpoint and overall, the
claims the model generated, the claims shown (every cited quote occurs
verbatim in the retained copy of the cited specification page under
`corpus/`), and the claims withheld. It does not measure whether a shown claim
is a correct reading of the passage it quotes, and no gold explanations exist.

Each result records provider, model, prompt version, UTC date, the Git commit,
and the records file; `tests/test_ai_narration.py` refuses a result without
that provenance. Numbers are never written by hand. Running it needs the `ai`
extra and `FHIR_AI_PROVIDER` / `FHIR_AI_MODEL` (Anthropic API or Amazon
Bedrock through the public SDK).
