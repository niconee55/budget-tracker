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

## What It Does

- Reads `.eml` files and plain text email dumps from a local folder.
- Fetches matching Gmail messages through the Gmail API using OAuth.
- Extracts transaction date, merchant, amount, last 4 digits when present, source filename, and a raw snippet.
- Handles plaintext and HTML-only email bodies, including bank and card transaction alerts.
- Applies a hybrid categorizer:
  - direct keyword and merchant rules
  - vector-style cosine similarity over tokenized category descriptors and merchant knowledge
- Writes a JSON export of categorized transactions and a CSV category summary.
- Can write transaction and summary rows to Google Sheets through the Sheets API.

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
6. The Google Sheets flow reads the current sheet first, then appends only new categorized rows to the end.

```bash
PYTHONPATH=src python3 -m budget_tracker.google_cli \
  --query 'from:bank-alerts@example.com newer_than:30d' \
  --max-results 100 \
  --output-json output/gmail-transactions.json \
  --summary-csv output/gmail-summary.csv \
  --spreadsheet-id YOUR_SPREADSHEET_ID \
  --sheet-name Transactions \
  --summary-sheet-name Summary
```

Key flags:

- `--query` is the Gmail search query used to select messages.
- `--output-json`, `--summary-csv`, or `--spreadsheet-id`/`--sheet-name` must be provided so the command has somewhere to write results.
- `--spreadsheet-id` and `--sheet-name` read the existing tab and append new transaction rows to the end.
- `--summary-sheet-name` optionally writes category totals to a second tab.
- `--show-skipped` prints Gmail message IDs that did not parse into transactions.

## Stored Sync Config

Google sync defaults can live in `google_sync/settings.json`. The OAuth client file and future token file can live in the same `google_sync/` folder.

This lets you keep the Gmail sender list, credentials path, token path, and Google Sheets destination together in one place without running a sync immediately.

The Google command keeps imports lazy, so the local parsing CLI and the test suite can still run without Google packages installed.

## Test

```bash
PYTHONPATH=src ./venv/bin/python -m unittest discover -s tests -v
```

## Configuration

`config/email_sources.json` contains source-specific parsing rules. It ships with generic patterns and a placeholder Capital One source so additional email templates can be added without code changes.

`config/merchant_knowledge.json` contains merchant aliases and descriptive text used by the categorizer.
