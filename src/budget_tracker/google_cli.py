from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .categorizer import TransactionCategorizer, UNKNOWN_CATEGORY
from .config import load_google_sync_config
from .db import BudgetDatabase
from .gmail_sync import build_summary_rows, build_transaction_rows, fetch_transactions_from_gmail
from .google_auth import build_google_service, load_google_credentials
from .reporting import write_summary, write_transactions, write_transactions_csv
from .state import utc_now_iso
from .sheets_sync import (
    append_rows_to_sheet,
    read_budget_sheet_rows,
    read_rows_from_sheet,
    sync_monthly_budget_sheet,
)


LOCAL_TIMEZONE = ZoneInfo("America/New_York")


def build_parser() -> argparse.ArgumentParser:
    sync_config = _load_sync_defaults()
    paths = sync_config.get("paths", {})
    gmail_defaults = sync_config.get("gmail", {})
    sheets_defaults = sync_config.get("sheets", {})
    path_defaults = sync_config.get("paths", {})
    parser = argparse.ArgumentParser(
        description="Fetch transaction emails from Gmail and write categorized results to local files and Google Sheets."
    )
    parser.add_argument("--credentials-file", type=Path, default=Path(paths.get("credentials_file", "credentials.json")))
    parser.add_argument("--token-file", type=Path, default=Path(paths.get("token_file", "token.json")))
    parser.add_argument("--query", help="Gmail search query, for example: from:alerts@capitalone.com newer_than:30d")
    parser.add_argument("--max-results", type=int, default=int(gmail_defaults.get("max_results", 100)))
    parser.add_argument(
        "--db-file",
        "--state-file",
        dest="db_file",
        type=Path,
        default=Path(path_defaults.get("db_file", "google_sync/budget_sync.db")),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--spreadsheet-id", default=sheets_defaults.get("spreadsheet_id"))
    parser.add_argument("--sheet-name", default=sheets_defaults.get("sheet_name"))
    parser.add_argument("--summary-sheet-name", default=sheets_defaults.get("summary_sheet_name"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline without writing to Google Sheets or advancing the saved sync state.",
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
    if not hasattr(args, "db_file"):
        args.db_file = Path("google_sync/budget_sync.db")
    _validate_args(args)

    db = BudgetDatabase(args.db_file)
    db.ensure_schema()
    baseline_run = db.get_active_baseline_run()
    baseline_cursor = (
        baseline_run.completed_at_utc or baseline_run.started_at_utc
        if baseline_run
        else None
    )
    baseline_internal_date_ms = baseline_run.last_seen_internal_date_ms if baseline_run else None
    args.query = _augment_query_with_last_run(args.query, baseline_cursor)
    run_started_at = utc_now_iso()
    run_id = db.record_scrape_run(
        started_at_utc=run_started_at,
        status="dry_run" if args.dry_run else "started",
        gmail_query=args.query,
        max_results=args.max_results,
        notes="dry run" if args.dry_run else None,
    )
    last_updated_display = baseline_cursor or run_started_at
    print(f"Budget last updated: {_format_local_timestamp(last_updated_display)}")
    try:
        credentials = load_google_credentials(args.credentials_file, args.token_file)
        gmail_service = build_google_service("gmail", "v1", credentials)
        categorizer = TransactionCategorizer()
        result = fetch_transactions_from_gmail(
            gmail_service=gmail_service,
            query=args.query,
            max_results=args.max_results,
            categorizer=categorizer,
            since_internal_date_ms=baseline_internal_date_ms,
        )

        db.record_transactions(run_id, result.transactions, inserted_at_utc=run_started_at)

        if args.output_json:
            write_transactions(args.output_json, result.transactions)
        if args.summary_csv:
            write_summary(args.summary_csv, categorizer.summarize(result.transactions))
        write_transactions_csv(latest_scrape_csv, result.transactions)

        if args.spreadsheet_id and args.sheet_name and not getattr(args, "dry_run", False):
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
            _print_skipped_summary(result)

        run_completed_at = utc_now_iso()
        db.finalize_scrape_run(
            run_id,
            completed_at_utc=run_completed_at,
            status="dry_run" if args.dry_run else "completed",
            last_seen_internal_date_ms=result.max_internal_date_ms,
            is_baseline=not args.dry_run,
        )
    except Exception:
        db.finalize_scrape_run(
            run_id,
            completed_at_utc=utc_now_iso(),
            status="failed",
            last_seen_internal_date_ms=baseline_internal_date_ms,
            is_baseline=False,
            notes="pipeline failed",
        )
        raise

    return 0


def _validate_args(args: argparse.Namespace) -> None:
    max_results = getattr(args, "max_results", 0)
    query = getattr(args, "query", None)
    output_json = getattr(args, "output_json", None)
    summary_csv = getattr(args, "summary_csv", None)
    spreadsheet_id = getattr(args, "spreadsheet_id", None)
    sheet_name = getattr(args, "sheet_name", None)
    summary_sheet_name = getattr(args, "summary_sheet_name", None)
    dry_run = getattr(args, "dry_run", False)

    if hasattr(args, "query") and not query:
        raise SystemExit("--query is required")
    if max_results <= 0:
        raise SystemExit("--max-results must be greater than 0")
    if not any([output_json, summary_csv, spreadsheet_id]) and not dry_run:
        raise SystemExit("provide at least one output target: local files, sheet sync, or --dry-run")
    if bool(spreadsheet_id) != bool(sheet_name) and not dry_run:
        raise SystemExit("--spreadsheet-id and --sheet-name must be provided together")
    if summary_sheet_name and not spreadsheet_id and not dry_run:
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
    categorized_rows = [
        [
            transaction.date,
            f"${transaction.amount:.2f}",
            transaction.category,
            transaction.source,
            transaction.merchant,
        ]
        for transaction in categorized_transactions
    ]
    for line in _format_table_lines(categorized_rows):
        print(line)

    print("\n" + "-" * 48 + "\n")
    print("Unknown transactions:")
    unknown_rows = []
    for transaction in unknown_transactions:
        details = transaction.predicted_details_summary or transaction.merchant
        predicted_category = transaction.predicted_category or "Unknown"
        predicted_confidence = (
            f"{transaction.predicted_confidence:.4f}"
            if transaction.predicted_confidence is not None
            else "n/a"
        )
        predicted_rationale = transaction.predicted_rationale or "No Codex rationale available."
        unknown_rows.append(
            [
                transaction.date,
                f"${transaction.amount:.2f}",
                predicted_category,
                details,
                predicted_confidence,
                predicted_rationale,
            ]
        )
    for line in _format_table_lines(unknown_rows):
        print(line)


def _format_table_lines(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    column_widths = [
        max(len(row[index]) for row in rows)
        for index in range(len(rows[0]))
    ]
    formatted_lines: list[str] = []
    for row in rows:
        padded = []
        last_index = len(row) - 1
        for index, value in enumerate(row):
            if index == last_index:
                padded.append(value.ljust(column_widths[index]))
            else:
                padded.append(value.center(column_widths[index]))
        formatted_lines.append(f"- {' | '.join(padded)}")
    return formatted_lines


def _print_skipped_summary(result) -> None:
    print("\nSkipped messages:")
    skipped_messages = getattr(result, "skipped_messages", None) or []
    if skipped_messages:
        rows = [
            [
                skipped.subject or "(no subject)",
                skipped.sender or "(unknown sender)",
                skipped.reason,
            ]
            for skipped in skipped_messages
        ]
        for line in _format_table_lines(rows):
            print(line)
        return
    if result.skipped_message_ids:
        for message_id in result.skipped_message_ids:
            print(f"- {message_id}")
        return
    print("- none")


def _format_local_timestamp(value: str) -> str:
    dt = datetime.fromisoformat(value).astimezone(LOCAL_TIMEZONE)
    hour = dt.strftime("%I").lstrip("0") or "0"
    return f"{dt.strftime('%B')} {dt.day}, {dt.year} {hour}:{dt.strftime('%M %p %Z')}"


if __name__ == "__main__":
    raise SystemExit(main())
