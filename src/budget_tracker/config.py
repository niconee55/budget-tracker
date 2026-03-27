from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@lru_cache(maxsize=None)
def load_email_sources() -> dict[str, object]:
    return json.loads((CONFIG_DIR / "email_sources.json").read_text())


@lru_cache(maxsize=None)
def load_merchant_knowledge() -> dict[str, object]:
    return json.loads((CONFIG_DIR / "merchant_knowledge.json").read_text())


@lru_cache(maxsize=None)
def load_google_sync_config() -> dict[str, object]:
    return json.loads((ROOT / "google_sync" / "settings.json").read_text())
