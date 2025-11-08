from datetime import UTC, datetime, timedelta

from ActivityPoller.state_store import clamp_start, end_of_day, midnight_utc, next_day


def test_midnight_and_next_day():
    ts = datetime(2024, 5, 17, 12, 34, 56, tzinfo=UTC)
    assert midnight_utc(ts) == datetime(2024, 5, 17, 0, 0, 0, tzinfo=UTC)
    assert next_day(ts) == datetime(2024, 5, 18, 0, 0, 0, tzinfo=UTC)
    assert end_of_day(ts) == datetime(2024, 5, 18, 0, 0, 0, tzinfo=UTC)


def test_clamp_start_respects_overlap_and_window():
    now = datetime(2024, 5, 29, 10, 0, 0, tzinfo=UTC)
    last_end = datetime(2024, 5, 29, 9, 0, 0, tzinfo=UTC)
    assert clamp_start(now, last_end, overlap=60) == datetime(2024, 5, 29, 8, 59, 0, tzinfo=UTC)

    old_last_end = datetime(2024, 4, 15, 12, 0, 0, tzinfo=UTC)
    clamped = clamp_start(now, old_last_end, overlap=60)
    assert clamped == now - timedelta(days=28)

    assert clamp_start(now, None, overlap=60) == now - timedelta(days=28)

