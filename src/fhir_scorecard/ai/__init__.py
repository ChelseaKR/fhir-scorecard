"""AI narration outside the graded path (ADR 0003).

Nothing here is imported by grading, the report, the site, the Action, or the
MCP server's existing tools. A scorecard is produced deterministically and
then, optionally, narrated by a model whose every claim must quote one of the
retained specification pages under ``corpus/`` and pass
:meth:`fhir_scorecard.ai.corpus.CorpusIndex.verify_quote` before it is shown.
"""
