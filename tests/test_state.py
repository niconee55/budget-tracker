from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from budget_tracker.state import SyncState, iso_to_epoch_millis, load_sync_state, save_sync_state


class SyncStateTests(unittest.TestCase):
    def test_state_round_trips_and_preserves_last_successful_run_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "run_state.json"
            state = SyncState(
                last_successful_run_utc="2026-03-27T15:04:05+00:00",
                most_recent_run_utc="2026-03-27T15:04:05+00:00",
                previous_run_utc="2026-03-26T12:00:00+00:00",
            )

            save_sync_state(state_file, state)

            self.assertEqual(load_sync_state(state_file), state)
            expected_epoch = int(
                datetime(2026, 3, 27, 15, 4, 5, tzinfo=timezone.utc).timestamp() * 1000
            )
            self.assertEqual(iso_to_epoch_millis(state.last_successful_run_utc), expected_epoch)

    def test_save_sync_state_shifts_existing_most_recent_to_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "run_state.json"
            save_sync_state(
                state_file,
                SyncState(last_successful_run_utc="2026-03-27T15:04:05+00:00"),
            )

            save_sync_state(
                state_file,
                SyncState(last_successful_run_utc="2026-03-28T08:09:10+00:00"),
            )

            self.assertEqual(
                load_sync_state(state_file),
                SyncState(
                    last_successful_run_utc="2026-03-28T08:09:10+00:00",
                    most_recent_run_utc="2026-03-28T08:09:10+00:00",
                    previous_run_utc="2026-03-27T15:04:05+00:00",
                ),
            )
