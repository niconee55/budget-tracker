from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class SyncState:
    last_successful_run_utc: str | None = None
    most_recent_run_utc: str | None = None
    previous_run_utc: str | None = None


def load_sync_state(path: Path) -> SyncState:
    resolved = path.expanduser()
    if not resolved.exists():
        return SyncState()
    payload = json.loads(resolved.read_text())
    most_recent = payload.get("most_recent_run_utc") or payload.get("last_successful_run_utc")
    previous = payload.get("previous_run_utc")
    return SyncState(
        last_successful_run_utc=most_recent,
        most_recent_run_utc=most_recent,
        previous_run_utc=previous,
    )


def save_sync_state(path: Path, state: SyncState) -> None:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    existing = load_sync_state(resolved) if resolved.exists() else SyncState()
    most_recent = state.most_recent_run_utc or state.last_successful_run_utc
    previous = state.previous_run_utc
    if previous is None and most_recent and existing.most_recent_run_utc and existing.most_recent_run_utc != most_recent:
        previous = existing.most_recent_run_utc
    if previous is None:
        previous = existing.previous_run_utc
    resolved.write_text(
        json.dumps(
            {
                "last_successful_run_utc": most_recent,
                "most_recent_run_utc": most_recent,
                "previous_run_utc": previous,
            },
            indent=2,
        )
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso_to_epoch_millis(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(value).timestamp() * 1000)
