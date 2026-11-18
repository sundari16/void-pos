from __future__ import annotations

from datetime import UTC, date, datetime

from void_pos.clock import as_aware, is_valid_timezone, local_date


def test_business_date_follows_the_location_not_utc() -> None:
    # 22:30 UTC on the 17th is 08:30 on the 18th in Brisbane (UTC+10, no DST).
    late_utc = datetime(2026, 11, 17, 22, 30, tzinfo=UTC)
    assert local_date("Australia/Brisbane", late_utc) == date(2026, 11, 18)
    assert local_date("UTC", late_utc) == date(2026, 11, 17)


def test_naive_times_are_read_as_utc() -> None:
    assert as_aware(datetime(2026, 1, 1, 12, 0)) == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_timezone_names_are_checked() -> None:
    assert is_valid_timezone("Australia/Brisbane")
    assert not is_valid_timezone("Mars/Base")
    assert not is_valid_timezone("../etc/passwd")
