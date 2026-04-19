from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
SYNC_DIR = ROOT / "google_sync"
SYNC_DB_FILE = SYNC_DIR / "budget_sync.db"
LOOKUP_CACHE_FILE = SYNC_DIR / "merchant_lookup_cache.json"


@lru_cache(maxsize=None)
def load_email_sources() -> dict[str, object]:
    return json.loads((CONFIG_DIR / "email_sources.json").read_text())


@lru_cache(maxsize=None)
def load_merchant_knowledge() -> dict[str, object]:
    return json.loads((CONFIG_DIR / "merchant_knowledge.json").read_text())


@lru_cache(maxsize=None)
def load_google_sync_config() -> dict[str, object]:
    config = json.loads((ROOT / "google_sync" / "settings.json").read_text())
    return _apply_env_overrides(config)


def _apply_env_overrides(config: dict[str, object]) -> dict[str, object]:
    paths = dict(config.get("paths", {}))
    gmail = dict(config.get("gmail", {}))
    sheets = dict(config.get("sheets", {}))
    paths.setdefault("db_file", str(SYNC_DB_FILE))
    _set_if_env(paths, "credentials_file", "BUDGET_TRACKER_CREDENTIALS_FILE")
    _set_if_env(paths, "token_file", "BUDGET_TRACKER_TOKEN_FILE")
    _set_if_env(paths, "db_file", "BUDGET_TRACKER_DB_FILE")
    _set_if_env(paths, "latest_scrape_csv", "BUDGET_TRACKER_LATEST_SCRAPE_CSV")
    _set_if_env(gmail, "max_results", "BUDGET_TRACKER_GMAIL_MAX_RESULTS", cast=int)
    _set_if_env(sheets, "spreadsheet_id", "BUDGET_TRACKER_SPREADSHEET_ID")
    _set_if_env(sheets, "sheet_name", "BUDGET_TRACKER_SHEET_NAME")
    _set_if_env(sheets, "summary_sheet_name", "BUDGET_TRACKER_SUMMARY_SHEET_NAME")

    config["paths"] = paths
    config["gmail"] = gmail
    config["sheets"] = sheets
    return config


def _set_if_env(target: dict[str, object], key: str, env_name: str, cast=None) -> None:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return
    target[key] = cast(raw) if cast else raw
