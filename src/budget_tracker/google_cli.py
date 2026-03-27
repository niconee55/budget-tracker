from __future__ import annotations

import argparse
import json
from pathlib import Path

from .categorizer import TransactionCategorizer
from .config import load_google_sync_config
from .gmail_sync import build_summary_rows, build_transaction_rows, fetch_transactions_from_gmail
from .google_auth import build_google_service, load_google_credentials
from .reporting import write_summary, write_transactions
from .sheets_sync import (
    TRANSACTION_HEADER,
    append_rows_to_sheet,
    ensure_sheet_header,
    merge_transaction_rows,
    read_rows_from_sheet,
)


def build_parser() -> argparse.ArgumentParser:
    sync_config = _load_sync_defaults()
    paths = sync_config.get("paths", {})
    gmail_defaults = sync_config.get("gmail", {})
    sheets_defaults = sync_config.get("sheets", {})
    parser = argparse.ArgumentParser(
        description="Fetch transaction emails from Gmail and write categorized results to local files and Google Sheets."
    )
    parser.add_argument("--credentials-file", type=Path, default=Path(paths.get("credentials_file", "credentials.json")))
    parser.add_argument("--token-file", type=Path, default=Path(paths.get("token_file", "token.json")))
    parser.add_argument("--query", help="Gmail search query, for example: from:alerts@capitalone.com newer_than:30d")
    parser.add_argument("--max-results", type=int, default=int(gmail_defaults.get("max_results", 100)))
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--spreadsheet-id", default=sheets_defaults.get("spreadsheet_id"))
    parser.add_argument("--sheet-name", default=sheets_defaults.get("sheet_name"))
    parser.add_argument("--summary-sheet-name", default=sheets_defaults.get("summary_sheet_name"))
    parser.add_argument(
        "--clear-sheet",
        action="store_true",
        help="Legacy flag kept for compatibility. Sheets are read first and appended to instead of being cleared.",
    )
    parser.add_argument("--show-skipped", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    _validate_args(args)

    credentials = load_google_credentials(args.credentials_file, args.token_file)
    gmail_service = build_google_service("gmail", "v1", credentials)
    categorizer = TransactionCategorizer()
    result = fetch_transactions_from_gmail(
        gmail_service=gmail_service,
        query=args.query,
        max_results=args.max_results,
        categorizer=categorizer,
    )

    if args.output_json:
        write_transactions(args.output_json, result.transactions)
    if args.summary_csv:
        write_summary(args.summary_csv, categorizer.summarize(result.transactions))

    if args.spreadsheet_id and args.sheet_name:
        sheets_service = build_google_service("sheets", "v4", credentials)
        existing_rows = read_rows_from_sheet(
            sheets_service=sheets_service,
            spreadsheet_id=args.spreadsheet_id,
            sheet_name=args.sheet_name,
        )
        transaction_rows = build_transaction_rows(result.transactions)
        rows_to_append = [
            *ensure_sheet_header(existing_rows, TRANSACTION_HEADER),
            *merge_transaction_rows(existing_rows, transaction_rows),
        ]
        append_rows_to_sheet(
            sheets_service=sheets_service,
            spreadsheet_id=args.spreadsheet_id,
            sheet_name=args.sheet_name,
            rows=rows_to_append,
        )
        if args.summary_sheet_name:
            summary_rows = build_summary_rows(categorizer, result.transactions)
            existing_summary_rows = read_rows_from_sheet(
                sheets_service=sheets_service,
                spreadsheet_id=args.spreadsheet_id,
                sheet_name=args.summary_sheet_name,
            )
            summary_rows_to_append = [
                *ensure_sheet_header(existing_summary_rows, summary_rows[0]),
                *summary_rows[1:],
            ]
            append_rows_to_sheet(
                sheets_service=sheets_service,
                spreadsheet_id=args.spreadsheet_id,
                sheet_name=args.summary_sheet_name,
                rows=summary_rows_to_append,
            )

    if args.show_skipped:
        print(json.dumps({"skipped_message_ids": result.skipped_message_ids}, indent=2))

    return 0


def _validate_args(args: argparse.Namespace) -> None:
    max_results = getattr(args, "max_results", 0)
    query = getattr(args, "query", None)
    output_json = getattr(args, "output_json", None)
    summary_csv = getattr(args, "summary_csv", None)
    spreadsheet_id = getattr(args, "spreadsheet_id", None)
    sheet_name = getattr(args, "sheet_name", None)
    summary_sheet_name = getattr(args, "summary_sheet_name", None)

    if hasattr(args, "query") and not query:
        raise SystemExit("--query is required")
    if max_results <= 0:
        raise SystemExit("--max-results must be greater than 0")
    if not any([output_json, summary_csv, spreadsheet_id]):
        raise SystemExit("provide at least one output target: local files or --spreadsheet-id/--sheet-name")
    if bool(spreadsheet_id) != bool(sheet_name):
        raise SystemExit("--spreadsheet-id and --sheet-name must be provided together")
    if summary_sheet_name and not spreadsheet_id:
        raise SystemExit("--summary-sheet-name requires --spreadsheet-id and --sheet-name")


def _load_sync_defaults() -> dict[str, object]:
    try:
        return load_google_sync_config()
    except FileNotFoundError:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
