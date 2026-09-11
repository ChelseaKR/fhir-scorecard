"""The interval has to contain its own estimate, refuse rather than round, and decide nothing.

Three kinds of test:

* **Properties**, over every shape of input hypothesis can find: containment, bounds, the
  ordering of refusals, and that the correction never widens an interval.
* **A second reader**, recomputing the Wilson bounds from the closed form written out here, so
  the module cannot agree with itself by construction.
* **The decisions this module must not make**: it has no default threshold, and it publishes the
  same method name as the sibling project whose arithmetic it carries.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fhir_scorecard.statistics import (
    CONFIDENCE,
    METHOD,
    Z_95,
    Proportion,
    Refusal,
    RefusalCode,
    effective_sample_size,
    estimate_proportion,
    wilson_interval,
)

COUNTS = st.integers(min_value=1, max_value=5000)


def _closed_form(numerator: int, denominator: int, size: float) -> tuple[float, float]:
    """Wilson's bounds, written out here rather than imported, as a second reader."""
    z = Z_95
    p = numerator / denominator
    a = 1 + z**2 / size
    b = p + z**2 / (2 * size)
    c = z * math.sqrt(p * (1 - p) / size + z**2 / (4 * size**2))
    return (b - c) / a, (b + c) / a


# --- properties ---


@given(denominator=COUNTS, raw=st.integers(min_value=0, max_value=10**6))
def test_the_interval_always_contains_its_own_point_estimate(denominator: int, raw: int) -> None:
    numerator = raw % (denominator + 1)
    low, high = wilson_interval(numerator, denominator)
    assert 0.0 <= low <= numerator / denominator <= high <= 1.0


@given(
    denominator=COUNTS,
    raw=st.integers(min_value=0, max_value=10**6),
    extra=st.integers(min_value=0, max_value=10**5),
)
def test_the_correction_never_widens_an_interval(denominator: int, raw: int, extra: int) -> None:
    numerator = raw % (denominator + 1)
    universe = denominator + extra
    size, corrected = effective_sample_size(denominator, universe)
    low, high = wilson_interval(numerator, denominator, effective_size=size)
    plain_low, plain_high = wilson_interval(numerator, denominator)
    assert (high - low) <= (plain_high - plain_low) + 1e-12
    assert corrected is (universe > denominator and denominator > 1)


@given(
    denominator=st.integers(min_value=1, max_value=500),
    raw=st.integers(min_value=0, max_value=10**6),
    threshold=st.integers(min_value=0, max_value=600),
)
def test_an_estimate_is_either_a_proportion_or_a_stated_refusal(
    denominator: int, raw: int, threshold: int
) -> None:
    numerator = raw % (denominator + 1)
    result = estimate_proportion(numerator=numerator, denominator=denominator, threshold=threshold)
    if denominator < threshold:
        assert isinstance(result, Refusal)
        assert result.code is RefusalCode.BELOW_SUPPRESSION_THRESHOLD
        assert result.as_dict()["suppressed"] is True
    else:
        assert isinstance(result, Proportion)
        assert result.low <= result.point <= result.high
        assert result.as_dict()["suppressed"] is False


# --- a second reader ---


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(0, 1), (1, 1), (8, 27), (61, 176), (75, 132), (3, 4), (500, 1000)],
)
def test_the_bounds_match_the_closed_form_written_out_independently(
    numerator: int, denominator: int
) -> None:
    low, high = wilson_interval(numerator, denominator)
    want_low, want_high = _closed_form(numerator, denominator, float(denominator))
    observed = numerator / denominator
    # The same clamp the module documents, applied here too: comparing against the unclamped
    # bounds would fail at k == n for the reason the clamp exists.
    assert low == pytest.approx(min(observed, max(0.0, want_low)), abs=1e-12)
    assert high == pytest.approx(max(observed, min(1.0, want_high)), abs=1e-12)


