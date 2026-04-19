from __future__ import annotations

import base64
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from budget_tracker.categorizer import TransactionCategorizer
from budget_tracker.gmail_sync import (
    SkippedMessage,
    build_summary_rows,
    build_transaction_rows,
    decode_gmail_raw_message,
    fetch_transactions_from_gmail,
)
from budget_tracker.sheets_sync import is_monthly_budget_sheet


INBOX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inbox"

CAPITAL_ONE_EMAIL = """From: alerts@capitalone.com
To: user@example.com
Subject: Capital One Purchase Alert
Content-Type: text/plain; charset="utf-8"

Your Capital One card ending in 4242 was charged $18.79 at Whole Foods Market on March 12, 2026.
"""

DISCOVER_EMAIL = """From: discover@services.discover.com
To: user@example.com
Subject: Discover Card Purchase Alert
Content-Type: text/plain; charset="utf-8"

A purchase of $43.21 at Trader Joe's on March 27, 2026.
Card ending in 6789.
"""
DISCOVER_STATEMENT_EMAIL = """From: discover@services.discover.com
To: user@example.com
Subject: You have a new statement online
Content-Type: text/plain; charset="utf-8"

Your paperless statement is ready.
Statement Date: March 17, 2026
Statement Balance: $195.71
Your minimum payment of $35.00 is due on April 14, 2026.
"""
DISCOVER_PAYMENT_RECEIVED_EMAIL = """From: discover@services.discover.com
To: user@example.com
Subject: We've received your payment
Content-Type: text/plain; charset="utf-8"

Thanks for your payment.
Your Payment of $1,016.88 posted to your account on March 12, 2026.
"""
CAPITAL_ONE_WEALTHFRONT_EMAIL = """From: capitalone@notification.capitalone.com
To: user@example.com
Subject: You've received an instant payment
Content-Type: text/plain; charset="utf-8"

You've received an instant payment.
The money that was transferred has been deposited.
Amount: $2,000.00
From: WEALTHFRONT BROKERAGE LLC
Deposited to: 360 Checking...0140
Memo: None
"""
CAPITAL_ONE_DEPOSIT_EMAIL = b"""From: Capital One <capitalone@notification.capitalone.com>
To: user@example.com
Subject: Deposit notice
Date: Fri, 10 Apr 2026 09:15:00 -0400
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="CAPONE-DEPOSIT"

--CAPONE-DEPOSIT
Content-Type: text/plain; charset="utf-8"

Fallback body without the amount.

--CAPONE-DEPOSIT
Content-Type: text/html; charset="utf-8"

<html>
  <body>
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
  </body>
</html>
"""
CAPITAL_ONE_DEPOSIT_GENERIC_SUBJECT_EMAIL = CAPITAL_ONE_DEPOSIT_EMAIL.replace(
    b"Subject: Deposit notice",
    b"Subject: Account alert",
)
CAPITAL_ONE_SAVOR_WHOLE_FOODS_EMAIL = b"""From: Capital One | Savor <capitalone@notification.capitalone.com>
To: user@example.com
Subject: A new transaction was charged to your account
Date: Mon, 13 Apr 2026 19:54:19 -0400
Content-Type: text/plain; charset="utf-8"

View posted transaction details.
-- Capital One | Savor --
A purchase was charged to your account.
About your Savor Credit Card ending in 5363
As requested, we're notifying you that on April 13, 2026, at Whole Foods Market, a pending authorization or purchase in the amount of $10.49 was placed or charged on your Savor Credit Card.
"""
CAPITAL_ONE_SYNERGY_EMAIL = b"""From: Capital One | Savor <capitalone@notification.capitalone.com>
To: user@example.com
Subject: A new transaction was charged to your account
Date: Mon, 13 Apr 2026 20:14:29 -0400
Content-Type: text/plain; charset="utf-8"

View posted transaction details.
-- Capital One | Savor --
A purchase was charged to your account.
About your Savor Credit Card ending in 5363
As requested, we're notifying you that on April 13, 2026, at P 00372 SYNERGY FI, a pending authorization or purchase in the amount of $73.11 was placed or charged on your Savor Credit Card.
"""
CAPITAL_ONE_WITHDRAWAL_EMAIL = b"""From: Capital One <capitalone@notification.capitalone.com>
To: user@example.com
Subject: Withdrawal notice
Date: Mon, 13 Apr 2026 05:58:23 -0400
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="CAPONE-WITHDRAWAL"

--CAPONE-WITHDRAWAL
Content-Type: text/plain; charset="utf-8"

Withdrawal notice

--CAPONE-WITHDRAWAL
Content-Type: text/html; charset="utf-8"

<html>
  <body>
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
  </body>
</html>
"""
CAPITAL_ONE_VENMO_WITHDRAWAL_EMAIL = b"""From: Capital One <capitalone@notification.capitalone.com>
To: user@example.com
Subject: Withdrawal notice
Date: Mon, 13 Apr 2026 05:57:54 -0400
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="CAPONE-VENMO-WITHDRAWAL"

--CAPONE-VENMO-WITHDRAWAL
Content-Type: text/plain; charset="utf-8"

Withdrawal notice

--CAPONE-VENMO-WITHDRAWAL
Content-Type: text/html; charset="utf-8"

<html>
  <body>
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
  </body>
</html>
"""
CAPITAL_ONE_NOTIFICATION_EMAIL = (INBOX_FIXTURES / "capitalone_notification.eml").read_bytes()
VENMO_SENT_EMAIL = (INBOX_FIXTURES / "venmo_you_paid.txt").read_bytes()
VENMO_RECEIVED_EMAIL = (INBOX_FIXTURES / "venmo_paid_you.txt").read_bytes()
VENMO_TRANSFER_EMAIL = (INBOX_FIXTURES / "venmo_wealthfront_transfer.txt").read_bytes()


