#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from budget_tracker.db import BudgetDatabase

DB_PATH = ROOT / "google_sync" / "budget_sync.db"
CACHE_PATH = ROOT / "google_sync" / "merchant_lookup_cache.json"
LOCAL_TZ = ZoneInfo("America/New_York")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set the active budget-sync baseline to the most recent scrape run at or before a local timestamp."
    )
    parser.add_argument(
        "--local-datetime",
        required=True,
        help="Local New York datetime in 'YYYY-MM-DD HH:MM' or ISO format, for example '2026-03-28 19:42'.",
    )
    parser.add_argument(
        "--db-file",
        "--state-file",
        dest="db_file",
        type=Path,
        default=DB_PATH,
        help="Path to budget_sync.db",
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

    db = BudgetDatabase(args.db_file)
    db.ensure_schema()
    selected_run = db.get_scrape_run_at_or_before(utc_dt.isoformat())
    if selected_run is None:
        raise SystemExit(f"no scrape run exists at or before {utc_dt.isoformat()}")
    db.set_active_baseline_run(selected_run.id)

    args.cache_file.parent.mkdir(parents=True, exist_ok=True)
    args.cache_file.write_text(json.dumps({}, indent=2, sort_keys=True))

    print(
        json.dumps(
            {
                "db_file": str(args.db_file),
                "cache_file": str(args.cache_file),
                "local_datetime": local_dt.isoformat(),
                "utc_datetime": utc_dt.isoformat(),
                "selected_scrape_run": {
                    "id": selected_run.id,
                    "started_at_utc": selected_run.started_at_utc,
                    "completed_at_utc": selected_run.completed_at_utc,
                    "status": selected_run.status,
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
