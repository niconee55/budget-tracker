from __future__ import annotations

from pathlib import Path


GOOGLE_API_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def load_google_credentials(credentials_file: Path, token_file: Path, scopes: list[str] | None = None):
    scopes = scopes or GOOGLE_API_SCOPES
    try:
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Google API dependencies are missing. Install google-api-python-client, "
            "google-auth, google-auth-oauthlib, and google-auth-httplib2."
        ) from exc

    credentials_file = credentials_file.expanduser()
    token_file = token_file.expanduser()
    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        except ValueError:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
        except RefreshError as exc:
            if _is_revoked_or_expired_token_error(exc):
                creds = None
                if token_file.exists():
                    token_file.unlink()
            else:
                raise

    if not creds or not creds.valid:
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Google OAuth client file not found: {credentials_file}. "
                "Create a Desktop OAuth client in Google Cloud and download the JSON file."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
        creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())
    return creds


def _is_revoked_or_expired_token_error(exc: Exception) -> bool:
    details = " ".join(str(arg) for arg in getattr(exc, "args", ()))
    lowered = details.lower()
    return "invalid_grant" in lowered or "expired or revoked" in lowered or "revoked" in lowered


def build_google_service(api_name: str, version: str, credentials):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is missing. Install the Google API dependencies first."
        ) from exc
    return build(api_name, version, credentials=credentials, cache_discovery=False)
