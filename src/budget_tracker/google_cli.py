from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .categorizer import TransactionCategorizer, UNKNOWN_CATEGORY
from .config import load_google_sync_config
from .gmail_sync import build_summary_rows, build_transaction_rows, fetch_transactions_from_gmail
from .google_auth import build_google_service, load_google_credentials
from .reporting import write_summary, write_transactions, write_transactions_csv
from .state import SyncState, iso_to_epoch_millis, load_sync_state, save_sync_state, utc_now_iso
from .sheets_sync import (
    append_rows_to_sheet,
    read_budget_sheet_rows,
    read_rows_from_sheet,
    sync_monthly_budget_sheet,
)


def build_parser() -> argparse.ArgumentParser:
    sync_config = _load_sync_defaults()
    paths = sync_config.get("paths", {})
    gmail_defaults = sync_config.get("gmail", {})
    sheets_defaults = sync_config.get("sheets", {})
    state_defaults = sync_config.get("state", {})
    parser = argparse.ArgumentParser(
        description="Fetch transaction emails from Gmail and write categorized results to local files and Google Sheets."
    )
    parser.add_argument("--credentials-file", type=Path, default=Path(paths.get("credentials_file", "credentials.json")))
    parser.add_argument("--token-file", type=Path, default=Path(paths.get("token_file", "token.json")))
    parser.add_argument("--query", help="Gmail search query, for example: from:alerts@capitalone.com newer_than:30d")
    parser.add_argument("--max-results", type=int, default=int(gmail_defaults.get("max_results", 100)))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(state_defaults.get("run_state_file", paths.get("state_file", "google_sync/run_state.json"))),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--spreadsheet-id", default=sheets_defaults.get("spreadsheet_id"))
    parser.add_argument("--sheet-name", default=sheets_defaults.get("sheet_name"))
    parser.add_argument("--summary-sheet-name", default=sheets_defaults.get("summary_sheet_name"))
    parser.add_argument(
        "--skip-sheet-update",
        action="store_true",
        help="Fetch and categorize transactions without writing to Google Sheets.",
    )
    parser.add_argument(
        "--clear-sheet",
        action="store_true",
        help="Legacy flag kept for compatibility. Sheets are read first and appended to instead of being cleared.",
    )
    parser.add_argument("--show-skipped", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    latest_scrape_csv = _latest_scrape_csv_path()
    if not args.query:
        args.query = _default_query(_load_sync_defaults())
    if not hasattr(args, "state_file"):
        args.state_file = Path("google_sync/run_state.json")
    _validate_args(args)

    sync_state = load_sync_state(args.state_file)
    args.query = _augment_query_with_last_run(args.query, sync_state.last_successful_run_utc)
    run_started_at = utc_now_iso()
    credentials = load_google_credentials(args.credentials_file, args.token_file)
    gmail_service = build_google_service("gmail", "v1", credentials)
    categorizer = TransactionCategorizer()
    result = fetch_transactions_from_gmail(
        gmail_service=gmail_service,
        query=args.query,
        max_results=args.max_results,
        categorizer=categorizer,
        since_epoch_ms=iso_to_epoch_millis(sync_state.last_successful_run_utc),
    )

    if args.output_json:
        write_transactions(args.output_json, result.transactions)
    if args.summary_csv:
        write_summary(args.summary_csv, categorizer.summarize(result.transactions))
    write_transactions_csv(latest_scrape_csv, result.transactions)

    skip_sheet_update = getattr(args, "skip_sheet_update", False)
    if args.spreadsheet_id and args.sheet_name and not skip_sheet_update:
        sheets_service = build_google_service("sheets", "v4", credentials)
        existing_rows = read_budget_sheet_rows(
            sheets_service=sheets_service,
            spreadsheet_id=args.spreadsheet_id,
            sheet_name=args.sheet_name,
        )
        sync_monthly_budget_sheet(
            sheets_service=sheets_service,
            spreadsheet_id=args.spreadsheet_id,
            sheet_name=args.sheet_name,
            transactions=result.transactions,
            existing_rows=existing_rows,
        )

    _print_transaction_summary(result.transactions)

    if args.show_skipped:
        print(json.dumps({"skipped_message_ids": result.skipped_message_ids}, indent=2))

    save_sync_state(
        args.state_file,
        SyncState(last_successful_run_utc=run_started_at),
    )
    return 0


def _validate_args(args: argparse.Namespace) -> None:
    max_results = getattr(args, "max_results", 0)
    query = getattr(args, "query", None)
    output_json = getattr(args, "output_json", None)
    summary_csv = getattr(args, "summary_csv", None)
    spreadsheet_id = getattr(args, "spreadsheet_id", None)
    sheet_name = getattr(args, "sheet_name", None)
    summary_sheet_name = getattr(args, "summary_sheet_name", None)
    skip_sheet_update = getattr(args, "skip_sheet_update", False)

    if hasattr(args, "query") and not query:
        raise SystemExit("--query is required")
    if max_results <= 0:
        raise SystemExit("--max-results must be greater than 0")
    if not any([output_json, summary_csv, spreadsheet_id]) and not skip_sheet_update:
        raise SystemExit("provide at least one output target: local files, sheet sync, or --skip-sheet-update")
    if bool(spreadsheet_id) != bool(sheet_name) and not skip_sheet_update:
        raise SystemExit("--spreadsheet-id and --sheet-name must be provided together")
    if summary_sheet_name and not spreadsheet_id and not skip_sheet_update:
        raise SystemExit("--summary-sheet-name requires --spreadsheet-id and --sheet-name")


def _load_sync_defaults() -> dict[str, object]:
    try:
        return load_google_sync_config()
    except FileNotFoundError:
        return {}


def _latest_scrape_csv_path() -> Path:
    sync_config = _load_sync_defaults()
    paths = sync_config.get("paths", {})
    latest_scrape_csv = paths.get("latest_scrape_csv")
    if latest_scrape_csv:
        return Path(str(latest_scrape_csv))
    return Path("google_sync/latest_scrape.csv")


def _default_query(sync_config: dict[str, object]) -> str:
    gmail_defaults = sync_config.get("gmail", {})
    senders = gmail_defaults.get("senders", [])
    emails = [entry["email"] for entry in senders if isinstance(entry, dict) and entry.get("email")]
    if not emails:
        return ""
    sender_terms = " OR ".join(f"from:{email}" for email in emails)
    return f"({sender_terms})"


def _augment_query_with_last_run(query: str, last_successful_run_utc: str | None) -> str:
    if not last_successful_run_utc:
        return query
    lowered = query.lower()
    if "after:" in lowered or "newer_than:" in lowered or "older_than:" in lowered:
        return query
    run_date = datetime.fromisoformat(last_successful_run_utc).date().strftime("%Y/%m/%d")
    if not query:
        return f"after:{run_date}"
    return f"({query}) after:{run_date}"


def _print_transaction_summary(transactions) -> None:
    categorized_transactions = [
        transaction for transaction in transactions if transaction.category != UNKNOWN_CATEGORY
    ]
    unknown_transactions = [
        transaction for transaction in transactions if transaction.category == UNKNOWN_CATEGORY
    ]

    print("Categorized transactions:")
    for transaction in categorized_transactions:
        print(
            f"- {transaction.date} | ${transaction.amount:.2f} | "
            f"{transaction.source} | {transaction.category} | {transaction.merchant}"
        )

    print("\n" + "-" * 48 + "\n")
    print("Unknown transactions:")
    for transaction in unknown_transactions:
        details = transaction.predicted_details_summary or transaction.merchant
        predicted_category = transaction.predicted_category or "Unknown"
        predicted_confidence = (
            f"{transaction.predicted_confidence:.4f}"
            if transaction.predicted_confidence is not None
            else "n/a"
        )
        predicted_rationale = transaction.predicted_rationale or "No Codex rationale available."
        print(
            f"- {transaction.date} | ${transaction.amount:.2f} | "
            f"{details} | {predicted_category} | {predicted_confidence} | {predicted_rationale}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
