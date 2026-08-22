"""Narrate one published scorecard in plain language, citing only the pages its findings cite.

The scorecard is an input here, never an output: this module reads a record
from the published dataset (``site/scorecards.json``) and asks a model to
explain it. The model is shown only passages from the specification pages the
record's own findings cite, it must quote them verbatim for every claim, and a
claim whose quote does not occur in the named page is withheld and counted.
The output is labeled AI-generated, describes findings rather than the
organization, and says what the verification does and does not establish.

A record with nothing to narrate is refused before the model is called. Every
claim must quote an offered passage, so a record that offers none (no graded
dimensions, no findings, or findings whose cited pages are not retained) can
only produce claims that are all withheld. Calling the model would spend
tokens to say nothing; instead ``narrate`` returns a ``Narration`` whose
``status`` is ``not_narrated``, whose ``not_narrated_reason`` names which of
the three it was, and whose ``model_called`` is ``False`` with zero tokens, so
the receipt records that the model was never invoked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from fhir_scorecard.ai.corpus import CorpusIndex, Passage
from fhir_scorecard.ai.provider import Provider
from fhir_scorecard.ai.retrieval import rank

PROMPT_VERSION = "narrate-v1"
MAX_OUTPUT_TOKENS = 4000
PASSAGES_PER_FINDING = 2
MAX_PASSAGES = 14
LANGUAGES = ("en", "es")
STATUS_NARRATED = "narrated"
STATUS_NOT_NARRATED = "not_narrated"
# The reasons a record is refused before the model is called, in the order they
# are checked. Each is a superset of the next: no dimensions means no findings
# means no passages, and the most specific applicable reason is the one recorded.
REASON_NO_DIMENSIONS = "no dimensions"
REASON_NO_FINDINGS = "no findings"
REASON_NO_PASSAGES = "no passages offered"
NOT_NARRATED_REASONS = (REASON_NO_DIMENSIONS, REASON_NO_FINDINGS, REASON_NO_PASSAGES)
LABEL = {
    "en": (
        "AI-generated narration of a deterministic scorecard. The grade and findings were "
        "computed by fhir-scorecard without a model from public, unauthenticated discovery "
        "documents; the model only explains them. Every statement shown quotes specification "
        "text that was checked against the retained copy of that page; the check proves the "
        "passage exists and says those words, not that the statement is a correct reading of "
        "the specification. This describes what an endpoint published on one day. It is not "
        "an audit, a ranking of care quality, or a statement about any organization's "
        "regulatory compliance."
    ),
    "es": (
        "Narración generada por IA de una tarjeta de puntuación determinista. fhir-scorecard "
        "calculó la calificación y los hallazgos sin ningún modelo a partir de documentos de "
        "descubrimiento públicos y no autenticados; el modelo solo los explica. Cada enunciado "
        "mostrado cita texto de la especificación verificado contra la copia retenida de esa "
        "página; la verificación prueba que el pasaje existe y dice esas palabras, no que el "
        "enunciado sea una lectura correcta de la especificación. Describe lo que un punto de "
        "acceso publicó un día determinado. No es una auditoría, una clasificación de la calidad "
        "de la atención ni una afirmación sobre el cumplimiento normativo de ninguna organización."
    ),
}


class NarrationError(ValueError):
    """The record or the model output could not be used."""


@dataclass(frozen=True)
class Citation:
    passage_id: str
    source_id: str | None
    source_label: str | None
    quote: str
    verified: bool
    reason: str | None


@dataclass(frozen=True)
class Claim:
    text: str
    dimension: str | None
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Withheld:
    text: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Narration:
    language: str
    endpoint_id: str
    name: str
    grade: str
    kind: str
    finding_codes: tuple[str, ...]
    claims: tuple[Claim, ...]
    withheld: tuple[Withheld, ...]
    offered_passage_ids: tuple[str, ...]
    uncited_sources: tuple[str, ...]
    label: str
    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    # ``narrated`` when the model was called and its claims verified;
    # ``not_narrated`` when the record offered nothing to cite and the model
    # was never invoked. ``model_called`` is the receipt for that: it is False
    # exactly when the tokens are zero because no request was made.
    status: str = STATUS_NARRATED
    not_narrated_reason: str | None = None
    model_called: bool = True

    @property
    def withheld_count(self) -> int:
        return len(self.withheld)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["withheld_count"] = self.withheld_count
        return payload


_SYSTEM_PROMPT = """\
You explain, in plain language, what a FHIR endpoint scorecard found. A deterministic
program already graded the endpoint from its public discovery documents and listed its
findings; you do not re-grade, soften, or extend that result. Your only job is to say what
each finding means for a reader, using the specification passages provided.

Hard rules:
1. Write only claims you can support with the provided passages. Each claim must cite one
   or more passages by passage_id, and for each citation copy a verbatim quote of at least
   eight consecutive words from that exact passage. Do not alter, shorten, or paraphrase
   inside a quote. A quote that is not an exact substring of the cited passage causes the
   whole claim to be withheld.
