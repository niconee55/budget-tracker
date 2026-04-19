from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.auth.exceptions import RefreshError

from budget_tracker.google_auth import _is_revoked_or_expired_token_error, load_google_credentials


class GoogleAuthTests(unittest.TestCase):
    def test_detects_revoked_or_expired_refresh_errors(self) -> None:
        exc = Exception("invalid_grant: Token has been expired or revoked.")
        self.assertTrue(_is_revoked_or_expired_token_error(exc))

    def test_load_google_credentials_reauths_after_revoked_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            credentials_file = root / "credentials.json"
            token_file = root / "token.json"
            credentials_file.write_text('{"installed": {"client_id": "x"}}')
            token_file.write_text('{"refresh_token": "stale"}')

            refreshed_creds = _FakeCredentials(valid=True, expired=False, refresh_token="fresh")
            stale_creds = _FakeCredentials(valid=False, expired=True, refresh_token="stale")
            flow = _FakeFlow(refreshed_creds)
            refresh_error = RefreshError(
                "invalid_grant: Token has been expired or revoked.",
                {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
            )

            with (
                patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=stale_creds),
                patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", return_value=flow),
                patch("google.auth.transport.requests.Request", return_value=object()),
                patch.object(stale_creds, "refresh", side_effect=refresh_error),
            ):
                creds = load_google_credentials(credentials_file, token_file)

            self.assertIs(creds, refreshed_creds)
            self.assertEqual(flow.run_local_server_calls, 1)
            self.assertTrue(token_file.exists())
            self.assertEqual(token_file.read_text(), refreshed_creds.to_json())


class _FakeCredentials:
    def __init__(self, *, valid: bool, expired: bool, refresh_token: str | None) -> None:
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token

    def refresh(self, request) -> None:
        del request

    def to_json(self) -> str:
        return '{"token": "fresh"}'


class _FakeFlow:
    def __init__(self, creds: _FakeCredentials) -> None:
        self.creds = creds
        self.run_local_server_calls = 0

    def run_local_server(self, port: int = 0):
        del port
        self.run_local_server_calls += 1
        return self.creds
