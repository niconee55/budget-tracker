#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RUN_STATE_PATH = ROOT / "google_sync" / "run_state.json"
CACHE_PATH = ROOT / "google_sync" / "merchant_lookup_cache.json"
LOCAL_TZ = ZoneInfo("America/New_York")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rollback budget sync state to a prior local timestamp and clear the merchant lookup cache."
    )
    parser.add_argument(
        "--local-datetime",
        required=True,
        help="Local New York datetime in 'YYYY-MM-DD HH:MM' or ISO format, for example '2026-03-28 19:42'.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=RUN_STATE_PATH,
        help="Path to run_state.json",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=CACHE_PATH,
        help="Path to merchant_lookup_cache.json",
    )
    return parser


def parse_local_datetime(value: str) -> datetime:
    text = value.strip()
    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def main() -> int:
    args = build_parser().parse_args()
    local_dt = parse_local_datetime(args.local_datetime)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(microsecond=0)

    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.write_text(
        json.dumps(
            {
                "last_successful_run_utc": utc_dt.isoformat(),
                "most_recent_run_utc": utc_dt.isoformat(),
                "previous_run_utc": None,
            },
            indent=2,
        )
    )

    args.cache_file.parent.mkdir(parents=True, exist_ok=True)
    args.cache_file.write_text(json.dumps({}, indent=2, sort_keys=True))

    print(
        json.dumps(
            {
                "state_file": str(args.state_file),
                "cache_file": str(args.cache_file),
                "local_datetime": local_dt.isoformat(),
                "utc_datetime": utc_dt.isoformat(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
