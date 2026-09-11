"""Interval arithmetic for a published share, and the refusals that guard it (#103).

The cohort, coverage and availability pages publish shares as counts over fixed denominators:
8 of 27 California organizations, 61 of 176 frame organizations. Those are honest counts, and
they carry no uncertainty and no floor. This is the arithmetic that would attach both.

**Nothing here is wired to a page, and that is the point of shipping it alone.** Two of the
three things needed to wire it are the maintainer's to decide, and neither is a coding task:

* **The small-cell threshold**, which is a statement about what this site refuses to say. The
  sibling ``mrf-honest`` sets 20 in its ADR 0007, measured over payer files: at a denominator of
  20 a 95 percent Wilson interval at the least favourable point still spans 0.401 of the scale.
  This project publishes *named organizations*, and the same number does not automatically mean
  the same thing here. So this module has **no default threshold**: every caller states the one
  it was told to use, and ``estimate_proportion`` cannot be called without it.
* **Four new published columns** - ``interval_low``, ``interval_high``, ``method``,
  ``suppressed`` - in ``dataset.csv`` and ``api/``, which is a schema change to a dataset the
  README invites people to cite.

**What is not a decision is the method.** It is ported from ``mrf-honest``'s ``statistics.py``
(ADR 0007) so that two published proportions in this portfolio carry the same interval under the
same name, which is what #103 asks for. Ported rather than vendored whole: that module also
carries a sampling-frame and stratum vocabulary built for payer files, and this repository's own
refusal - an unreviewed organization inside a denominator - already lives in ``coverage.py`` and
is not restated here. What is copied is the arithmetic, the clamp, the correction, and the
refusal shape.

**A refusal is a published outcome, not an error.** Every entry point returns either a
:class:`Proportion` carrying its own denominator and interval, or a :class:`Refusal` carrying the
reason there is no number. There is deliberately no third outcome and no bare ``float``: a caller
cannot render a point estimate without the interval that qualifies it, and cannot mistake a
refusal for a zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

#: Two-sided normal quantile for a 95 percent interval.
Z_95 = 1.959963984540054

#: The confidence the interval is computed at, published beside every estimate.
CONFIDENCE = 0.95

#: The interval method, published beside every estimate and matching ``mrf-honest``'s ADR 0007
#: so the same words in two projects mean the same arithmetic.
METHOD = "wilson-score"


class RefusalCode(StrEnum):
    """Why no number was produced. Both of these are outcomes a page prints."""

    EMPTY_DENOMINATOR = "empty_denominator"
    BELOW_SUPPRESSION_THRESHOLD = "below_suppression_threshold"


@dataclass(frozen=True)
class Refusal:
    """A stated reason that no proportion was produced."""

    code: RefusalCode
    reason: str
    denominator: int
    #: The threshold the caller stated, for the refusal that depends on one.
    threshold: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "refused",
            "code": str(self.code),
            "reason": self.reason,
            "denominator": self.denominator,
            "threshold": self.threshold,
            "suppressed": True,
        }


@dataclass(frozen=True)
class Proportion:
    """A proportion that carries everything needed to read it honestly."""

    numerator: int
    denominator: int
    point: float
    low: float
    high: float
    method: str
    confidence: float
    universe_size: int | None
    finite_population_correction: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": "estimated",
            "numerator": self.numerator,
            "denominator": self.denominator,
            "point": self.point,
            "interval_low": self.low,
            "interval_high": self.high,
            "method": self.method,
            "confidence": self.confidence,
            "universe_size": self.universe_size,
            "finite_population_correction": self.finite_population_correction,
            "suppressed": False,
        }

    def sentence(self) -> str:
        """One sentence a page can print without dropping the qualifiers."""
        return (
            f"{self.numerator} of {self.denominator} ({self.point * 100:.1f}%, "
            f"{self.confidence * 100:.0f}% interval {self.low * 100:.1f}% to "
            f"{self.high * 100:.1f}%)"
        )


Estimate = Proportion | Refusal

_REFUSAL_TEXT = {
    RefusalCode.EMPTY_DENOMINATOR: (
        "the denominator is zero, so there is nothing to take a proportion of"
    ),
    RefusalCode.BELOW_SUPPRESSION_THRESHOLD: (
        "the denominator is below the threshold this caller stated, at which an interval is "
        "wide enough that a percentage would say less than the counts do"
    ),
}


def refuse(code: RefusalCode, *, denominator: int, threshold: int | None = None) -> Refusal:
    """Build the stated refusal for a code, so the wording cannot drift between call sites."""
    return Refusal(
        code=code, reason=_REFUSAL_TEXT[code], denominator=denominator, threshold=threshold
    )


def effective_sample_size(denominator: int, universe_size: int | None) -> tuple[float, bool]:
    """The sample size the interval is computed at, and whether the correction was applied.

    A draw without replacement from a finite frame carries less uncertainty than one from an
    unbounded population. The correction multiplies the variance by ``(N - n) / (N - 1)``, which
    is the same as computing the interval at an effective size of ``n (N - 1) / (N - n)``.

    A census - where the frame is exactly the sample - is left uncorrected rather than given a
    zero-width interval: the formula divides by ``N - n`` there, and a published interval of zero
    width would claim the count has no uncertainty of measurement at all, which is a stronger
    claim than "we looked at every one of them on one day".
    """
    if universe_size is None or universe_size <= denominator or denominator <= 1:
        return float(denominator), False
    return denominator * (universe_size - 1) / (universe_size - denominator), True


def wilson_interval(
    numerator: int, denominator: int, *, z: float = Z_95, effective_size: float | None = None
) -> tuple[float, float]:
    """The Wilson score interval, clamped to ``[0, 1]`` and to the observed proportion.

    Chosen over the normal approximation because the normal interval is degenerate at 0 and 1,
    which is exactly where this project's shares land most often: a cohort where every listed
    endpoint answered, or none did.

    The clamp is not cosmetic. Wilson guarantees algebraically that the interval contains its own
    point estimate; binary floating point does not, and at ``numerator == denominator`` the upper
    bound evaluates to 0.9999999999999999 against an observed 1.0. An interval that excluded the
    value it was computed from would be a false statement on a page. The clamp moves a bound by at
    most one unit in the last place.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if not 0 <= numerator <= denominator:
        raise ValueError("numerator must lie between zero and the denominator")
    size = float(denominator) if effective_size is None else effective_size
    observed = numerator / denominator
    denom = 1.0 + z * z / size
    center = (observed + z * z / (2 * size)) / denom
    spread = z / denom * math.sqrt(observed * (1 - observed) / size + z * z / (4 * size * size))
    return min(observed, max(0.0, center - spread)), max(observed, min(1.0, center + spread))


