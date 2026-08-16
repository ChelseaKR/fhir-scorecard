"""Threshold evaluation for a single-endpoint CI check.

The caller sets the threshold and the tool never invents one. With no threshold the check is
informational and exits zero whatever it found, which keeps the existing contract intact: a
finding about a published document is data, not a failure of the program that read it.

Two rules make the gate safe to point at a named organization's endpoint:

* ``NOT_OBSERVED`` is never compared against a letter. A run that retrieved nothing has nothing
  to say about what the endpoint declares, and ranking that absence against ``F`` is exactly the
  conflation :mod:`fhir_scorecard.grading` split apart. When a threshold was requested and
  nothing was observed, the gate fails **because the threshold could not be evaluated**, and the
  reason says so in those words.
* A failing gate reports the grade it measured and the threshold the caller chose. It never
  reports compliance, quality, or a comparison against any other endpoint; grades are comparable
  within a kind only, and this check grades one endpoint against one number.
"""

from __future__ import annotations

from dataclasses import dataclass

from fhir_scorecard.grading import NOT_OBSERVED, Scorecard

#: Letters worst to best, matching the bands in :func:`fhir_scorecard.grading.letter`.
GRADE_ORDER = ("F", "D", "C", "B", "A")

_RANK = {letter: rank for rank, letter in enumerate(GRADE_ORDER)}


@dataclass(frozen=True)
class GateOutcome:
    passed: bool
    #: Empty when the gate passed. Never phrased as a compliance or quality claim.
    reason: str


def evaluate(card: Scorecard, *, min_grade: str = "", detail: str = "") -> GateOutcome:
    """Whether ``card`` clears ``min_grade``, and why not when it does not.

    ``detail`` is the retrieval error this run recorded, passed through verbatim so that a
    vantage-local problem (a TLS-intercepting middlebox, a blocked runner) reads as what it is
    rather than as something the endpoint did.
    """
    if not min_grade:
        return GateOutcome(True, "")
    if card.grade == NOT_OBSERVED:
        because = f" ({detail})" if detail else ""
        return GateOutcome(
            False,
            f"the endpoint's public documents were not retrieved on this run{because}, so the "
            f"requested minimum grade {min_grade} could not be evaluated. That is not a grade "
            "and not a finding about what this endpoint publishes.",
        )
    if _RANK[card.grade] < _RANK[min_grade]:
        return GateOutcome(
            False,
            f"grade {card.grade} is below the requested minimum {min_grade}, for the documents "
            "retrieved on this run.",
        )
    return GateOutcome(True, "")
