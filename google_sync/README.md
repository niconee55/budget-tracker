# Google Sync Setup

This folder stores the Gmail and Google Sheets sync configuration for the budget tracker.

Files:

- `credentials.json`: Google Desktop OAuth client credentials.
- `token.json`: OAuth token generated after the first authorized sync run.
- `settings.json`: default Gmail sender metadata and Google Sheets destination settings.

Sync behavior:

- The Google Sheets flow reads the existing rows in the target tab before writing.
- New categorized transaction rows are appended to the end of the sheet.
- Sender-specific rules can be configured for bank and payment alerts, and HTML-only Gmail bodies are supported.

No scraping has been run as part of this setup.