def estimate_proportion(
    *,
    numerator: int,
    denominator: int,
    threshold: int,
    universe_size: int | None = None,
) -> Estimate:
    """Estimate one proportion, or state why no number was produced.

    ``threshold`` has no default on purpose: the number below which this site refuses to publish
    a percentage is a publication policy, not arithmetic, and nothing in this module decides it.

    The refusals are ordered so the most fundamental reason is the one reported: an empty
    denominator is refused before the threshold is consulted, because raising the threshold would
    not change it.
    """
    if numerator < 0 or numerator > denominator:
        raise ValueError("numerator must lie between zero and the denominator")
    if threshold < 0:
        raise ValueError("threshold cannot be negative")
    if denominator == 0:
        return refuse(RefusalCode.EMPTY_DENOMINATOR, denominator=0, threshold=threshold)
    if denominator < threshold:
        return refuse(
            RefusalCode.BELOW_SUPPRESSION_THRESHOLD, denominator=denominator, threshold=threshold
        )
    size, corrected = effective_sample_size(denominator, universe_size)
    low, high = wilson_interval(numerator, denominator, effective_size=size)
    return Proportion(
        numerator=numerator,
        denominator=denominator,
        point=numerator / denominator,
        low=low,
        high=high,
        method=METHOD,
        confidence=CONFIDENCE,
        universe_size=universe_size,
        finite_population_correction=corrected,
    )
