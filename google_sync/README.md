# Google Sync Setup

This folder stores the Gmail and Google Sheets sync configuration for the budget tracker.

Files:

- `credentials.json`: Google Desktop OAuth client credentials.
- `token.json`: OAuth token generated after the first authorized sync run.
- `settings.json`: default Gmail sender metadata, Google Sheets destination settings, and the local run-state file path.
- `run_state.json`: last successful Gmail sync timestamp, written after successful runs.

Sync behavior:

- The Google Sheets flow reads the existing rows in the target tab before writing.
- Monthly budget sheets are updated cell-by-cell for the matching month/category intersection.
- Category cells are written as additive formulas so the amounts remain inspectable in Google Sheets.
- Sender-specific rules can be configured for bank and payment alerts, and HTML-only Gmail bodies are supported.
- The next sync only scans messages newer than the last successful run timestamp.
- A normal sync does not need to create per-run JSON or CSV files. If local exports are wanted, reuse stable filenames instead of timestamped/test-specific files.

No scraping has been run as part of this setup.
