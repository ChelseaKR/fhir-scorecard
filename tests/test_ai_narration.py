"""AI narration outside the graded path: corpus verifier, narration, MCP passages, eval, CLI."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard.ai import corpus as corpus_module
from fhir_scorecard.ai import eval as eval_module
from fhir_scorecard.ai.corpus import (
    MIN_QUOTE_CHARS,
    CorpusError,
    CorpusIndex,
    html_sections,
    normalize_for_match,
    split_passages,
)
from fhir_scorecard.ai.narrate import (
    CODE_HINTS,
    LABEL,
    NOT_NARRATED_REASONS,
    PROMPT_VERSION,
    STATUS_NARRATED,
    STATUS_NOT_NARRATED,
    NarrationError,
    grounding_passages,
    narrate,
    narration_schema,
    not_narratable_reason,
)
from fhir_scorecard.ai.provider import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    ProviderError,
    ScriptedProvider,
    SDKProvider,
    Settings,
    provider_from_env,
    provider_from_settings,
)
from fhir_scorecard.ai.retrieval import rank, tokenize
from fhir_scorecard.cli import main
from fhir_scorecard.mcp import call_tool

ROOT = Path(__file__).resolve().parents[1]
# A snapshot of the published dataset; site/ is a build output and is not checked in.
SCORECARDS = ROOT / "tests" / "fixtures" / "ai" / "scorecards.json"
# Records with nothing to cite: no dimensions, no findings, or findings whose cited page is
# not retained. The first is the exact record Gauntlet submitted in issue #47.
NOT_NARRATABLE = ROOT / "tests" / "fixtures" / "ai" / "not-narratable.json"
CORPUS = CorpusIndex.load(ROOT)
RECORDS = json.loads(SCORECARDS.read_text(encoding="utf-8"))["scorecards"]
EMPTY_RECORDS = json.loads(NOT_NARRATABLE.read_text(encoding="utf-8"))["scorecards"]
CITATIONS = sorted({f["citation"] for r in RECORDS for d in r["dimensions"] for f in d["findings"]})


def _findings(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**f, "dimension": d["key"]} for d in record["dimensions"] for f in d["findings"]]


def _real_quote(passage_id: str, words: int = 12) -> str:
    passage = CORPUS.passage(passage_id)
    assert passage is not None
    return " ".join(passage.text.split()[:words])


def _reply(offered: list[str], *, bad: bool = False) -> str:
    claims: list[dict[str, Any]] = [
        {
            "text": "Supported claim.",
            "dimension": "transparency",
            "citations": [{"passage_id": offered[0], "quote": _real_quote(offered[0])}],
        }
    ]
    if bad:
        claims += [
            {
                "text": "Altered quote.",
                "dimension": "interop",
                "citations": [
                    {"passage_id": offered[0], "quote": "words that are nowhere in the spec at all"}
                ],
            },
            {
                "text": "Unoffered passage.",
                "dimension": "overall",
                "citations": [
                    {"passage_id": "fhir-r4-http#0", "quote": _real_quote("fhir-r4-http#0")}
                ],
            },
            {"text": "No citation.", "dimension": "reachability", "citations": []},
            {"text": "", "dimension": "reachability", "citations": []},
            "junk",
        ]
    return json.dumps({"claims": claims})


# --- corpus -----------------------------------------------------------------


def test_every_published_citation_resolves_to_a_retained_page() -> None:
    assert CITATIONS
    for url in CITATIONS:
        assert CORPUS.source_for_url(url) is not None, url
    assert CORPUS.source_for_url("https://example.test/nope") is None
    assert CORPUS.not_retained == {}


def test_committed_corpus_matches_its_manifest_hashes() -> None:
    manifest = json.loads((ROOT / "corpus" / "SOURCES.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        digest = hashlib.sha256((ROOT / entry["local_copy"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["source_id"]
    summary = CORPUS.summary()
    assert set(summary) == {
        "fhir-r4-http",
        "fhir-r4-capabilitystatement",
        "smart-app-launch-conformance",
        "us-core-index",
    }
    assert all(item["passages"] > 0 for item in summary.values())


def test_html_sectioner_breaks_on_headings_and_skips_scripts() -> None:
    markup = (
        "<html><head><title>t</title><script>var x=1;</script></head><body>"
        "<p>intro text</p><h2>Security</h2><p>Servers SHALL use TLS.</p>"
        "<table><tr><td>cell one</td><td>cell two</td></tr></table>"
        "<h3>Sub</h3><ul><li>item</li></ul></body></html>"
    )
    sections = html_sections(markup)
    assert sections[0] == ("", "intro text")
    assert sections[1][0] == "Security" and "Servers SHALL use TLS." in sections[1][1]
    assert "cell one\n\ncell two" in sections[1][1]
    assert sections[2] == ("Sub", "item")
    assert "var x" not in "".join(b for _, b in sections)
    with pytest.raises(CorpusError, match="no text"):
        html_sections("<html><script>x</script></html>")


def test_passage_splitting_bounds_size() -> None:
    long = "Sentence one is here. " * 200
    passages = split_passages("doc", [("H", long), ("I", "short")])
    assert all(len(p.text) <= corpus_module.PASSAGE_MAX_CHARS for p in passages)
    assert [p.index for p in passages] == list(range(len(passages)))
    assert passages[-1].heading == "I" and passages[-1].text == "short"


def test_verify_quote_is_verbatim_but_typography_tolerant() -> None:
    passage = CORPUS.documents["fhir-r4-http"].passages[10]
    quote = " ".join(passage.text.split()[:10])
    assert CORPUS.verify_quote("fhir-r4-http", quote) is not None
    folded = quote.upper().replace("'", "\u2019").replace("-", "\u2013")
    assert CORPUS.verify_quote("fhir-r4-http", folded) is not None
    assert CORPUS.verify_quote("fhir-r4-http", quote + " plus invented trailing words") is None
    assert CORPUS.verify_quote("fhir-r4-http", "too short") is None
    assert len(normalize_for_match("too short")) < MIN_QUOTE_CHARS
    assert CORPUS.verify_quote("missing", quote) is None
    assert CORPUS.passage("fhir-r4-http#99999") is None
    assert CORPUS.passage("nope#0") is None and CORPUS.passage("fhir-r4-http#x") is None


def test_corpus_load_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="cannot read"):
        CorpusIndex.load(tmp_path)
    (tmp_path / "corpus").mkdir()
    manifest = tmp_path / "corpus" / "SOURCES.json"
    manifest.write_text(json.dumps({"sources": []}), encoding="utf-8")
    with pytest.raises(CorpusError, match="no sources"):
        CorpusIndex.load(tmp_path)
    entry = {"source_id": "x", "local_copy": "corpus/x.html", "format": "html"}
    manifest.write_text(json.dumps({"sources": [entry]}), encoding="utf-8")
    with pytest.raises(CorpusError, match="missing"):
        CorpusIndex.load(tmp_path)
    (tmp_path / "corpus" / "x.html").write_text("<p>body</p>", encoding="utf-8")
    manifest.write_text(json.dumps({"sources": [{**entry, "format": "pdf"}]}), encoding="utf-8")
    with pytest.raises(CorpusError, match="unsupported"):
        CorpusIndex.load(tmp_path)
    manifest.write_text(
        json.dumps(
            {
                "sources": [entry],
                "not_retained": [{"citation_url": "https://x.test", "reason": "pdf"}],
            }
        ),
        encoding="utf-8",
    )
    index = CorpusIndex.load(tmp_path)
    assert index.not_retained == {"https://x.test": "pdf"}
    assert index.passages_for(["x", "absent"])[0].text == "body"


def test_retrieval_ranks_relevant_passages() -> None:
    passages = CORPUS.passages_for(["fhir-r4-http"])
    ranked = rank("capabilities interaction metadata", passages, 3)
    assert ranked and "capabilities" in ranked[0].passage.heading.lower()
    assert rank("", passages, 3) == [] and rank("zzzzqqq", passages, 3) == []
    assert rank("metadata", passages, 0) == []
    assert tokenize("The well-known smart-configuration JSON") == [
        "well-known",
        "smart-configuration",
        "json",
    ]


# --- narration --------------------------------------------------------------


def test_grounding_is_scoped_to_each_findings_page() -> None:
    record = RECORDS[0]
    findings = _findings(record)
    passages, unresolved = grounding_passages(findings, CORPUS)
    assert passages and unresolved == []
    allowed = {CORPUS.source_for_url(f["citation"]) for f in findings}
    assert {p.source_id for p in passages} <= allowed
    assert set(CODE_HINTS) == {f["code"] for r in RECORDS for f in _findings(r)} | {"T0"}
    only, missing = grounding_passages(
        [{"code": "R1", "message": "x", "citation": "https://example.test/not-retained"}], CORPUS
    )
    assert only == [] and missing == ["https://example.test/not-retained"]


def test_narration_keeps_verified_claims_and_withholds_the_rest() -> None:
    record = RECORDS[0]
    offered = [p.passage_id for p in grounding_passages(_findings(record), CORPUS)[0]]
    provider = ScriptedProvider([_reply(offered, bad=True)])
    narration = narrate(record, corpus=CORPUS, provider=provider)
    assert narration.grade == record["grade"] and narration.endpoint_id == record["endpoint_id"]
    assert [c.text for c in narration.claims] == ["Supported claim."]
    assert narration.claims[0].citations[0].verified
    assert narration.withheld_count == 5
    reasons = {w.text: w.reasons for w in narration.withheld}
    assert any("does not occur" in r for r in reasons["Altered quote."])
    assert any("not offered" in r for r in reasons["Unoffered passage."])
    assert reasons["No citation."] == ("no citation",)
    assert narration.label == LABEL["en"]
    assert narration.offered_passage_ids == tuple(offered)
    assert narration.to_dict()["withheld_count"] == 5
    call = provider.calls[0]
    assert "Write the claims in English." in call.user and f"Grade: {record['grade']}." in call.user
    assert call.schema == narration_schema()
    assert "never characterize the organization" in call.system


def test_narrated_record_carries_the_model_called_receipt() -> None:
    record = RECORDS[0]
    offered = [p.passage_id for p in grounding_passages(_findings(record), CORPUS)[0]]
    narration = narrate(record, corpus=CORPUS, provider=ScriptedProvider([_reply(offered)]))
    assert narration.status == STATUS_NARRATED and narration.model_called
    assert narration.not_narrated_reason is None
    payload = narration.to_dict()
    assert payload["status"] == "narrated" and payload["model_called"] is True
    passages = grounding_passages(_findings(record), CORPUS)[0]
    assert (
        not_narratable_reason(record, _findings(record), {p.passage_id: p for p in passages})
        is None
    )


@pytest.mark.parametrize(
    ("record", "reason"), list(zip(EMPTY_RECORDS, NOT_NARRATED_REASONS, strict=True))
)
def test_record_with_nothing_to_cite_is_refused_before_the_model_call(
    record: dict[str, Any], reason: str
) -> None:
    """Issue #47: every claim must cite an offered passage, so a record that offers none can
    only produce withheld claims. The model is not called, and the receipt says so."""
    provider = ScriptedProvider([])  # any call would raise "no response left"
    for language in ("en", "es"):
        narration = narrate(record, corpus=CORPUS, provider=provider, language=language)
        assert provider.calls == []
        assert narration.status == STATUS_NOT_NARRATED
        assert narration.not_narrated_reason == reason
        assert narration.model_called is False
        assert (narration.input_tokens, narration.output_tokens) == (0, 0)
        assert narration.claims == () and narration.withheld == ()
        assert narration.offered_passage_ids == ()
        assert narration.grade == record["grade"] and narration.endpoint_id == record["endpoint_id"]
        assert narration.label == LABEL[language]
        assert (narration.provider, narration.model) == (provider.name, provider.model)
        assert narration.prompt_version == PROMPT_VERSION
        payload = narration.to_dict()
        assert payload["status"] == "not_narrated" and payload["model_called"] is False
        assert payload["not_narrated_reason"] == reason and payload["withheld_count"] == 0
    if reason == "no passages offered":
        assert narration.uncited_sources == ("https://example.test/not-retained",)
        assert narration.finding_codes == ("R1",)
    else:
        assert narration.uncited_sources == () and narration.finding_codes == ()


def test_not_narratable_reason_is_the_most_specific_one() -> None:
    assert not_narratable_reason({"dimensions": []}, [], {}) == "no dimensions"
    assert not_narratable_reason({"dimensions": ["junk"]}, [], {}) == "no dimensions"
    assert not_narratable_reason({"dimensions": [{"key": "r"}]}, [], {}) == "no findings"
    assert not_narratable_reason({"dimensions": [{"key": "r"}]}, [{}], {}) == "no passages offered"


def test_narration_in_spanish_and_error_paths() -> None:
    record = RECORDS[0]
    spanish = narrate(
        record, corpus=CORPUS, provider=ScriptedProvider(['{"claims": []}']), language="es"
    )
    assert spanish.label == LABEL["es"] and spanish.claims == ()
    with pytest.raises(NarrationError, match="language"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider([]), language="fr")
    with pytest.raises(NarrationError, match="not a scorecard"):
        narrate({"name": "x"}, corpus=CORPUS, provider=ScriptedProvider([]))
    with pytest.raises(NarrationError, match="did not return JSON"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider(["?"]))
    with pytest.raises(NarrationError, match="claims list"):
        narrate(record, corpus=CORPUS, provider=ScriptedProvider(['{"claims": 1}']))


# --- MCP passages tool (no model) -------------------------------------------


def test_mcp_cited_passages_returns_spec_text_without_a_model(tmp_path: Path) -> None:
    endpoint_id = RECORDS[0]["endpoint_id"]
    site = tmp_path / "site"
    (site / "api" / "endpoint").mkdir(parents=True)
    record = {**RECORDS[0], "endpoint": {"endpoint_id": endpoint_id, "grade": RECORDS[0]["grade"]}}
    (site / "api" / "endpoint" / f"{endpoint_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    payload = json.loads(
        call_tool(site, "cited_passages", {"endpoint_id": endpoint_id}, root=ROOT)["content"][0][
            "text"
        ]
    )
    assert payload["endpoint_id"] == endpoint_id
    assert payload["findings"] and all(f["passages"] for f in payload["findings"])
    first = payload["findings"][0]["passages"][0]
    assert CORPUS.verify_quote(first["passage_id"].split("#")[0], first["text"][:200])
    assert "retrieval matches, not a determination" in payload["note"]
    denied = json.loads(
        call_tool(site, "cited_passages", {"endpoint_id": "../x"})["content"][0]["text"]
    )
    assert "bare identifier" in denied["error"]
    missing = json.loads(
        call_tool(site, "cited_passages", {"endpoint_id": endpoint_id}, root=tmp_path)["content"][
            0
        ]["text"]
    )
    assert missing["error"].startswith("corpus unavailable")


# --- provider ----------------------------------------------------------------


class _Response:
    def __init__(self, text: str, stop_reason: str) -> None:
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.model = "served"
        self.usage = types.SimpleNamespace(input_tokens=3, output_tokens=2)


class _Client:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_sdk_provider_translates_outcomes() -> None:
    client = _Client(_Response("{}", "end_turn"))
    provider = SDKProvider(client, model="m", name="anthropic")
    completion = provider.complete_json(system="s", user="u", schema={}, max_tokens=5)
    assert (completion.text, completion.model, completion.input_tokens) == ("{}", "served", 3)
    assert client.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert provider.name == "anthropic" and provider.model == "m"
    for response, message in (
        (_Response("{}", "refusal"), "declined"),
        (_Response("{", "max_tokens"), "truncated"),
        (_Response(" ", "end_turn"), "no text"),
    ):
        with pytest.raises(ProviderError, match=message):
            SDKProvider(_Client(response), model="m", name="x").complete_json(
                system="s", user="u", schema={}, max_tokens=5
            )
    import anthropic
    import httpx2 as httpx  # the SDK's own HTTP client package

    request = httpx.Request("POST", "https://example.test")
    status = anthropic.APIStatusError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    with pytest.raises(ProviderError, match="status 500"):
        SDKProvider(_Client(status), model="m", name="x").complete_json(
            system="s", user="u", schema={}, max_tokens=5
        )
    with pytest.raises(ProviderError, match="unreachable"):
        SDKProvider(
            _Client(anthropic.APIConnectionError(request=request)), model="m", name="x"
        ).complete_json(system="s", user="u", schema={}, max_tokens=5)


def test_the_two_providers_default_to_different_models_on_purpose() -> None:
    """A default is what a caller gets when they name no model, so it has to be one that
    provider can be asked for.

    The AWS account this project's evals run on is not entitled to Sonnet 5 on Bedrock -
    ``InvokeModel`` returns ``AccessDeniedException`` while the entitlement API reports the model
    AUTHORIZED, so entitlement can only be established by invoking. The Bedrock default was the
    Sonnet 5 id anyway, which is a 403 for anyone taking the Bedrock path without an explicit
    ``FHIR_AI_MODEL``, and Bedrock is the path this project's own evals take.

    Both halves are pinned because the failure mode is a tidy-up: the two lines look like they
    have drifted apart and should be reconciled, and reconciling them either breaks Bedrock again
    or holds a third-party deployer with ordinary API access back a model generation for a reason
    that is not about them.
    """
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5"
    assert DEFAULT_BEDROCK_MODEL == "global.anthropic.claude-sonnet-4-6"
    assert f"global.anthropic.{DEFAULT_ANTHROPIC_MODEL}" != DEFAULT_BEDROCK_MODEL


def test_the_bedrock_default_is_the_model_the_recorded_evals_ran_on() -> None:
    """The claim above is checkable against this repository's own committed evidence rather than
    only asserted: a default no recorded run has ever completed a call on is a guess."""
    results = sorted((ROOT / "evals" / "ai" / "results").glob("*.json"))
    assert results, "the recorded eval runs are the evidence for this default"
    runs = [json.loads(path.read_text(encoding="utf-8"))["run"] for path in results]
    bedrock = {run["model"] for run in runs if run["provider"] == "bedrock"}
    assert bedrock == {DEFAULT_BEDROCK_MODEL}, sorted(bedrock)


def test_settings_and_provider_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.from_environ({}) == Settings("anthropic", DEFAULT_ANTHROPIC_MODEL, None)
    bedrock = Settings.from_environ({"FHIR_AI_PROVIDER": "bedrock", "AWS_REGION": "us-east-1"})
    assert bedrock.region == "us-east-1" and bedrock.model == DEFAULT_BEDROCK_MODEL
    with pytest.raises(ProviderError, match="FHIR_AI_PROVIDER"):
        Settings.from_environ({"FHIR_AI_PROVIDER": "openai"})

    class _Error(Exception):
        pass

    built: list[str] = []
    fake = types.SimpleNamespace(
        Anthropic=lambda **kw: built.append("anthropic") or object(),
        AnthropicBedrock=lambda **kw: built.append("bedrock") or object(),
        AnthropicError=_Error,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert provider_from_env({}).name == "anthropic"
    assert provider_from_settings(Settings("bedrock", "m", "us-west-2")).name == "bedrock"
    assert built == ["anthropic", "bedrock"]

    def failing(**kw: Any) -> object:
        raise _Error("no credential")

    fake.Anthropic = failing
    with pytest.raises(ProviderError, match="could not configure"):
        provider_from_env({})
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ProviderError, match="could not be imported"):
        provider_from_settings(Settings("anthropic", "m", None))
    with pytest.raises(ProviderError, match="no response left"):
        ScriptedProvider([]).complete_json(system="s", user="u", schema={}, max_tokens=1)


# --- eval and CLI -----------------------------------------------------------


def test_eval_scores_records_and_records_provenance(tmp_path: Path) -> None:
    records = RECORDS[:2]
    offered = [p.passage_id for p in grounding_passages(_findings(records[0]), CORPUS)[0]]
    provider = ScriptedProvider([_reply(offered, bad=True), "not json"])
    result = eval_module.run(records, corpus=CORPUS, provider=provider)
    assert result["summary"]["records"] == 1
    assert (result["summary"]["claims_generated"], result["summary"]["claims_shown"]) == (6, 1)
    assert result["summary"]["fraction_claims_with_verified_citations"] == round(1 / 6, 4)
    assert result["errors"] == [{"index": "1", "error": "the model did not return JSON"}]
    assert result["not_narrated"] == [] and result["summary"]["records_not_narrated"] == 0
    assert result["records"][0]["status"] == "narrated"
    assert result["records"][0]["model_called"] is True
    assert eval_module.summarize([])["records"] == 0
    # A record refused before the model call is recorded with its reason and zero tokens, and
    # stays out of the grounding fractions: it is not a perfectly grounded narration.
    refused = eval_module.run(
        [*records[:1], *EMPTY_RECORDS], corpus=CORPUS, provider=ScriptedProvider([_reply(offered)])
    )
    assert refused["summary"]["records"] == 1
    assert refused["summary"]["records_not_narrated"] == 3
    assert refused["summary"]["records_with_no_withheld_claims"] == 1.0
    assert refused["summary"]["fraction_claims_with_verified_citations"] == 1.0
    assert [r["not_narrated_reason"] for r in refused["not_narrated"]] == list(NOT_NARRATED_REASONS)
    assert all(
        r["model_called"] is False and r["input_tokens"] == 0 and r["status"] == "not_narrated"
        for r in refused["not_narrated"]
    )
    assert [r["index"] for r in refused["not_narrated"]] == [1, 2, 3]
    assert refused["errors"] == []
    assert "records_not_narrated" in eval_module.metadata(provider, ROOT, SCORECARDS)["scoring"]
    meta = eval_module.metadata(provider, ROOT, SCORECARDS)
    assert meta["status"] == "recorded_live_run" and len(meta["commit"]) == 40
    assert eval_module.git_commit(tmp_path) == "unknown"
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"scorecards": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no scorecards"):
        eval_module.load_records(empty)
    assert len(eval_module.load_records(SCORECARDS, limit=3)) == 3


def test_committed_results_carry_provenance() -> None:
    results = sorted((ROOT / "evals" / "ai" / "results").glob("*.json"))
    assert results
    for path in results:
        payload = json.loads(path.read_text(encoding="utf-8"))
        run = payload["run"]
        assert run["status"] in {"recorded_live_run", "not_run"}
        if run["status"] == "recorded_live_run":
            assert run["provider"] in {"anthropic", "bedrock"} and len(run["commit"]) == 40
            assert payload["summary"]["records"] > 0


def test_narrate_cli_prints_claims_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    record = RECORDS[0]
    offered = [p.passage_id for p in grounding_passages(_findings(record), CORPUS)[0]]
    replies = iter([_reply(offered, bad=True), _reply(offered)])
    monkeypatch.setattr(
        "fhir_scorecard.ai.provider.provider_from_env", lambda: ScriptedProvider([next(replies)])
    )
    base = ["narrate", "--scorecards", str(SCORECARDS), "--root", str(ROOT)]
    assert main([*base, "--endpoint", record["endpoint_id"]]) == 0
    out = capsys.readouterr().out
    assert f"grade {record['grade']}" in out and "1. Supported claim." in out
    assert "5 statement(s) withheld" in out and "Prompt version: narrate-v1" in out
    assert main([*base, "--endpoint", record["endpoint_id"], "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["withheld_count"] == 0 and payload["claims"][0]["text"] == "Supported claim."
    assert main([*base, "--endpoint", "nope"]) == 2
    assert "unknown endpoint" in capsys.readouterr().err
    # Issue #47: a record with nothing to cite prints the documented outcome and never calls.
    monkeypatch.setattr(
        "fhir_scorecard.ai.provider.provider_from_env", lambda: ScriptedProvider([])
    )
    empty = ["narrate", "--scorecards", str(NOT_NARRATABLE), "--root", str(ROOT)]
    assert main([*empty, "--endpoint", "fixture-no-dimensions"]) == 0
    out = capsys.readouterr().out
    assert "Not narrated: no dimensions." in out and "the model was not called" in out
    assert "Prompt version: narrate-v1" in out and "withheld" not in out
    assert main([*empty, "--endpoint", "fixture-no-passages", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_narrated" and payload["model_called"] is False
    assert payload["not_narrated_reason"] == "no passages offered"
    assert payload["input_tokens"] == 0 and payload["claims"] == []
    assert main(["narrate", "--scorecards", str(tmp_path / "x.json"), "--endpoint", "e"]) == 2
    assert "cannot read" in capsys.readouterr().err
    monkeypatch.setattr(
        "fhir_scorecard.ai.provider.provider_from_env",
        lambda: (_ for _ in ()).throw(ProviderError("no credential")),
    )
    assert main([*base, "--endpoint", record["endpoint_id"]]) == 2
    assert "no credential" in capsys.readouterr().err
