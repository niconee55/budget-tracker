from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
import sys

from budget_tracker.categorizer import TransactionCategorizer
from budget_tracker.parser import extract_transactions


ROOT = Path(__file__).resolve().parents[1]
EMAIL_FIXTURES = ROOT / "tests" / "fixtures" / "emails"
INBOX_FIXTURES = ROOT / "tests" / "fixtures" / "inbox"


class ParserTests(unittest.TestCase):
    def test_extracts_transaction_fields_from_fixture_emails(self) -> None:
        parsed = extract_transactions(EMAIL_FIXTURES)
        self.assertEqual(len(parsed), 9)
        first = parsed[0]
        self.assertEqual(first.source_name, "generic")
        self.assertEqual(first.date, "2026-03-01")
        self.assertEqual(first.merchant, "Acme Payroll")
        self.assertEqual(first.amount, Decimal("2500.00"))
        self.assertEqual(first.account_last4, "1234")

    def test_extracts_discover_transaction_fields_from_inbox_fixtures(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        discover_transactions = [item for item in parsed if item.source_file.startswith("discover_")]

        self.assertEqual(len(discover_transactions), 4)
        categorizer = TransactionCategorizer()
        by_merchant = {item.merchant: categorizer.categorize(item).category for item in discover_transactions}
        self.assertEqual(by_merchant["Trader Joe's"], "Groceries")
        self.assertEqual(by_merchant["Walgreens"], "Healthcare")
        self.assertEqual(by_merchant["Uber"], "Transportation")
        self.assertEqual(by_merchant["Netflix"], "Entertainment")

    def test_extracts_capital_one_notification_transaction_fields_from_inbox_fixture(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        matches = [item for item in parsed if item.source_file == "capitalone_notification.eml"]

        self.assertEqual(len(matches), 1)
        transaction = matches[0]
        categorizer = TransactionCategorizer()
        self.assertEqual(categorizer.categorize(transaction).category, "Groceries")
        self.assertEqual(transaction.merchant, "H Mart")
        self.assertEqual(transaction.amount, Decimal("12.34"))
        self.assertEqual(transaction.last4, "4321")

    def test_extracts_venmo_transactions_and_ignores_brokerage_transfer(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        venmo_transactions = [
            item
            for item in parsed
            if item.source_file in {"venmo_sent.eml", "venmo_received.eml", "venmo_you_paid.txt", "venmo_paid_you.txt"}
        ]

        self.assertEqual(len(venmo_transactions), 4)
        by_source = {item.source_file: item for item in venmo_transactions}
        self.assertIn("venmo_sent.eml", by_source)
        self.assertIn("venmo_received.eml", by_source)
        self.assertIn("venmo_paid_you.txt", by_source)
        self.assertIn("venmo_you_paid.txt", by_source)
        self.assertNotIn("venmo_wealthfront_transfer.txt", by_source)
        self.assertNotIn("venmo_history.txt", by_source)

    def test_extracts_venmo_transactions_and_ignores_non_budget_noise(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        matches = {item.source_file: item for item in parsed if item.source_file.startswith("venmo_")}

        self.assertIn("venmo_paid_you.txt", matches)
        self.assertIn("venmo_you_paid.txt", matches)
        self.assertNotIn("venmo_history.txt", matches)
        self.assertNotIn("venmo_wealthfront_transfer.txt", matches)
        self.assertEqual(matches["venmo_paid_you.txt"].merchant, "Example Payroll LLC")
        self.assertEqual(matches["venmo_you_paid.txt"].merchant, "Bfast")


class CategorizerTests(unittest.TestCase):
    def test_categorizes_fixture_transactions_into_requested_categories(self) -> None:
        parsed = extract_transactions(EMAIL_FIXTURES)
        categorizer = TransactionCategorizer()
        categorized = [categorizer.categorize(item) for item in parsed]
        by_merchant = {item.merchant: item.category for item in categorized}

        self.assertEqual(by_merchant["Acme Payroll"], "Monthly Income")
        self.assertEqual(by_merchant["Con Edison"], "Utilities")
        self.assertEqual(by_merchant["Whole Foods"], "Groceries")
        self.assertEqual(by_merchant["Uber"], "Transportation")
        self.assertEqual(by_merchant["Joe's Pizza"], "Eating Out")
        self.assertEqual(by_merchant["CVS Pharmacy"], "Healthcare")
        self.assertEqual(by_merchant["Sephora"], "Clothes/Personal Care")
        self.assertEqual(by_merchant["Home Depot"], "Housing Supplies")
        self.assertEqual(by_merchant["Netflix"], "Entertainment")

    def test_categorizes_discover_fixture_transactions_into_requested_categories(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        discover_transactions = [item for item in parsed if item.source_file.startswith("discover_")]
        categorizer = TransactionCategorizer()
        categorized = {item.merchant: categorizer.categorize(item).category for item in discover_transactions}

        self.assertEqual(categorized["Trader Joe's"], "Groceries")
        self.assertEqual(categorized["Walgreens"], "Healthcare")
        self.assertEqual(categorized["Uber"], "Transportation")
        self.assertEqual(categorized["Netflix"], "Entertainment")

    def test_categorizes_capital_one_notification_fixture_into_requested_category(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        matches = [item for item in parsed if item.source_file == "capitalone_notification.eml"]
        categorizer = TransactionCategorizer()

        self.assertEqual(len(matches), 1)
        self.assertEqual(categorizer.categorize(matches[0]).category, "Groceries")

    def test_categorizes_venmo_transactions_by_direction(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        venmo_transactions = [
            item
            for item in parsed
            if item.source_file in {"venmo_sent.eml", "venmo_received.eml", "venmo_you_paid.txt", "venmo_paid_you.txt"}
        ]
        categorizer = TransactionCategorizer()
        categorized = {item.source_file: categorizer.categorize(item) for item in venmo_transactions}

        self.assertEqual(categorized["venmo_received.eml"].category, "Eating Out")
        self.assertEqual(categorized["venmo_received.eml"].amount, Decimal("-27.00"))
        self.assertEqual(categorized["venmo_sent.eml"].category, "Eating Out")
        self.assertEqual(categorized["venmo_sent.eml"].amount, Decimal("19.50"))

    def test_categorizes_venmo_income_and_memo_spend(self) -> None:
        parsed = extract_transactions(INBOX_FIXTURES)
        categorizer = TransactionCategorizer()
        by_file = {
            item.source_file: categorizer.categorize(item)
            for item in parsed
            if item.source_file.startswith("venmo_")
        }

        self.assertEqual(by_file["venmo_paid_you.txt"].category, "Monthly Income")
        self.assertEqual(by_file["venmo_paid_you.txt"].amount, Decimal("37.88"))
        self.assertEqual(by_file["venmo_you_paid.txt"].category, "Eating Out")
        self.assertEqual(by_file["venmo_you_paid.txt"].amount, Decimal("8.00"))


class CliEndToEndTests(unittest.TestCase):
    def test_cli_writes_json_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_json = Path(temp_dir) / "transactions.json"
            output_csv = Path(temp_dir) / "summary.csv"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "budget_tracker.cli",
                    "--input-dir",
                    str(EMAIL_FIXTURES),
                    "--output-json",
                    str(output_json),
                    "--summary-csv",
                    str(output_csv),
                ],
                check=True,
                cwd=ROOT,
                env=env,
            )

            transactions = json.loads(output_json.read_text())
            self.assertEqual(len(transactions), 9)
            self.assertEqual(transactions[0]["category"], "Monthly Income")

            with output_csv.open() as handle:
                rows = list(csv.DictReader(handle))
            totals = {row["category"]: row["total"] for row in rows}
            self.assertEqual(
                totals,
                {
                    "Monthly Income": "2500.00",
                    "Utilities": "82.15",
                    "Groceries": "138.42",
                    "Transportation": "24.18",
                    "Eating Out": "31.27",
                    "Healthcare": "17.64",
                    "Clothes/Personal Care": "54.73",
                    "Housing Supplies": "76.01",
                    "Entertainment": "15.99",
                    "Gifts": "0.00",
                },
            )


if __name__ == "__main__":
    unittest.main()
