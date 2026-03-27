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
                },
            )


if __name__ == "__main__":
    unittest.main()
