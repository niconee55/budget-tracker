# Budget Tracker

This project can scan either local email exports or Gmail messages, extract transaction-like events, and assign each one to a budget category.

Supported categories:

- Monthly Income
- Utilities
- Groceries
- Transportation
- Eating Out
- Healthcare
- Clothes/Personal Care
- Housing Supplies
- Entertainment
- Unknown

## What It Does

- Reads `.eml` files and plain text email dumps from a local folder.
- Fetches matching Gmail messages through the Gmail API using OAuth.
- Extracts transaction date, merchant, amount, last 4 digits when present, source filename, and a raw snippet.
- Handles plaintext and HTML-only email bodies, including bank and card transaction alerts.
- Applies a hybrid categorizer:
  - direct keyword and merchant rules
  - vector-style cosine similarity over tokenized category descriptors and merchant knowledge
  - optional OpenAI web-search categorization for unfamiliar businesses, with local caching and confidence-aware categorization
- Can optionally write a JSON export of categorized transactions and a CSV category summary.
- Always rewrites a stable local CSV snapshot of the most recent scrape at `google_sync/latest_scrape.csv`.
- Can update a monthly Google Sheets budget matrix by reading the existing sheet, matching the month in column A, and updating only the category cells for that month with formula-style additive strings so the individual transaction amounts remain inspectable.
- Stores scrape runs and transaction history in a local SQLite database so the next run only scans newer mail and rollbacks can target prior scrape checkpoints.
- Prints categorized transactions separately from `Unknown` transactions, including Codex prediction details for unknowns when available.

## Run

```bash
PYTHONPATH=src ./venv/bin/python -m budget_tracker.cli \
  --input-dir tests/fixtures/emails \
  --output-json output/transactions.json \
  --summary-csv output/summary.csv
```

## Gmail And Sheets Sync

Install the Google client dependencies only if you want the Gmail/Sheets path:

```bash
python3 -m pip install -e '.[google]'
```

1. Create a Google Cloud project.
2. Enable the Gmail API and Google Sheets API.
3. Create Desktop OAuth credentials and download the client JSON as `credentials.json`.
4. Run the Google sync command. The first run opens a browser for OAuth consent and saves `token.json`.
5. Reuse the same `token.json` on later runs. If scopes or the client change, delete it and re-authenticate.
6. The real budget matrix uses `Month` in column A and category headers across row 1. The updater reads the sheet first, finds the matching month row, and adds totals into the correct category cells instead of appending transactions.
7. The writable sheet columns are limited to `F,G,H,I,K,L,M,N,V`, where `V` is `Unknown`.
8. The updater writes additive formulas such as `=8.00+2.00+3.00` into the target cell rather than replacing the cell with a bare sum.
9. Successful Gmail syncs are recorded in `google_sync/budget_sync.db`. The active baseline scrape run is used to limit the next Gmail scan.
10. If `google_sync/settings.json` defines Gmail sender addresses, the CLI builds the default Gmail query from those senders automatically.

Normal sheet sync with no extra local artifacts:

```bash
PYTHONPATH=src .venv/bin/python -m budget_tracker.google_cli --show-skipped
```

That run still refreshes:

```text
google_sync/latest_scrape.csv
```

Optional local exports with stable reusable filenames:

```bash
PYTHONPATH=src .venv/bin/python -m budget_tracker.google_cli \
  --output-json google_sync/output/transactions_latest.json \
  --summary-csv google_sync/output/summary_latest.csv \
  --show-skipped
```

Test a scrape without writing to Google Sheets:

```bash
PYTHONPATH=src .venv/bin/python -m budget_tracker.google_cli \
  --dry-run \
  --show-skipped
```

Key flags:

- `--query` is the Gmail search query used to select messages. If omitted, the CLI builds one from the configured sender list in `google_sync/settings.json`.
- `--output-json` and `--summary-csv` are optional. If you omit them, the run updates only Google Sheets and the local scrape history database.
- `--spreadsheet-id` and `--sheet-name` read the existing tab and update the matching month row in place when the sheet is in monthly budget format.
- `--summary-sheet-name` is kept for compatibility with legacy layouts.
- `--dry-run` runs the full fetch and categorization pipeline without touching Google Sheets or advancing the active baseline scrape run in the local database.
- `--show-skipped` prints Gmail message IDs that did not parse into transactions.
- `--db-file` overrides the local scrape history database path. `--state-file` is kept as a legacy alias for the same value.

## Stored Sync Config

Google sync defaults can live in `google_sync/settings.json`. The OAuth client file and future token file can live in the same `google_sync/` folder.

This lets you keep the Gmail sender list, credentials path, token path, local artifact paths, database path, and Google Sheets destination together in one place without running a sync immediately.

The Google command keeps imports lazy, so the local parsing CLI and the test suite can still run without Google packages installed.

## Test

```bash
PYTHONPATH=src ./venv/bin/python -m unittest discover -s tests -v
```

## Configuration

`config/email_sources.json` contains source-specific parsing rules. It ships with generic patterns and a placeholder Capital One source so additional email templates can be added without code changes.

`config/merchant_knowledge.json` contains merchant aliases and descriptive text used by the categorizer.
`config/categories.json` mirrors the budget categories used by the sheet, including the `Unknown` review bucket.

Merchant lookup is enabled by default for merchants that do not match the local rules. It uses the local `codex` CLI in non-interactive mode with web search enabled, caches results in `google_sync/merchant_lookup_cache.json`, and falls back silently when Codex is unavailable. Set `BUDGET_TRACKER_ENABLE_MERCHANT_LOOKUP=0` to disable it. You can override the model with `BUDGET_TRACKER_MERCHANT_LOOKUP_MODEL` and the binary with `BUDGET_TRACKER_CODEX_BIN`.

`google_sync/budget_sync.db` stores both `scrape_runs` and `transactions`. `scrape_runs` is the rollback and baseline source of truth. `transactions` stores the parsed transaction records plus scrape metadata and deduplicates Gmail messages by `gmail_message_id`.
