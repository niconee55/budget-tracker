from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
SYNC_DIR = ROOT / "google_sync"
SYNC_STATE_FILE = SYNC_DIR / "run_state.json"
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


def load_google_sync_state() -> dict[str, object]:
    if not SYNC_STATE_FILE.exists():
        return {}
    return json.loads(SYNC_STATE_FILE.read_text())


def save_google_sync_state(state: dict[str, object]) -> None:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _apply_env_overrides(config: dict[str, object]) -> dict[str, object]:
    paths = dict(config.get("paths", {}))
    gmail = dict(config.get("gmail", {}))
    sheets = dict(config.get("sheets", {}))
    state = dict(config.get("state", {}))

    _set_if_env(paths, "credentials_file", "BUDGET_TRACKER_CREDENTIALS_FILE")
    _set_if_env(paths, "token_file", "BUDGET_TRACKER_TOKEN_FILE")
    _set_if_env(paths, "state_file", "BUDGET_TRACKER_STATE_FILE")
    _set_if_env(paths, "latest_scrape_csv", "BUDGET_TRACKER_LATEST_SCRAPE_CSV")
    _set_if_env(gmail, "max_results", "BUDGET_TRACKER_GMAIL_MAX_RESULTS", cast=int)
    _set_if_env(sheets, "spreadsheet_id", "BUDGET_TRACKER_SPREADSHEET_ID")
    _set_if_env(sheets, "sheet_name", "BUDGET_TRACKER_SHEET_NAME")
    _set_if_env(sheets, "summary_sheet_name", "BUDGET_TRACKER_SUMMARY_SHEET_NAME")
    _set_if_env(state, "run_state_file", "BUDGET_TRACKER_STATE_FILE")

    config["paths"] = paths
    config["gmail"] = gmail
    config["sheets"] = sheets
    config["state"] = state
    return config


def _set_if_env(target: dict[str, object], key: str, env_name: str, cast=None) -> None:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return
    target[key] = cast(raw) if cast else raw
