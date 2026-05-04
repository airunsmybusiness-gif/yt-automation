"""Gmail API email sender — sends approval emails for newly discovered videos."""

import base64
import json
import logging
import os
from email.mime.text import MIMEText
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

GMAIL_CREDENTIALS_JSON: str = os.environ["GMAIL_CREDENTIALS_JSON"]
GMAIL_TOKEN_JSON: str = os.environ["GMAIL_TOKEN_JSON"]
GMAIL_SENDER_EMAIL: str = os.environ["GMAIL_SENDER_EMAIL"]
GMAIL_APPROVAL_TO: str = os.environ["GMAIL_APPROVAL_TO"]


def _build_service() -> Any:
    """Build authenticated Gmail API service from stored OAuth tokens."""
    creds_data = json.loads(GMAIL_CREDENTIALS_JSON)
    token_data = json.loads(GMAIL_TOKEN_JSON)

    installed = creds_data.get("installed") or creds_data.get("web") or creds_data
    creds = Credentials(
        token=token_data.get("access_token") or token_data.get("token"),
        refresh_token=token_data["refresh_token"],
        token_uri=installed.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_approval_email(video: dict[str, Any]) -> str:
    """Send a daily approval email for a newly discovered viral video.

    Returns the Gmail thread_id for tracking the response.
    """
    service = _build_service()

    title = video.get("title", "Unknown title")
    channel = video.get("channel_title", "Unknown channel")
    views = video.get("views", 0)
    url = video.get("url", "")
    age_hours = video.get("age_hours", 0)
    record_id = video.get("id", "")

    body = (
        f"New viral video discovered.\n\n"
        f"Title: {title}\n"
        f"Channel: {channel}\n"
        f"Views: {views:,}\n"
        f"Age: {age_hours:.1f} hours\n"
        f"URL: {url}\n\n"
        f"Reply YES to produce this video.\n"
        f"Reply NO or ignore to skip.\n\n"
        f"Internal ID: {record_id}\n"
    )

    message = MIMEText(body)
    message["to"] = GMAIL_APPROVAL_TO
    message["from"] = GMAIL_SENDER_EMAIL
    message["subject"] = f"[YT Auto] Approve: {title[:60]}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    thread_id = sent.get("threadId", "")
    logger.info("Approval email sent: thread_id=%s for %s", thread_id, record_id)
    return thread_id
