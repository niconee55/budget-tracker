from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from budget_tracker.parser import parse_email_file, parse_transaction_file


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emails"
INBOX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inbox"


class ParserTests(unittest.TestCase):
    def test_parses_grocery_fixture(self) -> None:
        transaction = parse_transaction_file(FIXTURES / "03_grocery.txt")
        self.assertEqual(transaction.date, "2026-03-03")
        self.assertEqual(transaction.merchant, "Whole Foods")
        self.assertEqual(transaction.amount, Decimal("138.42"))
        self.assertEqual(transaction.last4, "1234")
        self.assertIn("Purchase at Whole Foods", transaction.raw_snippet)

    def test_parses_generic_text_fixture(self) -> None:
        transaction = parse_transaction_file(FIXTURES / "01_income.txt")
        self.assertEqual(transaction.date, "2026-03-01")
        self.assertEqual(transaction.merchant, "Acme Payroll")
        self.assertEqual(transaction.amount, Decimal("2500.00"))
        self.assertEqual(transaction.last4, "1234")

    def test_reads_capital_one_eml_fixture(self) -> None:
        email = parse_email_file(INBOX_FIXTURES / "capital_one_groceries.eml")
        self.assertEqual(email["sender"], "alerts@capitalone.com")
        self.assertEqual(email["subject"], "Capital One Purchase Alert")
        self.assertIn("Whole Foods Market", email["body"])
        self.assertIn("$18.79", email["body"])
        self.assertIn("March 12, 2026", email["body"])

    def test_parses_discover_purchase_alert_fixture(self) -> None:
        transaction = parse_transaction_file(INBOX_FIXTURES / "discover_groceries.eml")
        self.assertEqual(transaction.date, "2026-03-27")
        self.assertEqual(transaction.merchant, "Trader Joe's")
        self.assertEqual(transaction.amount, Decimal("43.21"))
        self.assertEqual(transaction.last4, "6789")
        self.assertIn("A purchase of $43.21 at Trader Joe's", transaction.raw_snippet)