def test_all_four_clamps_are_needed_and_each_has_a_denominator_that_needs_it() -> None:
    """Which way the floating-point error runs depends on the denominator, so a test that
    picked one denominator and one direction would prove a quarter of the clamp.

    Measured over n = 1 to 399. At k == n the unclamped upper bound lands above 1.0 for 104
    denominators and below the observed 1.0 for 90. At k == 0 the unclamped lower bound lands
    below 0.0 for 48 and above the observed 0.0 for 69. Each of the four clamps gets a case.
    """
    # min(1.0, ...): the bound overshoots the scale.
    assert _closed_form(11, 11, 11.0)[1] > 1.0
    assert wilson_interval(11, 11)[1] == 1.0
    # max(observed, ...): the bound would exclude the 1.0 it was computed from.
    assert _closed_form(10, 10, 10.0)[1] < 1.0
    assert wilson_interval(10, 10)[1] == 1.0
    # max(0.0, ...): the bound goes negative.
    assert _closed_form(0, 21, 21.0)[0] < 0.0
    assert wilson_interval(0, 21)[0] == 0.0
    # min(observed, ...): the bound would exclude the 0.0 it was computed from.
    assert _closed_form(0, 3, 3.0)[0] > 0.0
    assert wilson_interval(0, 3)[0] == 0.0


def test_the_published_shares_this_issue_names_get_intervals_a_reader_can_check() -> None:
    """8 of 27 is the California cohort's share on the live site."""
    estimate = estimate_proportion(numerator=8, denominator=27, threshold=20)
    assert isinstance(estimate, Proportion)
    assert estimate.sentence().startswith("8 of 27 (29.6%, 95% interval ")
    assert estimate.low < estimate.point < estimate.high


# --- what this module must not decide ---


def test_there_is_no_default_threshold() -> None:
    """The number below which this site refuses to publish a percentage is a publication
    policy. A default here would be this module deciding it."""
    with pytest.raises(TypeError):
        estimate_proportion(numerator=1, denominator=2)  # type: ignore[call-arg]


def test_an_empty_denominator_is_refused_before_the_threshold_is_consulted() -> None:
    refusal = estimate_proportion(numerator=0, denominator=0, threshold=0)
    assert isinstance(refusal, Refusal)
    assert refusal.code is RefusalCode.EMPTY_DENOMINATOR
    assert "nothing to take a proportion of" in refusal.reason


def test_a_zero_share_is_a_number_and_not_a_refusal() -> None:
    """0 of 40 is a measurement. Only an absent denominator or a small one refuses."""
    estimate = estimate_proportion(numerator=0, denominator=40, threshold=20)
    assert isinstance(estimate, Proportion)
    assert estimate.point == 0.0
    assert estimate.high > 0.0


def test_the_quantile_is_the_two_sided_normal_one_for_the_stated_confidence() -> None:
    """Without this, nothing here holds the quantile at all: the closed form written out in this
    file imports ``Z_95`` from the module under test, so the two readers move together and a
    wrong quantile agrees with itself. The standard library settles it independently, and ties
    the constant to the confidence it is published under - a 95 percent label over the 68
    percent quantile is a mistake this portfolio has shipped before."""
    from statistics import NormalDist

    expected = NormalDist().inv_cdf(1 - (1 - CONFIDENCE) / 2)
    assert pytest.approx(expected, abs=1e-12) == Z_95


def test_the_method_and_confidence_are_the_siblings_so_two_projects_mean_one_thing() -> None:
    """#103 asks for one interval method under one name across this portfolio.
    ``mrf-honest``'s ADR 0007 publishes ``wilson-score`` at 95 percent."""
    estimate = estimate_proportion(numerator=8, denominator=27, threshold=20)
    assert isinstance(estimate, Proportion)
    assert estimate.method == METHOD == "wilson-score"
    assert estimate.confidence == CONFIDENCE == 0.95
    assert estimate.as_dict()["method"] == "wilson-score"


def test_impossible_counts_raise_rather_than_publish() -> None:
    """Every refusal that is a programming error rather than a published outcome, from both
    entry points. Read off the coverage report: each of these lines had no test."""
    for numerator, denominator in ((-1, 10), (11, 10)):
        with pytest.raises(ValueError, match="numerator must lie"):
            estimate_proportion(numerator=numerator, denominator=denominator, threshold=0)
    with pytest.raises(ValueError, match="denominator must be positive"):
        wilson_interval(0, 0)
    with pytest.raises(ValueError, match="numerator must lie"):
        wilson_interval(5, 3)
    with pytest.raises(ValueError, match="threshold cannot be negative"):
        estimate_proportion(numerator=0, denominator=10, threshold=-1)
