"""Gmail OAuth2 setup helper.

Run locally once to generate base64-encoded credentials and token
for use as environment variables on Railway.

Usage:
    1. Download OAuth2 client credentials JSON from Google Cloud Console
       (APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON)
    2. Save as credentials.json in this directory
    3. Run: python scripts/gmail_auth.py
    4. Browser opens → authorize → token.json created
    5. Script outputs base64 strings for GMAIL_CREDENTIALS_JSON and GMAIL_TOKEN_JSON
    6. Set those as env vars on Railway
"""

import base64
import json
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def main() -> None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install deps: pip install google-auth-oauthlib google-auth")
        sys.exit(1)

    creds_path = Path("credentials.json")
    token_path = Path("token.json")

    if not creds_path.exists():
        print("ERROR: credentials.json not found in current directory.")
        print("Download from Google Cloud Console → APIs & Services → Credentials")
        sys.exit(1)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to {token_path}")

    # Encode to base64 for env vars
    creds_b64 = base64.b64encode(creds_path.read_bytes()).decode("utf-8")
    token_b64 = base64.b64encode(token_path.read_bytes()).decode("utf-8")

    print("\n" + "=" * 60)
    print("Set these as environment variables on Railway:")
    print("=" * 60)
    print(f"\nGMAIL_CREDENTIALS_JSON={creds_b64}")
    print(f"\nGMAIL_TOKEN_JSON={token_b64}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
