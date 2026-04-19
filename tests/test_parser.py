from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from budget_tracker.config import load_email_sources
from budget_tracker.parser import extract_transaction_from_email_data, parse_email_file, parse_transaction_file


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emails"
INBOX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inbox"


class ParserTests(unittest.TestCase):
    def test_email_source_config_includes_capital_one_deposit_source(self) -> None:
        sources = load_email_sources()["sources"]
        source = next(item for item in sources if item["name"] == "capital_one_deposit")

        self.assertIn("capitalone@notification\\.captialone\\.com", source["sender_patterns"])
        self.assertEqual(
            source["body_selectors"],
            [
                "#m_1774081847278880687body > div:nth-child(1) > table > tbody > tr > td > table:nth-child(5) > tbody > tr > td > p:nth-child(2)",
                "#m_1774081847278880687body > div:nth-child(1) > table > tbody > tr > td > table:nth-child(4)"
            ],
        )
        self.assertIn("deposit", source["subject_patterns"])

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

    def test_email_source_config_includes_capital_one_pending_purchase_patterns(self) -> None:
        sources = load_email_sources()["sources"]
        source = next(item for item in sources if item["name"] == "capital_one_placeholder")

        self.assertIn("charged to your account", source["subject_patterns"])
        self.assertIn(
            "a\\s+purchase\\s+was\\s+charged\\s+to\\s+your\\s+account",
            source["body_match_patterns"],
        )
        self.assertIn(
            "pending\\s+authorization\\s+or\\s+purchase\\s+in\\s+the\\s+amount\\s+of\\s+\\$(-?[0-9][0-9,]*\\.[0-9]{2})",
            source["amount_patterns"],
        )
        self.assertIn(
            "([A-Za-z0-9 '&./*-]{2,80}?),\\s+a\\s+pending\\s+authorization\\s+or\\s+purchase\\s+in\\s+the\\s+amount\\s+of\\s+\\$[-]?[0-9][0-9,]*\\.[0-9]{2}",
            source["merchant_patterns"],
        )

    def test_email_source_config_includes_capital_one_withdrawal_source(self) -> None:
        sources = load_email_sources()["sources"]
        source = next(item for item in sources if item["name"] == "capital_one_withdrawal")

        self.assertEqual(source["subject_patterns"], ["withdrawal notice"])
        self.assertEqual(source["body_selectors"], ["table.darkmode td.webfont"])
        self.assertIn(
            "([A-Za-z0-9 '&./*-]{2,80})\\s+has\\s+initiated\\s+the\\s+following\\s+withdrawal",
            source["merchant_patterns"],
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

    def test_parses_capital_one_pending_purchase_wording_without_hard_coded_selector(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Capital One Savor purchase alert",
            "date_header": "Thu, 16 Apr 2026 14:20:00 -0400",
            "body": "TARGET STORE T-3387, a pending authorization or purchase in the amount of $18.42 was placed on your Capital One Savor card ending in 5512 on April 16, 2026.",
            "html_body": "",
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-savor")

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one_placeholder")
        self.assertEqual(transaction.date, "2026-04-16")
        self.assertEqual(transaction.merchant, "TARGET STORE T-3387")
        self.assertEqual(transaction.amount, Decimal("18.42"))
        self.assertEqual(transaction.last4, "5512")

    def test_ignores_ambiguous_capital_one_synergy_charge(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Capital One purchase alert",
            "date_header": "Wed, 15 Apr 2026 09:20:00 -0400",
            "body": "SYNERGY FI, a pending authorization or purchase in the amount of $73.11 was placed on your Capital One card ending in 5512 on April 15, 2026.",
            "html_body": "",
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-synergy")

        self.assertIsNone(transaction)

    def test_parses_capital_one_savor_charged_to_account_wording(self) -> None:
        email_data = {
            "sender": "Capital One | Savor <capitalone@notification.capitalone.com>",
            "subject": "A new transaction was charged to your account",
            "date_header": "Mon, 13 Apr 2026 19:54:19 -0400",
            "body": (
                "View posted transaction details. -- Capital One | Savor -- A purchase was charged to your account. "
                "About your Savor Credit Card ending in 5363 As requested, we're notifying you that on April 13, 2026, "
                "at Whole Foods Market, a pending authorization or purchase in the amount of $10.49 was placed or charged "
                "on your Savor Credit Card."
            ),
            "html_body": "",
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-savor-charged")

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one_placeholder")
        self.assertEqual(transaction.date, "2026-04-13")
        self.assertEqual(transaction.merchant, "Whole Foods Market")
        self.assertEqual(transaction.amount, Decimal("10.49"))
        self.assertEqual(transaction.last4, "5363")

    def test_ignores_capital_one_withdrawal_notice_when_initiator_is_discover(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Withdrawal notice",
            "date_header": "Mon, 13 Apr 2026 05:58:23 -0400",
            "body": "Withdrawal notice",
            "html_body": """
                <table class="darkmode">
                  <tr>
                    <td class="webfont">
                      <p>DISCOVER has initiated the following withdrawal from your 360 Checking...0140 account:</p>
                      <p>Amount: <strong>$195.71</strong></p>
                      <p>From: Account ending in <strong>0140</strong></p>
                      <p>Submitted on: <strong>April 13, 2026</strong></p>
                    </td>
                  </tr>
                </table>
            """,
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-withdrawal")

        self.assertIsNone(transaction)

    def test_ignores_capital_one_withdrawal_notice_when_initiator_is_venmo(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Withdrawal notice",
            "date_header": "Mon, 13 Apr 2026 05:57:54 -0400",
            "body": "Withdrawal notice",
            "html_body": """
                <table class="darkmode">
                  <tr>
                    <td class="webfont">
                      <p>VENMO has initiated the following withdrawal from your 360 Checking...0140 account:</p>
                      <p>Amount: <strong>$175.00</strong></p>
                      <p>From: Account ending in <strong>0140</strong></p>
                      <p>Submitted on: <strong>April 13, 2026</strong></p>
                    </td>
                  </tr>
                </table>
            """,
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-venmo-withdrawal")

        self.assertIsNone(transaction)

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

    def test_parses_capital_one_deposit_notice_from_html_selector(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.captialone.com>",
            "subject": "Deposit notice",
            "date_header": "Fri, 10 Apr 2026 09:15:00 -0400",
            "body": "Fallback body without the amount.",
            "html_body": """
                <div id="m_1774081847278880687body">
                  <div>
                    <table><tbody><tr><td>
                      <table></table>
                      <table></table>
                      <table></table>
                      <table>
                        <tbody><tr><td>
                          <p>Payroll from Example Employer LLC</p>
                        </td></tr></tbody>
                      </table>
                      <table>
                        <tbody><tr><td>
                          <p>Deposit details</p>
                          <p>You received a deposit of $3,396.70 into your account ending in 0140.</p>
                        </td></tr></tbody>
                      </table>
                    </td></tr></tbody></table>
                  </div>
                </div>
            """,
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-deposit")

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one_deposit")
        self.assertEqual(transaction.date, "2026-04-10")
        self.assertEqual(transaction.amount, Decimal("3396.70"))
        self.assertEqual(transaction.last4, "0140")

    def test_parses_capital_one_deposit_notice_without_deposit_in_subject(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Account alert",
            "date_header": "Fri, 10 Apr 2026 09:15:00 -0400",
            "body": "Fallback body without the amount.",
            "html_body": """
                <div id="m_1774081847278880687body">
                  <div>
                    <table><tbody><tr><td>
                      <table></table>
                      <table></table>
                      <table></table>
                      <table>
                        <tbody><tr><td>
                          <p>Payroll from Example Employer LLC</p>
                        </td></tr></tbody>
                      </table>
                      <table>
                        <tbody><tr><td>
                          <p>Deposit details</p>
                          <p>You received a deposit of $3,396.70 into your account ending in 0140.</p>
                        </td></tr></tbody>
                      </table>
                    </td></tr></tbody></table>
                  </div>
                </div>
            """,
        }

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-deposit-generic-subject")

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one_deposit")
        self.assertEqual(transaction.date, "2026-04-10")
        self.assertEqual(transaction.amount, Decimal("3396.70"))
        self.assertEqual(transaction.last4, "0140")

    def test_generic_fallback_uses_discover_sender_family_as_source_name(self) -> None:
        email_data = {
            "sender": "Discover <discover@services.discover.com>",
            "subject": "Card activity",
            "date_header": "Tue, 8 Apr 2026 09:15:00 -0400",
            "body": "A purchase of $10.88 at TARGET STORE T-3387 on April 8, 2026. Card ending in 6789.",
            "html_body": "",
        }
        sources = [
            {
                "name": "generic",
                "sender_patterns": [],
                "subject_patterns": [],
                "amount_patterns": [r"\$(-?[0-9][0-9,]*\.[0-9]{2})"],
                "merchant_patterns": [r"at\s+([A-Za-z0-9 '&./-]{2,80}?)(?=\s+on\s+|[.,]|$)"],
                "date_patterns": [r"on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})"],
                "last4_patterns": [r"ending in\s*(\d{4})"],
            }
        ]

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-discover-generic", sources)

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "discover")

    def test_generic_fallback_uses_capital_one_sender_family_as_source_name(self) -> None:
        email_data = {
            "sender": "Capital One <capitalone@notification.capitalone.com>",
            "subject": "Card activity",
            "date_header": "Mon, 7 Apr 2026 09:15:00 -0400",
            "body": "A purchase of $16.25 at WHOLEFDS UES on April 7, 2026. Card ending in 0140.",
            "html_body": "",
        }
        sources = [
            {
                "name": "generic",
                "sender_patterns": [],
                "subject_patterns": [],
                "amount_patterns": [r"\$(-?[0-9][0-9,]*\.[0-9]{2})"],
                "merchant_patterns": [r"at\s+([A-Za-z0-9 '&./-]{2,80}?)(?=\s+on\s+|[.,]|$)"],
                "date_patterns": [r"on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})"],
                "last4_patterns": [r"ending in\s*(\d{4})"],
            }
        ]

        transaction = extract_transaction_from_email_data(email_data, "gmail:test-capitalone-generic", sources)

        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertEqual(transaction.source_name, "capital_one")
