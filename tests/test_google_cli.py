from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from budget_tracker.google_cli import _default_query, _validate_args, main
from budget_tracker.gmail_sync import GmailSyncResult
from budget_tracker.models import Transaction
from budget_tracker.state import SyncState, iso_to_epoch_millis


class GoogleCliValidationTests(unittest.TestCase):
    def test_rejects_non_positive_max_results(self) -> None:
        args = argparse.Namespace(
            max_results=0,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            skip_sheet_update=False,
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
            skip_sheet_update=False,
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
            skip_sheet_update=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(
            str(exc.exception),
            "provide at least one output target: local files, sheet sync, or --skip-sheet-update",
        )

    def test_rejects_summary_sheet_without_primary_sheet_target(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name="Summary",
            skip_sheet_update=False,
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(
            str(exc.exception),
            "--summary-sheet-name requires --spreadsheet-id and --sheet-name",
        )

    def test_allows_skip_sheet_update_without_other_output_targets(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
            skip_sheet_update=True,
        )

        self.assertIsNone(_validate_args(args))


class GoogleCliMainTests(unittest.TestCase):
    def test_main_writes_local_outputs_and_prints_skipped_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_json = root / "transactions.json"
            summary_csv = root / "summary.csv"
            state_file = root / "state.json"
            args = argparse.Namespace(
                credentials_file=root / "credentials.json",
                token_file=root / "token.json",
                state_file=state_file,
                query="from:alerts@capitalone.com",
                max_results=10,
                output_json=output_json,
                summary_csv=summary_csv,
                spreadsheet_id=None,
                sheet_name=None,
                summary_sheet_name=None,
                skip_sheet_update=False,
                clear_sheet=False,
                show_skipped=True,
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=["msg-2"],
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_sync_state", return_value=SyncState()) as load_sync_state,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()) as load_credentials,
                patch("budget_tracker.google_cli.build_google_service", return_value=object()) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
                patch("budget_tracker.google_cli.utc_now_iso", return_value="2026-03-27T00:00:00+00:00"),
                patch("budget_tracker.google_cli.save_sync_state") as save_sync_state,
            ):
                build_parser.return_value.parse_args.return_value = args
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_json.exists())
            self.assertTrue(summary_csv.exists())
            self.assertIn('"skipped_message_ids": [', stdout.getvalue())
            self.assertIn('"msg-2"', stdout.getvalue())
            load_sync_state.assert_called_once_with(args.state_file)
            load_credentials.assert_called_once_with(args.credentials_file, args.token_file)
            build_service.assert_called_once_with("gmail", "v1", load_credentials.return_value)
            fetch_transactions.assert_called_once()
            save_sync_state.assert_called_once_with(
                args.state_file,
                SyncState(last_successful_run_utc="2026-03-27T00:00:00+00:00"),
            )

    def test_main_rewrites_stable_latest_scrape_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            latest_scrape_csv = root / "latest_scrape.csv"
            args = argparse.Namespace(
                credentials_file=root / "credentials.json",
                token_file=root / "token.json",
                state_file=root / "state.json",
                query="from:alerts@capitalone.com",
                max_results=10,
                output_json=None,
                summary_csv=None,
                spreadsheet_id="spreadsheet-123",
                sheet_name="Transactions",
                summary_sheet_name=None,
                skip_sheet_update=False,
                clear_sheet=False,
                show_skipped=False,
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=[],
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli._latest_scrape_csv_path", return_value=latest_scrape_csv),
                patch("budget_tracker.google_cli.load_sync_state", return_value=SyncState()),
                patch("budget_tracker.google_cli.utc_now_iso", return_value="2026-03-27T00:00:00+00:00"),
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
                patch("budget_tracker.google_cli.build_google_service", side_effect=[object(), object()]),
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result),
                patch("budget_tracker.google_cli.read_budget_sheet_rows", return_value=[["Month"], ["Expected "], ["March 2026"]]),
                patch("budget_tracker.google_cli.sync_monthly_budget_sheet"),
                patch("budget_tracker.google_cli.save_sync_state"),
            ):
                build_parser.return_value.parse_args.return_value = args
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(latest_scrape_csv.exists())
            csv_text = latest_scrape_csv.read_text()
            self.assertIn(
                "date,merchant,amount,source,category,assigned_category,source_name,source_file,account_last4,confidence,raw_snippet",
                csv_text,
            )
            self.assertIn(
                "2026-03-12,Whole Foods Market,18.79,capital_one,Groceries,Groceries,capital_one,gmail:msg-1,4242,0.9800",
                csv_text,
            )

    def test_main_prints_categorized_and_unknown_transaction_sections(self) -> None:
        args = argparse.Namespace(
            credentials_file=Path("credentials.json"),
            token_file=Path("token.json"),
            state_file=Path("state.json"),
            query="from:alerts@capitalone.com",
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Transactions",
            summary_sheet_name=None,
            skip_sheet_update=False,
            clear_sheet=False,
            show_skipped=False,
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
        )

        with (
            patch("budget_tracker.google_cli.build_parser") as build_parser,
            patch("budget_tracker.google_cli.load_sync_state", return_value=SyncState()),
            patch("budget_tracker.google_cli.utc_now_iso", return_value="2026-03-27T12:00:00+00:00"),
            patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
            patch("budget_tracker.google_cli.build_google_service", side_effect=[object(), object()]),
            patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result),
            patch("budget_tracker.google_cli.read_budget_sheet_rows", return_value=[["Month"], ["Expected "], ["March 2026"]]),
            patch("budget_tracker.google_cli.sync_monthly_budget_sheet"),
            patch("budget_tracker.google_cli.save_sync_state"),
        ):
            build_parser.return_value.parse_args.return_value = args
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Categorized transactions:", stdout.getvalue())
        self.assertIn(
            "2026-03-12 14:30 | $18.79 | gmail | Groceries | Whole Foods Market",
            stdout.getvalue(),
        )
        self.assertIn("--------------------------------", stdout.getvalue())
        self.assertIn("Unknown transactions:", stdout.getvalue())
        self.assertIn(
            "2026-03-27 09:15 | $4.56 | restaurant charge | Eating Out | 0.8200 | The merchant appears to be a restaurant charge.",
            stdout.getvalue(),
        )

    def test_main_skips_google_sheets_when_flag_is_set(self) -> None:
        args = argparse.Namespace(
            credentials_file=Path("credentials.json"),
            token_file=Path("token.json"),
            state_file=Path("state.json"),
            query="from:alerts@capitalone.com",
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Transactions",
            summary_sheet_name=None,
            skip_sheet_update=True,
            clear_sheet=False,
            show_skipped=False,
        )
        result = GmailSyncResult(
            transactions=[_sample_transaction()],
            skipped_message_ids=[],
        )

        with (
            patch("budget_tracker.google_cli.build_parser") as build_parser,
            patch("budget_tracker.google_cli.load_sync_state", return_value=SyncState()),
            patch("budget_tracker.google_cli.utc_now_iso", return_value="2026-03-27T12:00:00+00:00"),
            patch("budget_tracker.google_cli.load_google_credentials", return_value=object()),
            patch("budget_tracker.google_cli.build_google_service", return_value=object()) as build_service,
            patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result),
            patch("budget_tracker.google_cli.read_budget_sheet_rows") as read_budget_sheet_rows,
            patch("budget_tracker.google_cli.sync_monthly_budget_sheet") as sync_monthly_budget_sheet,
            patch("budget_tracker.google_cli.save_sync_state"),
        ):
            build_parser.return_value.parse_args.return_value = args
            exit_code = main()

        self.assertEqual(exit_code, 0)
        build_service.assert_called_once()
        read_budget_sheet_rows.assert_not_called()
        sync_monthly_budget_sheet.assert_not_called()

    def test_main_reads_monthly_sheet_uses_last_run_state_and_syncs_specific_cells(self) -> None:
        args = argparse.Namespace(
            credentials_file=Path("credentials.json"),
            token_file=Path("token.json"),
            state_file=Path("state.json"),
            query="from:alerts@capitalone.com",
            max_results=10,
            output_json=None,
            summary_csv=None,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Transactions",
            summary_sheet_name=None,
            clear_sheet=False,
            show_skipped=False,
        )
        credentials = object()
        gmail_service = object()
        sheets_service = object()
        monthly_rows = [
            ["Month", "Monthly Income", "Utilities"],
            ["Expected ", "$0.00", "$0.00"],
            ["March 2026", "", ""],
        ]
        result = GmailSyncResult(
            transactions=[_sample_transaction()],
            skipped_message_ids=[],
        )
        events: list[str] = []

        with (
            patch("budget_tracker.google_cli.build_parser") as build_parser,
            patch(
                "budget_tracker.google_cli.load_sync_state",
                return_value=SyncState(last_successful_run_utc="2026-03-26T12:00:00+00:00"),
            ),
            patch("budget_tracker.google_cli.utc_now_iso", return_value="2026-03-27T12:00:00+00:00"),
            patch("budget_tracker.google_cli.load_google_credentials", return_value=credentials),
            patch("budget_tracker.google_cli.build_google_service", side_effect=[gmail_service, sheets_service]) as build_service,
            patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
            patch("budget_tracker.google_cli.read_budget_sheet_rows", side_effect=lambda **kwargs: events.append("read") or monthly_rows),
            patch("budget_tracker.google_cli.sync_monthly_budget_sheet", side_effect=lambda **kwargs: events.append("sync")),
            patch("budget_tracker.google_cli.save_sync_state") as save_sync_state,
        ):
            build_parser.return_value.parse_args.return_value = args
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(build_service.call_count, 2)
        self.assertEqual(events, ["read", "sync"])
        fetch_kwargs = fetch_transactions.call_args.kwargs
        self.assertEqual(
            fetch_kwargs["since_epoch_ms"],
            iso_to_epoch_millis("2026-03-26T12:00:00+00:00"),
        )
        save_sync_state.assert_called_once_with(
            args.state_file,
            SyncState(last_successful_run_utc="2026-03-27T12:00:00+00:00"),
        )
        self.assertTrue(
            fetch_kwargs["categorizer"].__class__.__name__.endswith("TransactionCategorizer")
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
