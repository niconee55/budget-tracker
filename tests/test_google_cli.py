from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from budget_tracker.google_cli import _validate_args, main
from budget_tracker.gmail_sync import GmailSyncResult
from budget_tracker.models import Transaction


class GoogleCliValidationTests(unittest.TestCase):
    def test_rejects_non_positive_max_results(self) -> None:
        args = argparse.Namespace(
            max_results=0,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name=None,
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
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(
            str(exc.exception),
            "provide at least one output target: local files or --spreadsheet-id/--sheet-name",
        )

    def test_rejects_summary_sheet_without_primary_sheet_target(self) -> None:
        args = argparse.Namespace(
            max_results=10,
            output_json=Path("transactions.json"),
            summary_csv=None,
            spreadsheet_id=None,
            sheet_name=None,
            summary_sheet_name="Summary",
        )

        with self.assertRaises(SystemExit) as exc:
            _validate_args(args)

        self.assertEqual(
            str(exc.exception),
            "--summary-sheet-name requires --spreadsheet-id and --sheet-name",
        )


class GoogleCliMainTests(unittest.TestCase):
    def test_main_writes_local_outputs_and_prints_skipped_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_json = root / "transactions.json"
            summary_csv = root / "summary.csv"
            args = argparse.Namespace(
                credentials_file=root / "credentials.json",
                token_file=root / "token.json",
                query="from:alerts@capitalone.com",
                max_results=10,
                output_json=output_json,
                summary_csv=summary_csv,
                spreadsheet_id=None,
                sheet_name=None,
                summary_sheet_name=None,
                clear_sheet=False,
                show_skipped=True,
            )
            result = GmailSyncResult(
                transactions=[_sample_transaction()],
                skipped_message_ids=["msg-2"],
            )

            with (
                patch("budget_tracker.google_cli.build_parser") as build_parser,
                patch("budget_tracker.google_cli.load_google_credentials", return_value=object()) as load_credentials,
                patch("budget_tracker.google_cli.build_google_service", return_value=object()) as build_service,
                patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result) as fetch_transactions,
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
            load_credentials.assert_called_once_with(args.credentials_file, args.token_file)
            build_service.assert_called_once_with("gmail", "v1", load_credentials.return_value)
            fetch_transactions.assert_called_once()

    def test_main_reads_sheet_before_appending_transactions(self) -> None:
        args = argparse.Namespace(
            credentials_file=Path("credentials.json"),
            token_file=Path("token.json"),
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
        result = GmailSyncResult(
            transactions=[_sample_transaction()],
            skipped_message_ids=[],
        )
        events: list[str] = []

        with (
            patch("budget_tracker.google_cli.build_parser") as build_parser,
            patch("budget_tracker.google_cli.load_google_credentials", return_value=credentials),
            patch("budget_tracker.google_cli.build_google_service", side_effect=[gmail_service, sheets_service]) as build_service,
            patch("budget_tracker.google_cli.fetch_transactions_from_gmail", return_value=result),
            patch("budget_tracker.google_cli.read_rows_from_sheet", side_effect=lambda **kwargs: events.append("read") or [["date", "merchant", "amount", "category"]]),
            patch("budget_tracker.google_cli.append_rows_to_sheet", side_effect=lambda **kwargs: events.append("append")),
        ):
            build_parser.return_value.parse_args.return_value = args
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(build_service.call_count, 2)
        self.assertEqual(events, ["read", "append"])


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
