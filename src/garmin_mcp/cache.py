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
from typing import Any, Callable, Dict, List, Optional, Tuple

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


# Marker stored for a day that was live-fetched and confirmed to have no
# usable data (curate() returned None), so future queries don't keep
# re-fetching it forever — without this, a stable day with genuinely no data
# (e.g. before a device was worn) would never satisfy missing_dates() and
# would be retried on every single call.
NO_DATA_KEY = "__no_data__"


def _is_no_data(payload: Dict[str, Any]) -> bool:
    return bool(payload.get(NO_DATA_KEY))


def resolve_range(
    metric: str,
    start_date: str,
    end_date: str,
    fetch: Callable[[str], Any],
    curate: Callable[[Any, str], Optional[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Resolve a date range for a metric, serving stable days from cache and
    live-fetching (then caching) the rest.

    ``fetch(date_str)`` should call the Garmin client for that single day;
    ``curate(raw_data, date_str)`` should extract the small curated dict to
    store/return, or None if there's nothing usable for that day. Trend tools
    should catch per-day exceptions from `fetch` themselves if they want a
    specific policy — resolve_range treats any exception from `fetch` or
    `curate` as "no data for this day" and moves on, matching the existing
    trend tools' "skip days with no data" behavior. A confirmed-empty day is
    still cached (as a no-data marker) so it isn't live-refetched forever.

    Returns (trend, cache_hits, live_fetches) where trend is sorted by date
    and excludes no-data markers.
    """
    missing = missing_dates(metric, start_date, end_date)
    missing_set = set(missing)
    cached_entries = get_range(metric, start_date, end_date)
    stable_cached = {d: v for d, v in cached_entries.items() if d not in missing_set}
    cache_hits = len(stable_cached)
    entries: Dict[str, Dict[str, Any]] = {
        d: v for d, v in stable_cached.items() if not _is_no_data(v)
    }

    live_fetches = 0
    for date_str in missing:
        live_fetches += 1
        try:
            data = fetch(date_str)
            entry = curate(data, date_str)
            if entry:
                entries[date_str] = entry
                store_day(metric, date_str, entry)
            else:
                store_day(metric, date_str, {"date": date_str, NO_DATA_KEY: True})
        except Exception:
            pass

    trend = [entries[d] for d in sorted(entries)]
    return trend, cache_hits, live_fetches
