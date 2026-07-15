"""Unit tests for the cache module."""

import datetime
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from garmin_mcp import cache


@pytest.fixture
def temp_cache():
    """Configure the cache against a fresh temp DB file for the duration of a test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_cache.db")
        cache.configure(db_path)
        try:
            yield db_path
        finally:
            cache.close()


class TestGetCachePath:
    """Tests for get_cache_path function."""

    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_CACHE_PATH", None)
            assert cache.get_cache_path() == "~/.garmin_mcp_cache.db"

    def test_env_var_path(self):
        with patch.dict(os.environ, {"GARMIN_CACHE_PATH": "/custom/cache.db"}):
            assert cache.get_cache_path() == "/custom/cache.db"


class TestConfigure:
    """Tests for configure function."""

    def test_creates_db_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "nested", "cache.db")
            cache.configure(db_path)
            try:
                assert Path(db_path).exists()
            finally:
                cache.close()

    def test_expands_user_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HOME": tmpdir, "USERPROFILE": tmpdir}):
                cache.configure("~/cache_from_env.db")
                try:
                    assert (Path(tmpdir) / "cache_from_env.db").exists()
                finally:
                    cache.close()


class TestStoreAndGetRange:
    """Tests for store_day and get_range."""

    def test_roundtrip(self, temp_cache):
        cache.store_day("hrv", "2026-01-01", {"date": "2026-01-01", "last_night_avg_hrv_ms": 55.0})
        result = cache.get_range("hrv", "2026-01-01", "2026-01-01")
        assert result == {"2026-01-01": {"date": "2026-01-01", "last_night_avg_hrv_ms": 55.0}}

    def test_get_range_empty_when_nothing_cached(self, temp_cache):
        assert cache.get_range("hrv", "2026-01-01", "2026-01-31") == {}

    def test_get_range_filters_to_bounds(self, temp_cache):
        cache.store_day("hrv", "2026-01-01", {"date": "2026-01-01"})
        cache.store_day("hrv", "2026-01-15", {"date": "2026-01-15"})
        cache.store_day("hrv", "2026-02-01", {"date": "2026-02-01"})
        result = cache.get_range("hrv", "2026-01-01", "2026-01-31")
        assert set(result) == {"2026-01-01", "2026-01-15"}

    def test_get_range_scoped_to_metric(self, temp_cache):
        cache.store_day("hrv", "2026-01-01", {"date": "2026-01-01", "value": "hrv"})
        cache.store_day("training_load", "2026-01-01", {"date": "2026-01-01", "value": "load"})
        result = cache.get_range("hrv", "2026-01-01", "2026-01-01")
        assert result["2026-01-01"]["value"] == "hrv"

    def test_store_day_upserts(self, temp_cache):
        cache.store_day("hrv", "2026-01-01", {"date": "2026-01-01", "last_night_avg_hrv_ms": 50.0})
        cache.store_day("hrv", "2026-01-01", {"date": "2026-01-01", "last_night_avg_hrv_ms": 60.0})
        result = cache.get_range("hrv", "2026-01-01", "2026-01-01")
        assert result["2026-01-01"]["last_night_avg_hrv_ms"] == 60.0

    def test_requires_configure_first(self):
        cache._conn = None
        with pytest.raises(RuntimeError):
            cache.get_range("hrv", "2026-01-01", "2026-01-01")


class TestMissingDates:
    """Tests for missing_dates, including the freshness window."""

    def test_all_missing_when_nothing_cached(self, temp_cache):
        result = cache.missing_dates("hrv", "2026-01-01", "2026-01-03")
        assert result == ["2026-01-01", "2026-01-02", "2026-01-03"]

    def test_stable_cached_day_is_not_missing(self, temp_cache):
        stable_date = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        cache.store_day("hrv", stable_date, {"date": stable_date})
        assert cache.missing_dates("hrv", stable_date, stable_date) == []

    def test_recent_day_always_missing_even_if_cached(self, temp_cache):
        """Days within the freshness window are always re-fetched, since Garmin
        can revise recent sync data — a cached entry shouldn't be trusted."""
        today = datetime.date.today().isoformat()
        cache.store_day("hrv", today, {"date": today})
        assert cache.missing_dates("hrv", today, today) == [today]

    def test_mixed_range(self, temp_cache):
        stable_date = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        uncached_stable_date = (datetime.date.today() - datetime.timedelta(days=9)).isoformat()
        today = datetime.date.today().isoformat()
        cache.store_day("hrv", stable_date, {"date": stable_date})

        result = cache.missing_dates("hrv", stable_date, today)
        assert stable_date not in result
        assert uncached_stable_date in result
        assert today in result
