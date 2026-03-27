from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Transaction


def write_transactions(path: Path, transactions: list[Transaction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [transaction.to_dict() for transaction in transactions]
    path.write_text(json.dumps(payload, indent=2))


def write_summary(path: Path, summary: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "total"])
        for category, total in summary.items():
            writer.writerow([category, total])
