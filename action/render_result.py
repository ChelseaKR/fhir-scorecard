#!/usr/bin/env python3
"""Publish composite-action outputs, a job summary, and one concise failure annotation.

Runs whatever the check exited with, so the evidence survives a failing gate. It reads the
result the check already wrote and never recomputes a grade: this script's only job is to say
out loud what the artifact says.

Two things it must not do, both of which are the reason it is a script and not three lines of
shell. It must not print ``F`` for an endpoint nobody reached, because ``not observed`` and
``F`` are different values in this project and squashing them back together in the summary
would undo the split. And it must not describe a failing gate as non-compliance, a ranking, or
a quality judgement: it reports the letter measured, the threshold the caller chose, and the
disclaimer the artifact carries.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

NOT_OBSERVED = "not observed"


def _escape_command(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _card(artifact: dict[str, Any]) -> dict[str, Any]:
    cards = artifact.get("scorecards") or []
    first = cards[0] if cards else {}
    return first if isinstance(first, dict) else {}


def build_summary(artifact: dict[str, Any], passed: bool) -> str:
    """A plain-language job summary grounded entirely in the written result."""
    card = _card(artifact)
    grade = str(card.get("grade", ""))
    name = str(card.get("name") or "the endpoint")
    state = "passed" if passed else "did not pass"
    headline = (
        f"**{name}: not observed on this run. Gate {state}.**"
        if grade == NOT_OBSERVED
        else f"**{name}: grade {grade or '—'}. Gate {state}.**"
    )
    lines = [
        "## FHIR endpoint check",
        "",
        headline,
        "",
        f"Measured {artifact.get('generated_at', 'at an unrecorded time')} "
        f"from one vantage: {artifact.get('vantage', 'unspecified')}.",
        "",
    ]
    dimensions = card.get("dimensions") or []
    if isinstance(dimensions, list) and dimensions:
        lines.extend(["| Dimension | Score |", "| --- | --- |"])
        for dimension in dimensions:
            if not isinstance(dimension, dict):
                continue
            score = dimension.get("score")
            shown = "not observed on this run" if score is None else f"{score} / 100"
            lines.append(f"| {dimension.get('title', '')} | {shown} |")
        lines.append("")
    disclaimer = artifact.get("disclaimer")
    if isinstance(disclaimer, str) and disclaimer:
        lines.extend([disclaimer, ""])
    lines.extend(
        [
            "The complete machine-readable result is at the action's `result-json` output.",
            "",
        ]
    )
    return "\n".join(lines)


def _append(path_var: str, content: str) -> None:
    path = os.environ.get(path_var)
    if path:
        with Path(path).open("a") as handle:
            handle.write(content)


def failure_message(artifact: dict[str, Any], min_grade: str) -> str:
    """One sentence naming what was measured and what the caller asked for."""
    if not artifact:
        return (
            "The FHIR endpoint could not be checked; read the action log for the input or "
            "retrieval error."
        )
    grade = str(_card(artifact).get("grade", ""))
    requirement = f" Requested minimum: {min_grade}." if min_grade else ""
    if grade == NOT_OBSERVED:
        return (
            "The endpoint's public documents were not retrieved on this run, so the requested "
            "minimum grade could not be evaluated. This is not a grade and not a finding about "
            f"what the endpoint publishes.{requirement}"
        )
    return f"Measured grade {grade} on this run.{requirement}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--gate-rc", required=True, type=int)
    parser.add_argument("--min-grade", default="")
    parser.add_argument("--write-summary", default="true")
    args = parser.parse_args()

    result_path = Path(args.json)
    try:
        artifact = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        artifact = {}
    passed = args.gate_rc == 0 and bool(artifact)
    card = _card(artifact)
    grade = str(card.get("grade", ""))
    _append(
        "GITHUB_OUTPUT",
        "\n".join(
            [
                f"grade={grade}",
                f"reachable={str(bool(card.get('reachable'))).lower()}",
                f"observed={str(bool(grade) and grade != NOT_OBSERVED).lower()}",
                f"passed={str(passed).lower()}",
                f"result-json={result_path}",
                "",
            ]
        ),
    )
    if artifact and args.write_summary.casefold() == "true":
        _append("GITHUB_STEP_SUMMARY", build_summary(artifact, passed))
    if not passed:
        message = failure_message(artifact, args.min_grade)
        print(f"::error title=FHIR endpoint check::{_escape_command(message)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
