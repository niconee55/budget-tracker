# Google Sync Setup

This folder stores the Gmail and Google Sheets sync configuration for the budget tracker.

Files:

- `credentials.json`: Google Desktop OAuth client credentials.
- `token.json`: OAuth token generated after the first authorized sync run.
- `settings.json`: default Gmail sender metadata and Google Sheets destination settings.

Configured Gmail senders:

- `discover@services.discover.com`
- `venmo@venmo.com`

Configured Google Sheets target:

- Spreadsheet ID: `1y3sCfXcZSIe8kfvL6x3oq3tg1hnb6ZBXynM7Mct8hEU`
- Sheet tab: `Sheet1`

Sync behavior:

- The Google Sheets flow reads the existing rows in the target tab before writing.
- New categorized transaction rows are appended to the end of the sheet.
- Discover alerts from `discover@services.discover.com` are parsed with sender-specific rules, and HTML-only Gmail bodies are supported.

No scraping has been run as part of this setup.
