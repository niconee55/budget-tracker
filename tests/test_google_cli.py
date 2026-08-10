from __future__ import annotations

import argparse
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from budget_tracker.db import BudgetDatabase
from budget_tracker.gmail_sync import GmailSyncResult, SkippedMessage
from budget_tracker.google_cli import _default_query, _validate_args, main
from budget_tracker.models import Transaction
from budget_tracker.parser import ParsedEmail


class GoogleCliValidationTests(unittest.TestCase):
    def test_rejects_non_positive_max_results(self) -> None:
        args = argparse.Namespace(
            max_results=0,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(str(exc.exception), "--max-results must be greater than 0")

    def test_rejects_spreadsheet_id_without_sheet_name(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id="sheet-123",
            sheet_name=None,
            summary_sheet_name=None,
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(str(exc.exception), "--spreadsheet-id and --sheet-name must be provided together")

    def test_rejects_missing_output_targets(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(str(exc.exception), "provide at least one output target: local files, sheet sync, or --dry-run")

    def test_rejects_summary_sheet_without_primary_sheet_target(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name="Summary",
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(str(exc.exception), "--summary-sheet-name requires --spreadsheet-id and --sheet-name")

    def test_allows_dry_run_without_other_output_targets(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            dry_run=True,
        )

        self.assertIsNone(_validate_args(args))

    def test_rejects_trip_mode_without_trip_spreadsheet_id(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            trip_sheet_name="Taiwan/Vietnam 2026",
            trip_spreadsheet_id=None,
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(str(exc.exception), "--trip-spreadsheet-id is required with --trip")

    def test_rejects_summary_csv_in_trip_mode(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=Path("summary.csv"),
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            trip_sheet_name="Taiwan/Vietnam 2026",
            trip_spreadsheet_id="trip-spreadsheet-123",
            dry_run=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(
            str(exc.exception),
            "--summary-csv is not supported with --trip because trip mode skips categorization",
        )


class GoogleCliMainTests(unittest.TestCase):
    def test_main_records_completed_run_and_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "budget_sync.db"
            output_json = root / "transactions.json"
            summary_csv = root / "summary.csv"
            latest_scrape_csv = root / "latest_scrape.csv"
            self._seed_completed_baseline(db_file, started_at="2026-03-26T18:15:00+00:00", internal_date_ms=1234)
            args = self._args(
                db_file=db_file,
                output_json=output_json,
                summary_csv=summary_csv,
                query="from:alerts@capitalone.com",
                dry_run=False,
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=["msg-2"],
                skipped_messages=[
                    SkippedMessage(
                        message_id="msg-2",
                        subject="Noise",
                        sender="x@example.com",
                        reason="Message did not match a tracked transaction template.",
                    )
                ],
                max_internal_date_ms=4567,
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli._latest_scrape_csv_path", return_value=latest_scrape_csv),
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()) as load_credentials,
                patch("budget_tracker.google_cli.build_google_service", return_value=object()) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
                patch("budget_tracker.google_cli.utc_now_iso", side_effect=["2026-03-27T00:00:00+00:00", "2026-03-27T00:05:00+00:00"]),
            ):
                build_parser.return_value.parse_args.return_value = args
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_json.exists())
            self.assertTrue(summary_csv.exists())
            self.assertTrue(latest_scrape_csv.exists())
            self.assertTrue(stdout.getvalue().startswith("Budget last updated: March 26, 2026 2:15 PM EDT"))
            self.assertIn("Skipped messages:", stdout.getvalue())
            self.assertIn("Noise", stdout.getvalue())
            self.assertIn("x@example.com", stdout.getvalue())
            self.assertIn("Message did not match a tracked transaction template.", stdout.getvalue())
            load_credentials.assert_called_once_with(args.credentials_file, args.token_file)
            build_service.assert_called_once_with("gmail", "v1", load_credentials.return_value)
            self.assertEqual(fetch_transactions.call_args.kwargs["since_internal_date_ms"], 1234)

            db = BudgetDatabase(db_file)
            active_baseline = db.get_active_baseline_run()
            self.assertIsNotNone(active_baseline)
            assert active_baseline is not None
            self.assertEqual(active_baseline.started_at_utc, "2026-03-27T00:00:00+00:00")
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0], 2)
                row = conn.execute(
                    "SELECT gmail_message_id, category, scrape_run_id FROM transactions ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertEqual(row["gmail_message_id"], "msg-1")
            self.assertEqual(row["category"], "Groceries")

    def test_main_dry_run_records_rows_without_advancing_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "budget_sync.db"
            self._seed_completed_baseline(db_file, started_at="2026-03-26T18:15:00+00:00", internal_date_ms=1234)
            args = self._args(
                db_file=db_file,
                query="from:capitalone@notification.capitalone.com",
                dry_run=True,
                spreadsheet_id="spreadsheet-123",
                sheet_name="Sheet1",
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=[],
                max_internal_date_ms=4567,
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()) as load_credentials,
                patch("budget_tracker.google_cli.build_google_service", return_value=object()) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
                patch("budget_tracker.google_cli.write_transactions_csv"),
                patch("budget_tracker.google_cli.read_budget_sheet_rows") as read_budget_sheet_rows,
                patch("budget_tracker.google_cli.sync_monthly_budget_sheet") as sync_monthly_budget_sheet,
                patch("budget_tracker.google_cli.utc_now_iso", side_effect=["2026-03-27T00:00:00+00:00", "2026-03-27T00:05:00+00:00"]),
            ):
                build_parser.return_value.parse_args.return_value = args
                exit_code = main()

            self.assertEqual(exit_code, 0)
            load_credentials.assert_called_once_with(args.credentials_file, args.token_file)
            build_service.assert_called_once_with("gmail", "v1", load_credentials.return_value)
            self.assertEqual(fetch_transactions.call_args.kwargs["since_internal_date_ms"], 1234)
            read_budget_sheet_rows.assert_not_called()
            sync_monthly_budget_sheet.assert_not_called()

            db = BudgetDatabase(db_file)
            active_baseline = db.get_active_baseline_run()
            self.assertIsNotNone(active_baseline)
            assert active_baseline is not None
            self.assertEqual(active_baseline.started_at_utc, "2026-03-26T18:15:00+00:00")
            runs = db.list_scrape_runs()
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[-1].status, "dry_run")
            self.assertFalse(runs[-1].is_baseline)
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)

    def test_main_reads_monthly_sheet_and_syncs_specific_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "budget_sync.db"
            self._seed_completed_baseline(db_file, started_at="2026-03-26T12:00:00+00:00", internal_date_ms=222)
            args = self._args(
                db_file=db_file,
                query="from:alerts@capitalone.com",
                dry_run=False,
                spreadsheet_id="spreadsheet-123",
                sheet_name="Transactions",
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=[],
                max_internal_date_ms=333,
            )
            events: list[str] = []
            monthly_rows = [["Month"], ["Expected "], ["March 2026"]]

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
                patch("budget_tracker.google_cli.build_google_service", side_effect=[object(), object()]) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
                patch("budget_tracker.google_cli.read_budget_sheet_rows", side_effect=lambda **kwargs: events.append("read") or monthly_rows),
                patch("budget_tracker.google_cli.sync_monthly_budget_sheet", side_effect=lambda **kwargs: events.append("sync")),
                patch("budget_tracker.google_cli.utc_now_iso", side_effect=["2026-03-27T12:00:00+00:00", "2026-03-27T12:05:00+00:00"]),
                patch("budget_tracker.google_cli.write_transactions_csv"),
            ):
                build_parser.return_value.parse_args.return_value = args
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(build_service.call_count, 2)
            self.assertEqual(events, ["read", "sync"])
            self.assertEqual(fetch_transactions.call_args.kwargs["since_internal_date_ms"], 222)

    def test_main_trip_mode_appends_trip_costs_without_categorizing_or_budget_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "budget_sync.db"
            self._seed_completed_baseline(db_file, started_at="2026-03-26T12:00:00+00:00", internal_date_ms=222)
            args = self._args(
                db_file=db_file,
                query="from:alerts@capitalone.com",
                dry_run=False,
                trip_sheet_name="Taiwan/Vietnam 2026",
                trip_spreadsheet_id="trip-spreadsheet-123",
            )
            result = GmailSyncResult(
                transactions=[
                    ParsedEmail(
                        amount=Decimal("18.79"),
                        date="2026-03-12",
                        merchant="Whole Foods Market",
                        source_file="gmail:msg-1",
                        raw_snippet="Your Capital One card ending in 4242 was charged $18.79 at Whole Foods Market.",
                        source_name="capital_one",
                        account_last4="4242",
                    )
                ],
                skipped_message_ids=[],
                max_internal_date_ms=333,
            )
            gmail_service = object()
            sheets_service = object()

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
                patch("budget_tracker.google_cli.build_google_service", side_effect=[gmail_service, sheets_service]) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
                patch("budget_tracker.google_cli.append_trip_cost_rows") as append_trip_cost_rows,
                patch("budget_tracker.google_cli.read_budget_sheet_rows") as read_budget_sheet_rows,
                patch("budget_tracker.google_cli.sync_monthly_budget_sheet") as sync_monthly_budget_sheet,
                patch("budget_tracker.google_cli.utc_now_iso", side_effect=["2026-03-27T12:00:00+00:00", "2026-03-27T12:05:00+00:00"]),
                patch("budget_tracker.google_cli.write_transactions_csv"),
            ):
                build_parser.return_value.parse_args.return_value = args
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(build_service.call_count, 2)
            self.assertIsNone(fetch_transactions.call_args.kwargs["categorizer"])
            self.assertFalse(fetch_transactions.call_args.kwargs["categorize"])
            append_trip_cost_rows.assert_called_once_with(
                sheets_service=sheets_service,
                spreadsheet_id="trip-spreadsheet-123",
                sheet_name="Taiwan/Vietnam 2026",
                transactions=append_trip_cost_rows.call_args.kwargs["transactions"],
            )
            trip_transaction = append_trip_cost_rows.call_args.kwargs["transactions"][0]
            self.assertIsInstance(trip_transaction, Transaction)
            self.assertEqual(trip_transaction.category, "Unknown")
            self.assertEqual(trip_transaction.merchant, "Whole Foods Market")
            read_budget_sheet_rows.assert_not_called()
            sync_monthly_budget_sheet.assert_not_called()
            self.assertIn("Trip costs written to: Taiwan/Vietnam 2026", stdout.getvalue())

    def test_main_prints_categorized_and_unknown_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            args = self._args(
                db_file=root / "budget_sync.db",
                query="from:alerts@capitalone.com",
                dry_run=True,
            )
            categorized_transaction = Transaction(
                amount=Decimal("18.79"),
                date="2026-03-12 14:30",
                merchant="Whole Foods Market",
                category="Groceries",
                source_file="gmail:msg-1",
                raw_snippet="Your Capital One card ending in 4242 was charged $18.79 at Whole Foods Market.",
                source_name="gmail",
                account_last4="4242",
                confidence=0.98,
            )
            unknown_transaction = Transaction(
                amount=Decimal("4.56"),
                date="2026-03-27 09:15",
                merchant="Unknown Vendor",
                category="Unknown",
                source_file="gmail:msg-unknown",
                raw_snippet="mystery merchant charge from email body",
                source_name="gmail",
                account_last4=None,
                confidence=0.42,
                predicted_category="Eating Out",
                predicted_confidence=0.82,
                predicted_details_summary="restaurant charge",
                predicted_rationale="The merchant appears to be a restaurant charge.",
            )
            result = GmailSyncResult(
                transactions=[categorized_transaction, unknown_transaction],
                skipped_message_ids=[],
                skipped_messages=[],
                max_internal_date_ms=1,
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
                patch("budget_tracker.google_cli.build_google_service", return_value=object()),
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result),
                patch("budget_tracker.google_cli.write_transactions_csv"),
                patch("budget_tracker.google_cli.utc_now_iso", side_effect=["2026-03-27T12:00:00+00:00", "2026-03-27T12:05:00+00:00"]),
            ):
                build_parser.return_value.parse_args.return_value = args
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertIn("Categorized transactions:", stdout.getvalue())
            self.assertIn("- 2026-03-12 14:30 | $18.79 | Groceries | gmail | Whole Foods Market", stdout.getvalue())
            self.assertIn("Unknown transactions:", stdout.getvalue())
            self.assertIn(
                "- 2026-03-27 09:15 | $4.56 | Eating Out | restaurant charge | 0.8200 | The merchant appears to be a restaurant charge.",
                stdout.getvalue(),
            )

    def test_default_query_includes_all_configured_sender_addresses(self) -> None:
        sync_config = {
            "gmail": {
                "senders": [
                    {"email": "discover@services.discover.com"},
                    {"email": "venmo@venmo.com"},
                    {"email": "capitalone@notification.capitalone.com"},
                ]
            }
        }

        self.assertEqual(
            _default_query(sync_config),
            "(from:discover@services.discover.com OR from:venmo@venmo.com OR from:capitalone@notification.capitalone.com)",
        )

    def _seed_completed_baseline(self, db_file: Path, *, started_at: str, internal_date_ms: int) -> None:
        db = BudgetDatabase(db_file)
        db.ensure_schema()
        run_id = db.record_scrape_run(
            started_at_utc=started_at,
            status="started",
            gmail_query="from:seed@example.com",
            max_results=10,
        )
        db.finalize_scrape_run(
            run_id,
            completed_at_utc=started_at,
            status="completed",
            last_seen_internal_date_ms=internal_date_ms,
            is_baseline=True,
        )

    def _args(
        self,
        *,
        db_file: Path,
        query: str,
        dry_run: bool,
        output_json: Path | None = None,
        summary_csv: Path | None = None,
        spreadsheet_id: str | None = None,
        sheet_name: str | None = None,
        trip_sheet_name: str | None = None,
        trip_spreadsheet_id: str | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            credentials_file=Path("credentials.json"),
            token_file=Path("token.json"),
            db_file=db_file,
            query=query,
            max_results=10,
            output_json=output_json,
            summary_csv=summary_csv,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            summary_sheet_name=None,
            trip_sheet_name=trip_sheet_name,
            trip_spreadsheet_id=trip_spreadsheet_id,
            dry_run=dry_run,
            clear_sheet=False,
            show_skipped=True,
        )


def _sample_transaction() -> Transaction:
    return Transaction(
        amount=Decimal("18.79"),
        date="2026-03-12",
        merchant="Whole Foods Market",
        category="Groceries",
        source_file="gmail:msg-1",
        raw_snippet="Your Capital One card ending in 4242 was charged $18.79 at Whole Foods Market.",
        source_name="capital_one",
        account_last4="4242",
        confidence=0.98,
    )
