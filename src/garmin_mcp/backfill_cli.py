"""Backfill CLI for the Garmin MCP local trend cache.

Populates the local SQLite cache (see cache.py) with historical daily data
so long-range trend tools (e.g. get_hrv_trend) can serve 2-year queries from
cache instead of making hundreds of live Garmin API calls on every request.

Uses the same saved OAuth tokens as the MCP server itself (see auth_cli.py) —
run 'garmin-mcp-auth' first if you haven't already.
"""

import argparse
import datetime
import sys
import time

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import cache
from garmin_mcp.token_utils import get_token_path, token_exists
from garmin_mcp.health_wellness import _curate_heart_rate_day, _curate_sleep_day
from garmin_mcp.training import (
    _curate_hrv_day,
    _curate_respiration_day,
    _curate_training_load_day,
    _curate_vo2max_day,
)

# metric name -> (garmin client method name, curation function)
_METRICS = {
    "hrv": ("get_hrv_data", _curate_hrv_day),
    "training_load": ("get_training_status", _curate_training_load_day),
    "vo2max": ("get_training_status", _curate_vo2max_day),
    "respiration": ("get_respiration_data", _curate_respiration_day),
    "sleep": ("get_sleep_data", _curate_sleep_day),
    "heart_rate": ("get_heart_rates", _curate_heart_rate_day),
}


def backfill(
    metric: str,
    start_date: str,
    end_date: str,
    token_path: str,
    pace: float,
    is_cn: bool = False,
) -> bool:
    """Fetch and cache every not-yet-cached, stable day for a metric in [start_date, end_date].

    Returns:
        bool: True if the backfill ran to completion (individual day failures
        are logged and skipped, not fatal), False on an unrecoverable error
        (e.g. bad/expired tokens).
    """
    if not token_exists(token_path):
        print(f"\n✗ No saved tokens found at: {token_path}", file=sys.stderr)
        print("  Run 'garmin-mcp-auth' first to authenticate.", file=sys.stderr)
        return False

    method_name, curate = _METRICS[metric]

    print(f"\nLogging in with saved tokens from '{token_path}'...")
    try:
        garmin = Garmin(is_cn=is_cn)
        garmin.login(token_path)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        print(f"\n✗ Login failed: {e}", file=sys.stderr)
        print("  Run 'garmin-mcp-auth --force-reauth' to refresh your tokens.", file=sys.stderr)
        return False

    missing = cache.missing_dates(metric, start_date, end_date)
    if not missing:
        print(f"\nNothing to do — every day in {start_date}..{end_date} is already cached.")
        return True

    print(f"\nBacking up '{metric}': {len(missing)} day(s) to fetch (of {start_date}..{end_date}).")
    print(f"Pace: {pace}s between requests. Press Ctrl+C to stop — already-cached days are safe to skip on resume.\n")

    fetch = getattr(garmin, method_name)
    fetched = 0
    skipped = 0
    for i, date_str in enumerate(missing, start=1):
        try:
            data = fetch(date_str)
            entry = curate(data, date_str)
            if entry:
                cache.store_day(metric, date_str, entry)
                fetched += 1
            else:
                # Cache a no-data marker too, so this confirmed-empty day
                # isn't live-refetched on every future trend query.
                cache.store_day(metric, date_str, {"date": date_str, cache.NO_DATA_KEY: True})
                skipped += 1
        except GarminConnectTooManyRequestsError:
            print(f"\n✗ Rate limited by Garmin after {fetched} day(s). Wait a while and re-run to resume.", file=sys.stderr)
            return False
        except Exception as e:
            print(f"  {date_str}: skipped ({e})", file=sys.stderr)
            skipped += 1

        if i % 10 == 0 or i == len(missing):
            print(f"  {i}/{len(missing)} processed ({fetched} cached, {skipped} skipped)")

        if i < len(missing):
            time.sleep(pace)

    print(f"\n✓ Backfill complete: {fetched} day(s) cached, {skipped} skipped, at {cache.get_cache_path()}")
    return True


def main():
    """Main entry point for the backfill CLI tool."""
    # Progress output uses non-ASCII characters (checkmarks); stdout can default
    # to a legacy codepage on Windows when not attached to an interactive
    # console (e.g. when piped), so force UTF-8 rather than let it crash mid-run.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Backfill the local Garmin MCP trend cache with historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available metrics: {", ".join(sorted(_METRICS))}

Examples:
  # Backfill 2 years of HRV data ending today
  garmin-mcp-backfill --metric hrv

  # Backfill a specific, smaller range first
  garmin-mcp-backfill --metric sleep --days 14

  # Go easier on Garmin's API (default is 1.5s between requests)
  garmin-mcp-backfill --metric heart_rate --pace 3
        """
    )

    parser.add_argument(
        "--metric",
        type=str,
        default="hrv",
        choices=sorted(_METRICS),
        help="Metric to backfill (default: hrv)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="Number of days to backfill, ending at --end-date (default: 730, ~2 years)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format (default: today)"
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=1.5,
        help="Seconds to wait between Garmin API requests (default: 1.5)"
    )
    parser.add_argument(
        "--token-path",
        type=str,
        default=None,
        help="Custom token storage directory (default: ~/.garminconnect or $GARMINTOKENS)"
    )
    parser.add_argument(
        "--is-cn",
        action="store_true",
        default=False,
        help="Use Garmin Connect China (garmin.cn) instead of the international version"
    )

    args = parser.parse_args()

    end = datetime.date.fromisoformat(args.end_date) if args.end_date else datetime.date.today()
    start = end - datetime.timedelta(days=args.days - 1)
    token_path = args.token_path or get_token_path()

    cache.configure()

    print("\n" + "=" * 60)
    print("Garmin MCP Cache Backfill")
    print("=" * 60)

    success = backfill(
        args.metric,
        start.isoformat(),
        end.isoformat(),
        token_path,
        args.pace,
        args.is_cn,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
