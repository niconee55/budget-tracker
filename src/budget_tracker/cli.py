from __future__ import annotations

import argparse
from pathlib import Path

from .categorizer import TransactionCategorizer
from .parser import extract_transactions
from .reporting import write_summary, write_transactions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and categorize budget transactions from local email exports.")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--summary-csv", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    parsed_transactions = extract_transactions(args.input_dir)
    categorizer = TransactionCategorizer()
    transactions = [categorizer.categorize(parsed) for parsed in parsed_transactions]
    summary = categorizer.summarize(transactions)
    write_transactions(args.output_json, transactions)
    write_summary(args.summary_csv, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
