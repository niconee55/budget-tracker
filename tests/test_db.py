from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from budget_tracker.db import BudgetDatabase
from budget_tracker.models import Transaction


ROOT = Path(__file__).resolve().parents[1]


class BudgetDatabaseTests(unittest.TestCase):
    def test_ensure_schema_creates_expected_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = BudgetDatabase(Path(tmpdir) / "budget_sync.db")
            db.ensure_schema()

            with db.connect() as conn:
                table_names = {
                    row["name"]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }

            self.assertIn("scrape_runs", table_names)
            self.assertIn("transactions", table_names)

    def test_record_transactions_deduplicates_gmail_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = BudgetDatabase(Path(tmpdir) / "budget_sync.db")
            db.ensure_schema()
            first_run = self._record_completed_run(db, "2026-03-26T18:15:00+00:00", baseline=True)
            second_run = self._record_completed_run(db, "2026-03-27T18:15:00+00:00", baseline=False)
            transaction = Transaction(
                amount=Decimal("18.79"),
                date="2026-03-12",
                merchant="Whole Foods Market",
                category="Groceries",
                source_file="gmail:msg-1",
                raw_snippet="sample",
                source_name="capital_one",
            )

            inserted_first = db.record_transactions(first_run, [transaction], inserted_at_utc="2026-03-26T18:15:00+00:00")
            inserted_second = db.record_transactions(second_run, [transaction], inserted_at_utc="2026-03-27T18:15:00+00:00")

            self.assertEqual(inserted_first, 1)
            self.assertEqual(inserted_second, 0)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)

    def test_get_scrape_run_at_or_before_ignores_dry_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = BudgetDatabase(Path(tmpdir) / "budget_sync.db")
            db.ensure_schema()
            completed_run_id = self._record_completed_run(db, "2026-03-28T23:42:00+00:00", baseline=True)
            dry_run_id = db.record_scrape_run(
                started_at_utc="2026-03-29T01:00:00+00:00",
                status="dry_run",
                gmail_query="from:test@example.com",
                max_results=10,
            )
            db.finalize_scrape_run(
                dry_run_id,
                completed_at_utc="2026-03-29T01:05:00+00:00",
                status="dry_run",
                last_seen_internal_date_ms=222,
                is_baseline=False,
            )

            run = db.get_scrape_run_at_or_before("2026-03-29T02:00:00+00:00")

            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(run.id, completed_run_id)
            self.assertEqual(run.status, "completed")

    def test_rollback_script_sets_active_baseline_from_db_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "budget_sync.db"
            cache_file = Path(tmpdir) / "merchant_lookup_cache.json"
            db = BudgetDatabase(db_file)
            db.ensure_schema()
            early_run_id = self._record_completed_run(db, "2026-03-28T23:42:00+00:00", baseline=False)
            self._record_completed_run(db, "2026-04-01T21:43:41+00:00", baseline=True)

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "rollback_budget_state.py"),
                    "--local-datetime",
                    "2026-03-28 19:42",
                    "--db-file",
                    str(db_file),
                    "--cache-file",
                    str(cache_file),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["selected_scrape_run"]["id"], early_run_id)
            active_baseline = db.get_active_baseline_run()
            self.assertIsNotNone(active_baseline)
            assert active_baseline is not None
            self.assertEqual(active_baseline.id, early_run_id)
            self.assertEqual(cache_file.read_text().strip(), "{}")

    def _record_completed_run(self, db: BudgetDatabase, started_at: str, *, baseline: bool) -> int:
        run_id = db.record_scrape_run(
            started_at_utc=started_at,
            status="started",
            gmail_query="from:test@example.com",
            max_results=10,
        )
        db.finalize_scrape_run(
            run_id,
            completed_at_utc=started_at,
            status="completed",
            last_seen_internal_date_ms=111,
            is_baseline=baseline,
        )
        return run_id
