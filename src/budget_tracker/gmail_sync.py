from __future__ import annotations

import base64
from dataclasses import dataclass

from .categorizer import TransactionCategorizer
from .models import Transaction
from .parser import extract_transaction_from_email_data, parse_email_bytes


@dataclass(slots=True)
class GmailSyncResult:
    transactions: list[Transaction]
    skipped_message_ids: list[str]
    processed_message_ids: list[str] | None = None


def fetch_transactions_from_gmail(
    gmail_service,
    query: str,
    max_results: int,
    categorizer: TransactionCategorizer | None = None,
    since_epoch_ms: int | None = None,
    since_internal_date_ms: int | None = None,
) -> GmailSyncResult:
    categorizer = categorizer or TransactionCategorizer()
    if since_epoch_ms is not None and since_internal_date_ms is None:
        since_internal_date_ms = since_epoch_ms
    parsed_transactions: list[Transaction] = []
    skipped_message_ids: list[str] = []
    processed_message_ids: list[str] = []
    page_token: str | None = None
    remaining = max_results

    while remaining > 0:
        response = _list_gmail_messages(
            gmail_service=gmail_service,
            query=query,
            max_results=min(remaining, 500),
            page_token=page_token,
        )
        messages = response.get("messages", [])
        if not messages:
            break

        for message in messages:
            message_id = message["id"]
            try:
                detail = (
                    gmail_service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="raw")
                    .execute()
                )
                internal_date = int(detail.get("internalDate", "0") or "0")
                if since_internal_date_ms is not None and internal_date <= since_internal_date_ms:
                    continue
                raw_payload = detail.get("raw")
                if not raw_payload:
                    skipped_message_ids.append(message_id)
                    continue
                raw_bytes = decode_gmail_raw_message(raw_payload)
                email_data = parse_email_bytes(raw_bytes)
                parsed = extract_transaction_from_email_data(email_data, source_file=f"gmail:{message_id}")
            except Exception:
                skipped_message_ids.append(message_id)
                continue

            processed_message_ids.append(message_id)
            if parsed is None:
                skipped_message_ids.append(message_id)
                continue
            parsed_transactions.append(categorizer.categorize(parsed))

        remaining = max_results - len(processed_message_ids)
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return GmailSyncResult(
        transactions=parsed_transactions,
        skipped_message_ids=skipped_message_ids,
        processed_message_ids=processed_message_ids,
    )


def decode_gmail_raw_message(raw_payload: str) -> bytes:
    padding = "=" * (-len(raw_payload) % 4)
    return base64.urlsafe_b64decode(raw_payload + padding)


def _list_gmail_messages(gmail_service, query: str, max_results: int, page_token: str | None):
    request_args = {
        "userId": "me",
        "q": query,
        "maxResults": max_results,
    }
    if page_token:
        request_args["pageToken"] = page_token
    messages_api = gmail_service.users().messages()
    try:
        return messages_api.list(**request_args).execute()
    except TypeError:
        request_args.pop("pageToken", None)
        return messages_api.list(**request_args).execute()


def build_transaction_rows(transactions: list[Transaction]) -> list[list[str]]:
    rows: list[list[str]] = [[
        "date",
        "merchant",
        "amount",
        "source",
        "category",
        "source_name",
        "source_file",
        "account_last4",
        "confidence",
        "raw_snippet",
    ]]
    for transaction in transactions:
        rows.append(
            [
                transaction.date,
                transaction.merchant,
                format(transaction.amount, "f"),
                transaction.source,
                transaction.category,
                transaction.source_name,
                transaction.source_file,
                transaction.account_last4 or "",
                f"{transaction.confidence:.4f}",
                transaction.raw_snippet,
            ]
        )
    return rows


def build_summary_rows(categorizer: TransactionCategorizer, transactions: list[Transaction]) -> list[list[str]]:
    summary = categorizer.summarize(transactions)
    rows = [["category", "total"]]
    for category, total in summary.items():
        rows.append([category, total])
    return rows