2. Do not cite a passage that was not provided. Do not invent requirements, deadlines,
   regulations, or penalties. If a finding's page was not provided, say nothing about it.
3. Describe the endpoint's published documents; never characterize the organization. Do
   not say it is compliant, noncompliant, negligent, or good; do not mention regulators or
   enforcement. A finding that was "not observed" means the check did not run, not that it
   failed; say so if you mention it.
4. Plain language: one requirement or number per sentence; define a term (FHIR,
   CapabilityStatement, SMART, US Core) the first time it appears; keep sentences short.
   Set "dimension" on each claim to the dimension key it explains, or "overall".
5. Write between three and eight claims in the requested language. Quotes stay in the
   language of the source (English).
"""


# Retrieval hints per finding code: the words of the cited page that the check
# actually rests on. The finding message alone is about the endpoint ("/metadata
# responded in 397 ms"), which is not how the specification phrases the rule.
CODE_HINTS: dict[str, str] = {
    "R1": "capabilities interaction metadata capability statement GET base",
    "R2": "https transport security TLS certificate",
    "T0": "CapabilityStatement resource content conformance",
    "T1": "fhirVersion FHIR version the system supports",
    "T2": "software name version the software running",
    "T3": "rest resource type interaction supported servers SHALL specify",
    "T4": "rest resource interaction code read search-type",
    "I1": "profile supportedProfile US Core implementation guide",
    "I2": "well-known smart-configuration discovery document JSON",
    "I3": "security service OAuth SMART-on-FHIR authorization extension",
}


def _findings(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension in record.get("dimensions", []):
        if not isinstance(dimension, Mapping):
            continue
        for finding in dimension.get("findings", []):
            if isinstance(finding, Mapping):
                rows.append({**finding, "dimension": str(dimension.get("key", ""))})
    return rows


def grounding_passages(
    findings: Sequence[Mapping[str, Any]], corpus: CorpusIndex
) -> tuple[list[Passage], list[str]]:
    """Passages from the page each finding cites, interleaved across findings.

    Returns the passages and any citation URL that is not retained in the corpus.
    """
    per_finding: list[list[Passage]] = []
    unresolved: list[str] = []
    for finding in findings:
        url = str(finding.get("citation", ""))
        source_id = corpus.source_for_url(url)
        if source_id is None:
            if url and url not in unresolved:
                unresolved.append(url)
            per_finding.append([])
            continue
        code = str(finding.get("code", ""))
        query = f"{CODE_HINTS.get(code, code)} {finding.get('message', '')}"
        ranked = rank(query, corpus.passages_for([source_id]), PASSAGES_PER_FINDING)
        per_finding.append([r.passage for r in ranked])
    chosen: dict[str, Passage] = {}
    depth = max((len(lst) for lst in per_finding), default=0)
    for position in range(depth):
        for lst in per_finding:
            if position < len(lst):
                chosen.setdefault(lst[position].passage_id, lst[position])
    return list(chosen.values())[:MAX_PASSAGES], unresolved


def narration_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "dimension": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "passage_id": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                                "required": ["passage_id", "quote"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["text", "dimension", "citations"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


def _dimension_lines(record: Mapping[str, Any]) -> str:
    lines = []
    for dimension in record.get("dimensions", []):
        if not isinstance(dimension, Mapping):
            continue
        score = dimension.get("score")
        shown = "not observed" if score is None else f"{score}/100"
        lines.append(f"- {dimension.get('key')} ({dimension.get('title')}): {shown}")
    return "\n".join(lines)


def _user_prompt(
    record: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    passages: Sequence[Passage],
    corpus: CorpusIndex,
    language: str,
) -> str:
    language_name = "Spanish" if language == "es" else "English"

    def status(f: Mapping[str, Any]) -> str:
        if not f.get("observed", True):
            return "not observed"
        return "pass" if f.get("ok") else "fail"

    finding_lines = (
        "\n".join(
            f"- [{f.get('dimension')}] {f.get('code')} ({status(f)}, "
            f"{f.get('points')}/{f.get('max_points')} points): {f.get('message')}"
            for f in findings
        )
        or "- none"
    )
    passage_lines = "\n".join(
        f'<passage id="{p.passage_id}" source="{corpus.documents[p.source_id].label}" '
        f'heading="{p.heading}">\n{p.text}\n</passage>'
        for p in passages
    )
    reachable = "reachable" if record.get("reachable") else "not reachable on this run"
    return "\n\n".join(
        [
            f"Write the claims in {language_name}.",
            f"Endpoint: {record.get('name')} (kind: {record.get('kind')}; {reachable}).",
            f"Grade: {record.get('grade')}.",
            f"Dimensions:\n{_dimension_lines(record)}",
            f"Findings (deterministic; do not re-evaluate):\n{finding_lines}",
            f"Specification passages (cite by passage_id; quote verbatim):\n{passage_lines}",
        ]
    )


def _verify(raw: Any, offered: Mapping[str, Passage], corpus: CorpusIndex) -> Claim | Withheld:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("text"), str):
        return Withheld("", ("malformed claim",))
    text = raw["text"].strip()
    if not text:
        return Withheld("", ("empty claim",))
    raw_citations = raw.get("citations")
    if not isinstance(raw_citations, list) or not raw_citations:
        return Withheld(text, ("no citation",))
    citations: list[Citation] = []
    reasons: list[str] = []
    for item in raw_citations:
        passage_id = str(item.get("passage_id", "")) if isinstance(item, Mapping) else ""
        quote = str(item.get("quote", "")) if isinstance(item, Mapping) else ""
        passage = offered.get(passage_id)
        if passage is None:
            citation = Citation(passage_id, None, None, quote, False, "passage was not offered")
        else:
            match = corpus.verify_quote(passage.source_id, quote)
            citation = Citation(
                passage_id,
                passage.source_id,
                corpus.documents[passage.source_id].label,
                quote,
                match is not None,
                None if match else "quote does not occur in the source text",
            )
        citations.append(citation)
        if not citation.verified:
            reasons.append(f"{passage_id}: {citation.reason} (quote: {quote[:120]!r})")
    if reasons:
        return Withheld(text, tuple(reasons))
    dimension = raw.get("dimension")
    return Claim(text, dimension if isinstance(dimension, str) else None, tuple(citations))


def not_narratable_reason(
    record: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    offered: Mapping[str, Passage],
) -> str | None:
    """Why the model must not be called for this record, or None if it may be.

    The rule is that every claim must cite an offered passage. With none
    offered, every claim the model could write would be withheld, so the call
    would spend tokens to show nothing. The three reasons are reported
    separately because they mean different things to the reader: a record with
    no dimensions is not a graded scorecard; one with dimensions but no
    findings had nothing checked; one with findings but no passages cites
    pages the corpus does not retain.
    """
    if not any(isinstance(d, Mapping) for d in record.get("dimensions", [])):
        return REASON_NO_DIMENSIONS
    if not findings:
        return REASON_NO_FINDINGS
    if not offered:
        return REASON_NO_PASSAGES
    return None


def narrate(
    record: Mapping[str, Any],
    *,
    corpus: CorpusIndex,
    provider: Provider,
    language: str = "en",
) -> Narration:
    """Explain one published scorecard record.

    Returns a ``not_narrated`` Narration, without calling the model, when the
    record offers no passage a claim could cite (see :func:`not_narratable_reason`).
    """
    if language not in LANGUAGES:
        raise NarrationError(f"language must be one of {', '.join(LANGUAGES)}")
    if "dimensions" not in record or "grade" not in record:
        raise NarrationError("record is not a scorecard: missing dimensions or grade")
    findings = _findings(record)
    passages, unresolved = grounding_passages(findings, corpus)
    offered = {p.passage_id: p for p in passages}
    reason = not_narratable_reason(record, findings, offered)
    if reason is not None:
        return Narration(
            language=language,
            endpoint_id=str(record.get("endpoint_id", "")),
            name=str(record.get("name", "")),
            grade=str(record.get("grade", "")),
            kind=str(record.get("kind", "")),
            finding_codes=tuple(str(f.get("code")) for f in findings),
            claims=(),
            withheld=(),
            offered_passage_ids=(),
            uncited_sources=tuple(unresolved),
            label=LABEL[language],
            provider=provider.name,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
            status=STATUS_NOT_NARRATED,
            not_narrated_reason=reason,
            model_called=False,
        )
    completion = provider.complete_json(
        system=_SYSTEM_PROMPT,
        user=_user_prompt(record, findings, passages, corpus, language),
        schema=narration_schema(),
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    try:
        parsed = json.loads(completion.text)
    except ValueError as exc:
        raise NarrationError("the model did not return JSON") from exc
    raw_claims = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(raw_claims, list):
        raise NarrationError("the model did not return a claims list")
    claims: list[Claim] = []
    withheld: list[Withheld] = []
    for raw in raw_claims:
        outcome = _verify(raw, offered, corpus)
        if isinstance(outcome, Claim):
            claims.append(outcome)
        else:
            withheld.append(outcome)
    return Narration(
        language=language,
        endpoint_id=str(record.get("endpoint_id", "")),
        name=str(record.get("name", "")),
        grade=str(record.get("grade", "")),
        kind=str(record.get("kind", "")),
        finding_codes=tuple(str(f.get("code")) for f in findings),
        claims=tuple(claims),
        withheld=tuple(withheld),
        offered_passage_ids=tuple(offered),
        uncited_sources=tuple(unresolved),
        label=LABEL[language],
        provider=completion.provider,
        model=completion.model,
        prompt_version=PROMPT_VERSION,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
