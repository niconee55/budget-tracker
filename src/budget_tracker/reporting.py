from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Transaction


def write_transactions(path: Path, transactions: list[Transaction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [transaction.to_dict() for transaction in transactions]
    path.write_text(json.dumps(payload, indent=2))


def write_transactions_csv(path: Path, transactions: list[Transaction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "date",
            "merchant",
            "amount",
            "source",
            "category",
            "assigned_category",
            "source_name",
            "source_file",
            "account_last4",
            "confidence",
            "raw_snippet",
        ])
        for transaction in transactions:
            writer.writerow([
                transaction.date,
                transaction.merchant,
                format(transaction.amount, "f"),
                transaction.source,
                transaction.category,
                transaction.category,
                transaction.source_name,
                transaction.source_file,
                transaction.account_last4 or "",
                f"{transaction.confidence:.4f}",
                transaction.raw_snippet,
            ])


def write_summary(path: Path, summary: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "total"])
        for category, total in summary.items():
            writer.writerow([category, total])
