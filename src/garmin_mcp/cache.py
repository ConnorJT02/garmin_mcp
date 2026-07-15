"""Local SQLite cache for long-range Garmin trend data.

Garmin's API has no native weekly/monthly rollup for metrics like HRV,
training load, VO2 max, or respiration (unlike steps/stress, which do have
`get_weekly_*` aggregate endpoints) — trend tools for those metrics have to
fetch one day at a time. This cache lets each day be fetched from Garmin
once and reused for every future query, so long ranges (e.g. 2 years) don't
mean re-fetching the same historical days on every call.
"""

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# Days more recent than this are always fetched live, never trusted from
# cache, since Garmin can revise recent sync data (e.g. a delayed sync).
FRESHNESS_WINDOW_DAYS = 2

_conn: Optional[sqlite3.Connection] = None


def get_cache_path() -> str:
    """Get cache DB path from environment or default."""
    return os.getenv("GARMIN_CACHE_PATH") or "~/.garmin_mcp_cache.db"


def configure(db_path: Optional[str] = None) -> None:
    """Open (creating if needed) the cache database and ensure its schema exists."""
    global _conn
    path = Path(os.path.expanduser(db_path or get_cache_path()))
    path.parent.mkdir(parents=True, exist_ok=True)

    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_metrics (
            metric     TEXT NOT NULL,
            date       TEXT NOT NULL,
            payload    TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (metric, date)
        )
        """
    )
    _conn.commit()


def close() -> None:
    """Close the cache database connection, if open."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _require_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("cache.configure() must be called before using the cache")
    return _conn


def _stable_cutoff() -> datetime.date:
    """Last date that's old enough to be treated as stable/cacheable."""
    return datetime.date.today() - datetime.timedelta(days=FRESHNESS_WINDOW_DAYS)


def _date_range(start_date: str, end_date: str) -> List[str]:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += datetime.timedelta(days=1)
    return days


def get_range(metric: str, start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    """Return cached {date: payload} entries for a metric within [start_date, end_date]."""
    conn = _require_conn()
    rows = conn.execute(
        "SELECT date, payload FROM daily_metrics WHERE metric = ? AND date >= ? AND date <= ?",
        (metric, start_date, end_date),
    ).fetchall()
    return {date: json.loads(payload) for date, payload in rows}


def store_day(metric: str, date: str, payload: Dict[str, Any]) -> None:
    """Cache a single day's curated payload for a metric."""
    conn = _require_conn()
    conn.execute(
        """
        INSERT INTO daily_metrics (metric, date, payload, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(metric, date) DO UPDATE SET
            payload = excluded.payload,
            fetched_at = excluded.fetched_at
        """,
        (metric, date, json.dumps(payload), datetime.datetime.now().isoformat()),
    )
    conn.commit()


def missing_dates(metric: str, start_date: str, end_date: str) -> List[str]:
    """Dates in range that still need a live fetch: not cached, or too recent to trust the cache."""
    cutoff = _stable_cutoff()
    cached = get_range(metric, start_date, end_date)
    result = []
    for date_str in _date_range(start_date, end_date):
        date = datetime.date.fromisoformat(date_str)
        if date > cutoff or date_str not in cached:
            result.append(date_str)
    return result
