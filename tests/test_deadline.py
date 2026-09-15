"""Tests for deadline.py: the delivery-promise arithmetic ported from gtfs-scorecard.

The logic is domain-agnostic business-day math; these tests exist so this repository's own
coverage floor and its own test suite hold this module to the same standard as everything else
it ships, not because the arithmetic is new.
"""

from __future__ import annotations

import datetime as dt
import decimal

from fhir_scorecard import deadline


def test_business_days_after_skips_a_weekend() -> None:
    # Friday 2026-09-11 + 2 business days = Tuesday 2026-09-15 (skips Sat/Sun).
    friday = dt.date(2026, 9, 11)
    assert deadline.business_days_after(friday, 2) == dt.date(2026, 9, 15)


def test_business_days_after_skips_a_federal_holiday() -> None:
    # Thursday 2026-07-02 + 2 business days: Friday 07-03 counts, Saturday/Sunday do not,
    # Independence Day (observed Friday 07-03? No: 07-04-2026 is a Saturday, observed Friday
    # 07-03) is itself the holiday, so the count lands on Monday 07-06.
    start = dt.date(2026, 7, 1)  # Wednesday
    assert deadline.federal_holidays(2026) & {dt.date(2026, 7, 3)}
    result = deadline.business_days_after(start, 2)
    assert result not in deadline.federal_holidays(2026)
    assert result.weekday() < 5


def test_business_days_after_rejects_negative_days() -> None:
    import pytest

    with pytest.raises(ValueError, match="must not be negative"):
        deadline.business_days_after(dt.date(2026, 1, 1), -1)


def test_federal_holidays_shifts_a_saturday_holiday_to_friday() -> None:
    # 2027-01-01 (New Year's Day) is a Friday -- no shift needed, so pick a year where it lands
    # on Saturday: 2028-01-01 is a Saturday, observed Friday 2027-12-31.
    holidays = deadline.federal_holidays(2028)
    assert dt.date(2027, 12, 31) in holidays
    assert dt.date(2028, 1, 1) not in holidays


def test_federal_holidays_shifts_a_sunday_holiday_to_monday() -> None:
    # 2028-06-19 (Juneteenth) is a Monday; find a year it falls on Sunday instead: 2033-06-19.
    holidays = deadline.federal_holidays(2033)
    assert dt.date(2033, 6, 19).weekday() == 6  # Sunday, confirms the test picked the right year
    assert dt.date(2033, 6, 20) in holidays
    assert dt.date(2033, 6, 19) not in holidays


def test_is_business_day_false_on_weekend_and_holiday() -> None:
    assert not deadline.is_business_day(dt.date(2026, 9, 12))  # Saturday
    assert not deadline.is_business_day(dt.date(2026, 1, 1))  # New Year's Day, a Thursday
    assert deadline.is_business_day(dt.date(2026, 9, 14))  # an ordinary Monday


def test_from_epoch_returns_an_aware_utc_datetime() -> None:
    result = deadline.from_epoch(1_800_000_000)
    assert result.tzinfo is dt.UTC
    assert result == dt.datetime.fromtimestamp(1_800_000_000, tz=dt.UTC)


def test_promised_day_returns_none_without_a_readable_promise() -> None:
    assert deadline.promised_day(None) is None
    assert deadline.promised_day("garbage") is None


def test_deadline_date_uses_los_angeles_zone() -> None:
    # 2026-09-11 07:30 UTC is still 2026-09-11 00:30 in America/Los_Angeles (UTC-7 in September).
    checkout = dt.datetime(2026, 9, 11, 7, 30, tzinfo=dt.UTC)
    assert deadline.deadline_date(checkout) == dt.date(2026, 9, 15)


def test_deadline_date_accepts_a_naive_datetime() -> None:
    naive = dt.datetime(2026, 9, 11, 12, 0)
    assert deadline.deadline_date(naive) == dt.date(2026, 9, 15)


def test_spoken_date_has_no_year_or_zero_padding() -> None:
    assert deadline.spoken_date(dt.date(2026, 9, 5)) == "Saturday 5 September"


def test_promise_sentence_states_the_refund_commitment() -> None:
    sentence = deadline.promise_sentence(dt.datetime(2026, 9, 11, tzinfo=dt.UTC))
    assert "promised by" in sentence
    assert "refunded" in sentence


def test_from_epoch_and_deadline_epoch_round_trip_to_the_promised_day() -> None:
    checkout = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.UTC)
    epoch = deadline.deadline_epoch(checkout)
    assert deadline.promised_day(epoch) == deadline.deadline_date(checkout)


def test_promised_epoch_accepts_the_dynamodb_decimal_shape() -> None:
    """Negative control: a plain ``isinstance(x, int | float)`` check refuses a Decimal, which
    is exactly what boto3's resource layer hands back for a stored int. See the module's own
    docstring for the incident this guards."""
    stored = decimal.Decimal("1799999999")
    assert deadline.promised_epoch(stored) == 1799999999.0


def test_promised_epoch_rejects_a_bool_even_though_bool_is_an_int() -> None:
    assert deadline.promised_epoch(True) is None
    assert deadline.promised_epoch(False) is None


def test_promised_epoch_accepts_a_numeric_string() -> None:
    assert deadline.promised_epoch("1799999999") == 1799999999.0


def test_promised_epoch_rejects_unreadable_values() -> None:
    assert deadline.promised_epoch(None) is None
    assert deadline.promised_epoch("") is None
    assert deadline.promised_epoch("not a number") is None
    assert deadline.promised_epoch(float("nan")) is None


def test_is_breached_false_when_archive_present_even_past_the_promise() -> None:
    past_epoch = 1
    assert not deadline.is_breached(past_epoch, now=dt.datetime.now(dt.UTC), archive_present=True)


def test_is_breached_true_when_past_promise_with_no_archive() -> None:
    checkout = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    epoch = deadline.deadline_epoch(checkout)
    assert deadline.is_breached(epoch, now=dt.datetime.now(dt.UTC), archive_present=False)


def test_is_breached_false_when_no_promise_was_ever_made() -> None:
    assert not deadline.is_breached(None, now=dt.datetime.now(dt.UTC), archive_present=False)


def test_days_late_is_none_without_a_promise() -> None:
    assert deadline.days_late(None, now=dt.datetime.now(dt.UTC)) is None


def test_days_late_is_positive_after_the_promise() -> None:
    checkout = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    epoch = deadline.deadline_epoch(checkout)
    late = deadline.days_late(epoch, now=dt.datetime.now(dt.UTC))
    assert late is not None
    assert late > 0
