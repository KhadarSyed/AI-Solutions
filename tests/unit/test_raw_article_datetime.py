"""RawArticle date/time split — time only when the source provides it (never fabricated)."""
from datetime import date, datetime

from app.tools.connectors.base import RawArticle


def test_full_timestamp_splits_into_date_and_time():
    a = RawArticle(title="t", url="https://x/y",
                   published_at=datetime(2026, 7, 10, 9, 15))
    assert a.published_date == date(2026, 7, 10)
    assert a.published_time == "09:15"


def test_date_only_leaves_time_blank():
    a = RawArticle(title="t", url="https://x/y", published_date=date(2026, 7, 10))
    assert a.published_date == date(2026, 7, 10)
    assert a.published_time == ""   # never fabricated


def test_no_dates_all_empty():
    a = RawArticle(title="t", url="https://x/y")
    assert a.published_date is None and a.published_time == ""
