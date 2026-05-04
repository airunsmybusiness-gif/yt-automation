"""One-shot: mints Gmail credentials + refresh token for the pipeline.

Outputs two JSON blobs to paste into Railway as GMAIL_CREDENTIALS_JSON
and GMAIL_TOKEN_JSON. Run locally.
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_ID = input("Paste YOUTUBE_CLIENT_ID (full string ending in .apps.googleusercontent.com): ").strip()
CLIENT_SECRET = input("Paste YOUTUBE_CLIENT_SECRET: ").strip()

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(
    client_config,
    scopes=[
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

print("\n" + "=" * 60)
print("GMAIL_CREDENTIALS_JSON value (paste into Railway as one line):")
print(json.dumps(client_config))
print("\n" + "=" * 60)
print("GMAIL_TOKEN_JSON value (paste into Railway as one line):")
print(json.dumps({
    "access_token": creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
}))
print("=" * 60)
