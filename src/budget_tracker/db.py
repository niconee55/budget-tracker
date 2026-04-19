from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import Transaction


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL UNIQUE,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    gmail_query TEXT NOT NULL DEFAULT '',
    max_results INTEGER NOT NULL DEFAULT 0,
    last_seen_internal_date_ms INTEGER,
    is_baseline INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_run_id INTEGER NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    gmail_internal_date_ms INTEGER,
    date TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount TEXT NOT NULL,
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category TEXT NOT NULL,
    predicted_category TEXT,
    predicted_confidence REAL,
    predicted_rationale TEXT,
    predicted_details_summary TEXT,
    account_last4 TEXT,
    raw_snippet TEXT NOT NULL,
    source_file TEXT NOT NULL,
    inserted_at_utc TEXT NOT NULL,
    UNIQUE(gmail_message_id)
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_started_at_utc ON scrape_runs(started_at_utc);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_is_baseline ON scrape_runs(is_baseline);
CREATE INDEX IF NOT EXISTS idx_transactions_scrape_run_id ON transactions(scrape_run_id);
CREATE INDEX IF NOT EXISTS idx_transactions_gmail_internal_date_ms ON transactions(gmail_internal_date_ms);
"""


@dataclass(slots=True)
class ScrapeRun:
    id: int
    started_at_utc: str
    completed_at_utc: str | None
    status: str
    gmail_query: str
    max_results: int
    last_seen_internal_date_ms: int | None
    is_baseline: bool
    notes: str | None


class BudgetDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def record_scrape_run(
        self,
        *,
        started_at_utc: str,
        status: str,
        gmail_query: str,
        max_results: int,
        notes: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scrape_runs (
                    started_at_utc, completed_at_utc, status, gmail_query, max_results,
                    last_seen_internal_date_ms, is_baseline, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (started_at_utc, None, status, gmail_query, max_results, None, 0, notes),
            )
            return int(cursor.lastrowid)

    def finalize_scrape_run(
        self,
        scrape_run_id: int,
        *,
        completed_at_utc: str,
        status: str,
        last_seen_internal_date_ms: int | None,
        is_baseline: bool,
        notes: str | None = None,
    ) -> None:
        with self.connect() as conn:
            if is_baseline:
                conn.execute("UPDATE scrape_runs SET is_baseline = 0")
            conn.execute(
                """
                UPDATE scrape_runs
                SET completed_at_utc = ?, status = ?, last_seen_internal_date_ms = ?, is_baseline = ?, notes = COALESCE(?, notes)
                WHERE id = ?
                """,
                (
                    completed_at_utc,
                    status,
                    last_seen_internal_date_ms,
                    1 if is_baseline else 0,
                    notes,
                    scrape_run_id,
                ),
            )

    def set_active_baseline_run(self, scrape_run_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE scrape_runs SET is_baseline = 0")
            conn.execute("UPDATE scrape_runs SET is_baseline = 1 WHERE id = ?", (scrape_run_id,))

    def get_active_baseline_run(self) -> ScrapeRun | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, started_at_utc, completed_at_utc, status, gmail_query, max_results,
                       last_seen_internal_date_ms, is_baseline, notes
                FROM scrape_runs
                WHERE is_baseline = 1
                ORDER BY completed_at_utc DESC, started_at_utc DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return _row_to_scrape_run(row) if row else None

    def get_scrape_run_at_or_before(self, started_at_utc: str) -> ScrapeRun | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, started_at_utc, completed_at_utc, status, gmail_query, max_results,
                       last_seen_internal_date_ms, is_baseline, notes
                FROM scrape_runs
                WHERE started_at_utc <= ?
                  AND status != 'dry_run'
                ORDER BY started_at_utc DESC, id DESC
                LIMIT 1
                """,
                (started_at_utc,),
            ).fetchone()
        return _row_to_scrape_run(row) if row else None

    def list_scrape_runs(self) -> list[ScrapeRun]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at_utc, completed_at_utc, status, gmail_query, max_results,
                       last_seen_internal_date_ms, is_baseline, notes
                FROM scrape_runs
                ORDER BY started_at_utc ASC, id ASC
                """
            ).fetchall()
        return [_row_to_scrape_run(row) for row in rows]

    def record_transactions(
        self,
        scrape_run_id: int,
        transactions: Iterable[Transaction],
        *,
        inserted_at_utc: str,
    ) -> int:
        inserted = 0
        with self.connect() as conn:
            for transaction in transactions:
                gmail_message_id = _gmail_message_id_from_source_file(transaction.source_file)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions (
                        scrape_run_id, gmail_message_id, gmail_thread_id, gmail_internal_date_ms,
                        date, merchant, amount, source, source_name, category,
                        predicted_category, predicted_confidence, predicted_rationale,
                        predicted_details_summary, account_last4, raw_snippet,
                        source_file, inserted_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scrape_run_id,
                        gmail_message_id,
                        None,
                        None,
                        transaction.date,
                        transaction.merchant,
                        format(transaction.amount, "f"),
                        transaction.source,
                        transaction.source_name,
                        transaction.category,
                        transaction.predicted_category,
                        transaction.predicted_confidence,
                        transaction.predicted_rationale,
                        transaction.predicted_details_summary,
                        transaction.account_last4,
                        transaction.raw_snippet,
                        transaction.source_file,
                        inserted_at_utc,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        return inserted


def _row_to_scrape_run(row: sqlite3.Row) -> ScrapeRun:
    return ScrapeRun(
        id=int(row["id"]),
        started_at_utc=str(row["started_at_utc"]),
        completed_at_utc=str(row["completed_at_utc"]) if row["completed_at_utc"] is not None else None,
        status=str(row["status"]),
        gmail_query=str(row["gmail_query"]),
        max_results=int(row["max_results"]),
        last_seen_internal_date_ms=(
            int(row["last_seen_internal_date_ms"]) if row["last_seen_internal_date_ms"] is not None else None
        ),
        is_baseline=bool(row["is_baseline"]),
        notes=str(row["notes"]) if row["notes"] is not None else None,
    )
def _gmail_message_id_from_source_file(source_file: str) -> str | None:
    if source_file.startswith("gmail:"):
        message_id = source_file.split(":", 1)[1].strip()
        return message_id or None
    return None
