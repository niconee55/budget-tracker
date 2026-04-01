from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from budget_tracker.config import load_email_sources
from budget_tracker.parser import extract_transaction_from_email_data, parse_email_file, parse_transaction_file


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emails"
INBOX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inbox"


class ParserTests(unittest.TestCase):
    def test_email_source_config_includes_capital_one_zelle_source(self) -> None:
        sources = load_email_sources()["sources"]
        source = next(item for item in sources if item["name"] == "capital_one_zelle")

        self.assertIn("capitalone@notification\\.capitalone\\.com", source["sender_patterns"])
        self.assertEqual(
            source["body_selectors"],
            [
                "#m_-6797248785071591561body > div:nth-child(1) > table > tbody > tr > td > table:nth-child(6) > tbody > tr > td > p",
                "#m_-5746832775708897921body > div:nth-child(1) > table > tbody > tr > td > table:nth-child(5) > tbody > tr > td > p"
            ],
        )
        self.assertIn("zelle", source["subject_patterns"])
        self.assertLess(
            sources.index(source),
            next(index for index, item in enumerate(sources) if item["name"] == "capital_one_placeholder"),
        )

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

    def test_parses_capital_one_notification_fixture(self) -> None:
        transaction = parse_transaction_file(INBOX_FIXTURES / "capitalone_notification.eml")
        self.assertEqual(transaction.date, "2026-03-26")
        self.assertEqual(transaction.merchant, "H Mart")
        self.assertEqual(transaction.amount, Decimal("12.34"))
        self.assertEqual(transaction.last4, "4321")
        self.assertIn("Capital One purchase alert", transaction.raw_snippet)

    def test_parses_venmo_incoming_fixture_from_html_body(self) -> None:
        transaction = parse_transaction_file(INBOX_FIXTURES / "venmo_received.eml")
        self.assertEqual(transaction.date, "2026-03-27")
        self.assertEqual(transaction.merchant, "Jordan")
        self.assertEqual(transaction.amount, Decimal("27.00"))
        self.assertEqual(transaction.source_name, "venmo_incoming")
        self.assertIn("Jordan paid you $27.00", transaction.raw_snippet)

    def test_parses_venmo_received_payment_fixture(self) -> None:
        transaction = parse_transaction_file(INBOX_FIXTURES / "venmo_paid_you.txt")
        self.assertEqual(transaction.date, "2026-03-25")
        self.assertEqual(transaction.merchant, "Example Payroll LLC")
        self.assertEqual(transaction.amount, Decimal("37.88"))
        self.assertIn("NIL Club payout", transaction.raw_snippet)

    def test_parses_venmo_sent_payment_fixture_using_memo_for_merchant(self) -> None:
        transaction = parse_transaction_file(INBOX_FIXTURES / "venmo_you_paid.txt")
        self.assertEqual(transaction.date, "2026-03-22")
        self.assertEqual(transaction.merchant, "Bfast")
        self.assertEqual(transaction.amount, Decimal("8.00"))
        self.assertIn("You paid Casey Rivera $8.00", transaction.raw_snippet)

    def test_reads_venmo_html_fixture_body_from_nested_structure(self) -> None:
        sent_email = parse_email_file(INBOX_FIXTURES / "venmo_sent.eml")
        received_email = parse_email_file(INBOX_FIXTURES / "venmo_received.eml")

        self.assertIn("Ramen lunch", sent_email["body"])
        self.assertIn("Amount: $19.50", sent_email["body"])
        self.assertIn("Date: March 27, 2026", sent_email["body"])
        self.assertIn("You paid Jordan $19.50", sent_email["body"])
        self.assertIn("Dinner reimbursement", received_email["body"])
        self.assertIn("Amount: $27.00", received_email["body"])
        self.assertIn("Date: March 27, 2026", received_email["body"])
        self.assertIn("Jordan paid you $27.00", received_email["body"])

    def test_parses_capital_one_zelle_memo_from_html_selector(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "You sent money with Zelle",
            "date_header": "Mon, 30 Mar 2026 10:15:00 -0400",
            "body": "Fallback body without the memo.",
            "html_body": """
                <div id="m_-6797248785071591561body">
                  <div>
                    <table><tbody><tr><td>
                      <table></table>
                      <table></table>
                      <table></table>
                      <table></table>
                      <table></table>
                      <table>
                        <tbody><tr><td>
                          <p>You sent Bradley Chao $15.00 on March 30, 2026. Memo: Pizza. Account ending in 7175.</p>
                        </td></tr></tbody>
                      </table>
                    </td></tr></tbody></table>
                  </div>
                </div>
            """,
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-zelle")

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one_zelle")
        self.assertEqual(transaction.date, "2026-03-30")
        self.assertEqual(transaction.merchant, "Pizza")
        self.assertEqual(transaction.amount, Decimal("15.00"))
        self.assertEqual(transaction.last4, "7175")
