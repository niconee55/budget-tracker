# Google Sync Setup

This folder stores the Gmail and Google Sheets sync configuration for the budget tracker.

Files:

- `credentials.json`: Google Desktop OAuth client credentials.
- `token.json`: OAuth token generated after the first authorized sync run.
- `settings.json`: default Gmail sender metadata, Google Sheets destination settings, and the local database path.
- `budget_sync.db`: SQLite database containing `scrape_runs` and `transactions`.

Sync behavior:

- The Google Sheets flow reads the existing rows in the target tab before writing.
- Monthly budget sheets are updated cell-by-cell for the matching month/category intersection.
- Category cells are written as additive formulas so the amounts remain inspectable in Google Sheets.
- Sender-specific rules can be configured for bank and payment alerts, and HTML-only Gmail bodies are supported.
- The next sync only scans messages newer than the active baseline scrape run recorded in `budget_sync.db`.
- Rollbacks work by selecting an earlier scrape run from `scrape_runs` as the active baseline.
- Each parsed Gmail transaction is recorded in `transactions` with scrape metadata and deduplicated by Gmail message id.
- A normal sync does not need to create per-run JSON or CSV files. If local exports are wanted, reuse stable filenames instead of timestamped/test-specific files.

No scraping has been run as part of this setup.