class GoogleSyncTests(unittest.TestCase):
    def test_fetch_transactions_from_gmail_parses_and_categorizes_messages(self) -> None:
        gmail_service = FakeGmailService(
            {
                "msg-1": CAPITAL_ONE_EMAIL.encode("utf-8"),
                "msg-2": b"From: x@example.com\nSubject: Noise\n\nHello world\n",
            }
        )

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:alerts@capitalone.com",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "Whole Foods Market")
        self.assertEqual(transaction.amount, Decimal("18.79"))
        self.assertEqual(transaction.category, "Groceries")
        self.assertEqual(transaction.account_last4, "4242")
        self.assertEqual(transaction.source_file, "gmail:msg-1")
        self.assertEqual(result.skipped_message_ids, ["msg-2"])
        self.assertEqual(
            result.skipped_messages,
            [
                SkippedMessage(
                    message_id="msg-2",
                    subject="Noise",
                    sender="x@example.com",
                    reason="Message did not match a tracked transaction template.",
                )
            ],
        )

    def test_fetch_transactions_from_gmail_returns_empty_result_when_gmail_has_no_messages(self) -> None:
        gmail_service = FakeGmailService({})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="label:finance",
            max_results=5,
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_parses_discover_sender_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": DISCOVER_EMAIL.encode("utf-8")})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:discover@services.discover.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "Trader Joe's")
        self.assertEqual(transaction.amount, Decimal("43.21"))
        self.assertEqual(transaction.category, "Groceries")
        self.assertEqual(transaction.account_last4, "6789")
        self.assertEqual(transaction.source_file, "gmail:msg-1")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_parses_capital_one_notification_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_NOTIFICATION_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "H Mart")
        self.assertEqual(transaction.amount, Decimal("12.34"))
        self.assertEqual(transaction.category, "Groceries")
        self.assertEqual(transaction.account_last4, "4321")
        self.assertEqual(transaction.source_file, "gmail:msg-1")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_parses_capital_one_deposit_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_DEPOSIT_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "Direct Deposit")
        self.assertEqual(transaction.amount, Decimal("3396.70"))
        self.assertEqual(transaction.category, "Monthly Income")
        self.assertEqual(transaction.account_last4, "0140")
        self.assertEqual(transaction.source, "capital_one")
        self.assertEqual(transaction.source_file, "gmail:msg-1")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_parses_capital_one_deposit_messages_with_generic_subject(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_DEPOSIT_GENERIC_SUBJECT_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "Direct Deposit")
        self.assertEqual(transaction.amount, Decimal("3396.70"))
        self.assertEqual(transaction.category, "Monthly Income")
        self.assertEqual(transaction.account_last4, "0140")
        self.assertEqual(transaction.source, "capital_one")
        self.assertEqual(transaction.source_file, "gmail:msg-1")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_parses_capital_one_savor_charged_transaction_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_SAVOR_WHOLE_FOODS_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual(transaction.merchant, "Whole Foods Market")
        self.assertEqual(transaction.amount, Decimal("10.49"))
        self.assertEqual(transaction.category, "Groceries")
        self.assertEqual(transaction.account_last4, "5363")
        self.assertEqual(transaction.source, "capital_one")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_ignores_ambiguous_capital_one_synergy_charge(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_SYNERGY_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-1"])
        self.assertEqual(result.skipped_messages[0].reason, "Ambiguous Synergy merchant is intentionally ignored for manual handling.")

    def test_fetch_transactions_from_gmail_ignores_capital_one_discover_withdrawal_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_WITHDRAWAL_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-1"])
        self.assertEqual(
            result.skipped_messages[0].reason,
            "Discover withdrawal notice is intentionally ignored because it is tracked separately.",
        )

    def test_fetch_transactions_from_gmail_ignores_capital_one_venmo_withdrawal_messages(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_VENMO_WITHDRAWAL_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-1"])
        self.assertEqual(
            result.skipped_messages[0].reason,
            "Capital One Venmo funding withdrawal is intentionally ignored to avoid double counting.",
        )

    def test_fetch_transactions_from_gmail_parses_venmo_sent_and_received_html_messages(self) -> None:
        gmail_service = FakeGmailService(
            {
                "msg-sent": VENMO_SENT_EMAIL,
                "msg-received": VENMO_RECEIVED_EMAIL,
            }
        )

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:venmo@venmo.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(len(result.transactions), 2)
        by_file = {transaction.source_file: transaction for transaction in result.transactions}
        self.assertEqual(by_file["gmail:msg-sent"].merchant, "Bfast")
        self.assertEqual(by_file["gmail:msg-sent"].amount, Decimal("8.00"))
        self.assertEqual(by_file["gmail:msg-sent"].category, "Eating Out")
        self.assertEqual(by_file["gmail:msg-received"].merchant, "Example Payroll LLC")
        self.assertEqual(by_file["gmail:msg-received"].amount, Decimal("37.88"))
        self.assertEqual(by_file["gmail:msg-received"].category, "Monthly Income")
        self.assertEqual(result.skipped_message_ids, [])

    def test_fetch_transactions_from_gmail_ignores_wealthfront_brokerage_transfer(self) -> None:
        gmail_service = FakeGmailService({"msg-transfer": VENMO_TRANSFER_EMAIL})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:venmo@venmo.com newer_than:1d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-transfer"])
        self.assertEqual(
            result.skipped_messages[0].reason,
            "Transfer to or from Wealthfront is intentionally ignored.",
        )

    def test_fetch_transactions_from_gmail_ignores_discover_statement_and_payment_confirmation(self) -> None:
        gmail_service = FakeGmailService(
            {
                "msg-statement": DISCOVER_STATEMENT_EMAIL.encode("utf-8"),
                "msg-payment": DISCOVER_PAYMENT_RECEIVED_EMAIL.encode("utf-8"),
            }
        )

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:discover@services.discover.com newer_than:7d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-statement", "msg-payment"])
        self.assertEqual(result.skipped_messages[0].reason, "Statement notification, not a transaction alert.")
        self.assertEqual(result.skipped_messages[1].reason, "Card payment confirmation, not spending activity.")

    def test_fetch_transactions_from_gmail_ignores_capital_one_wealthfront_instant_payment(self) -> None:
        gmail_service = FakeGmailService({"msg-instant": CAPITAL_ONE_WEALTHFRONT_EMAIL.encode("utf-8")})

        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:capitalone@notification.capitalone.com newer_than:7d",
            max_results=10,
            categorizer=TransactionCategorizer(),
        )

        self.assertEqual(result.transactions, [])
        self.assertEqual(result.skipped_message_ids, ["msg-instant"])
        self.assertEqual(
            result.skipped_messages[0].reason,
            "Transfer to or from Wealthfront is intentionally ignored.",
        )

    def test_build_rows_for_sheet_exports(self) -> None:
        gmail_service = FakeGmailService({"msg-1": CAPITAL_ONE_EMAIL.encode("utf-8")})
        categorizer = TransactionCategorizer()
        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query="from:alerts@capitalone.com",
            max_results=10,
            categorizer=categorizer,
        )

        transaction_rows = build_transaction_rows(result.transactions)
        self.assertEqual(transaction_rows[0][:5], ["date", "merchant", "amount", "source", "category"])
        self.assertEqual(transaction_rows[1][1], "Whole Foods Market")
        self.assertEqual(transaction_rows[1][2], "18.79")
        self.assertEqual(transaction_rows[1][3], "capital_one")

        summary_rows = build_summary_rows(categorizer, result.transactions)
        self.assertEqual(summary_rows[0], ["category", "total"])
        self.assertIn(["Groceries", "18.79"], summary_rows)

    def test_decode_gmail_raw_message_accepts_unpadded_base64url_payloads(self) -> None:
        raw_payload = base64.urlsafe_b64encode(CAPITAL_ONE_EMAIL.encode("utf-8")).decode("ascii").rstrip("=")
        self.assertEqual(decode_gmail_raw_message(raw_payload), CAPITAL_ONE_EMAIL.encode("utf-8"))

    def test_monthly_sheet_detection_matches_live_budget_layout(self) -> None:
        self.assertTrue(
            is_monthly_budget_sheet(
                [
                    ["Month", "Monthly Income", "Monthly Pocket Change", "Monthly Expenses"],
                    ["Expected ", "$6,919", "-$35.29", "$6,954"],
                ]
            )
        )
        self.assertFalse(is_monthly_budget_sheet([["date", "merchant", "amount", "category"]]))

class FakeGmailService:
    def __init__(self, messages_by_id: dict[str, bytes | tuple[bytes, int]]) -> None:
        self.messages_by_id = messages_by_id

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> "FakeGmailService":
        return self

    def list(self, userId: str, q: str, maxResults: int, pageToken: str | None = None):
        del userId, q, pageToken
        return FakeExecute({"messages": [{"id": message_id} for message_id in self.messages_by_id]})

    def get(self, userId: str, id: str, format: str):
        del userId, format
        message_payload = self.messages_by_id[id]
        if isinstance(message_payload, tuple):
            raw_bytes, internal_date = message_payload
        else:
            raw_bytes = message_payload
            internal_date = 0
        raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
        return FakeExecute({"raw": raw, "internalDate": str(internal_date)})


class FakeExecute:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload
