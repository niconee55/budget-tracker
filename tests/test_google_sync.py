from __future__ import annotations

import base64
import unittest
from decimal import Decimal

from budget_tracker.categorizer import TransactionCategorizer
from budget_tracker.gmail_sync import (
    build_summary_rows,
    build_transaction_rows,
    decode_gmail_raw_message,
    fetch_transactions_from_gmail,
)
from budget_tracker.sheets_sync import (
    TRANSACTION_HEADER,
    append_rows_to_sheet,
    ensure_sheet_header,
    merge_transaction_rows,
    read_rows_from_sheet,
)


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
        self.assertEqual(transaction_rows[0][:4], ["date", "merchant", "amount", "category"])
        self.assertEqual(transaction_rows[1][1], "Whole Foods Market")
        self.assertEqual(transaction_rows[1][2], "18.79")

        summary_rows = build_summary_rows(categorizer, result.transactions)
        self.assertEqual(summary_rows[0], ["category", "total"])
        self.assertIn(["Groceries", "18.79"], summary_rows)

    def test_decode_gmail_raw_message_accepts_unpadded_base64url_payloads(self) -> None:
        raw_payload = base64.urlsafe_b64encode(CAPITAL_ONE_EMAIL.encode("utf-8")).decode("ascii").rstrip("=")
        self.assertEqual(decode_gmail_raw_message(raw_payload), CAPITAL_ONE_EMAIL.encode("utf-8"))

    def test_read_rows_from_sheet_returns_existing_sheet_data(self) -> None:
        sheets_service = FakeSheetsService(
            existing_rows=[
                ["date", "merchant", "amount", "category"],
                ["2026-03-20", "Existing Store", "12.34", "Groceries"],
            ]
        )

        rows = read_rows_from_sheet(
            sheets_service=sheets_service,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Transactions",
        )

        self.assertEqual(rows, sheets_service.existing_rows)
        self.assertEqual([call["action"] for call in sheets_service.calls], ["get"])
        self.assertEqual(sheets_service.calls[0]["range"], "Transactions")

    def test_merge_transaction_rows_skips_duplicates_and_keeps_new_rows(self) -> None:
        existing_rows = [
            TRANSACTION_HEADER,
            ["2026-03-20", "Existing Store", "12.34", "Groceries", "generic", "existing.txt", "1234", "0.9000", "snippet"],
        ]
        candidate_rows = [
            TRANSACTION_HEADER,
            ["2026-03-20", "Existing Store", "12.34", "Groceries", "generic", "existing.txt", "1234", "0.9000", "snippet"],
            ["2026-03-21", "Whole Foods Market", "18.79", "Groceries", "capital_one", "gmail:msg-1", "4242", "0.9800", "snippet"],
        ]

        merged = merge_transaction_rows(existing_rows, candidate_rows)

        self.assertEqual(
            merged,
            [["2026-03-21", "Whole Foods Market", "18.79", "Groceries", "capital_one", "gmail:msg-1", "4242", "0.9800", "snippet"]],
        )

    def test_append_rows_to_sheet_appends_only_new_rows(self) -> None:
        sheets_service = FakeSheetsService()
        rows = [["2026-03-21", "Whole Foods Market", "18.79", "Groceries"]]

        append_rows_to_sheet(
            sheets_service=sheets_service,
            spreadsheet_id="spreadsheet-123",
            sheet_name="Transactions",
            rows=rows,
        )

        self.assertEqual([call["action"] for call in sheets_service.calls], ["get", "append"])
        self.assertEqual(sheets_service.calls[0]["range"], "Transactions")
        self.assertEqual(sheets_service.calls[1]["range"], "Transactions!A1")
        self.assertEqual(sheets_service.calls[1]["body"]["values"], rows)
        self.assertEqual(sheets_service.calls[1]["insertDataOption"], "INSERT_ROWS")

    def test_ensure_sheet_header_returns_header_for_empty_sheet(self) -> None:
        self.assertEqual(ensure_sheet_header([], TRANSACTION_HEADER), [TRANSACTION_HEADER])


class FakeGmailService:
    def __init__(self, messages_by_id: dict[str, bytes]) -> None:
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
        raw = base64.urlsafe_b64encode(self.messages_by_id[id]).decode("ascii").rstrip("=")
        return FakeExecute({"raw": raw})


class FakeSheetsService:
    def __init__(self, existing_rows: list[list[str]] | None = None) -> None:
        self.existing_rows = existing_rows or []
        self.calls: list[dict[str, object]] = []

    def spreadsheets(self) -> "FakeSheetsService":
        return self

    def values(self) -> "FakeSheetsService":
        return self

    def get(self, spreadsheetId: str, range: str):
        self.calls.append(
            {
                "action": "get",
                "spreadsheetId": spreadsheetId,
                "range": range,
            }
        )
        return FakeExecute({"values": self.existing_rows})

    def append(self, spreadsheetId: str, range: str, valueInputOption: str, insertDataOption: str, body: dict[str, object]):
        self.calls.append(
            {
                "action": "append",
                "spreadsheetId": spreadsheetId,
                "range": range,
                "valueInputOption": valueInputOption,
                "insertDataOption": insertDataOption,
                "body": body,
            }
        )
        return FakeExecute({})


class FakeExecute:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload
