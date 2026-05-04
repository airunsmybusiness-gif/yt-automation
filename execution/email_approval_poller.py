"""Polls Gmail every 60s for replies to approval emails. Updates suitable flag."""

import base64
import json
import logging
import os
import re
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client

logger = logging.getLogger(__name__)

GMAIL_CREDENTIALS_JSON: str = os.environ["GMAIL_CREDENTIALS_JSON"]
GMAIL_TOKEN_JSON: str = os.environ["GMAIL_TOKEN_JSON"]
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

YES_PATTERN = re.compile(r"\b(yes|yep|yup|y|sure|do it|go|approve|approved)\b", re.IGNORECASE)
NO_PATTERN = re.compile(r"\b(no|nope|n|skip|reject|deny)\b", re.IGNORECASE)


def _build_service() -> Any:
    creds_data = json.loads(GMAIL_CREDENTIALS_JSON)
    token_data = json.loads(GMAIL_TOKEN_JSON)
    installed = creds_data.get("installed") or creds_data.get("web") or creds_data
    creds = Credentials(
        token=token_data.get("access_token") or token_data.get("token"),
        refresh_token=token_data["refresh_token"],
        token_uri=installed.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_body(payload: dict) -> str:
    parts = payload.get("parts") or [payload]
    for part in parts:
        body = part.get("body", {})
        data = body.get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            except Exception:
                continue
    return ""


def poll_approvals() -> int:
    """Check pending approvals, parse replies, update suitable flag.

    Returns number of approvals updated.
    """
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    pending = sb.table("yt_viral_videos").select("id,thread_id").is_(
        "suitable", "null"
    ).not_.is_("thread_id", "null").execute()

    if not pending.data:
        return 0

    service = _build_service()
    updated = 0

    for row in pending.data:
        thread_id = row["thread_id"]
        record_id = row["id"]
        try:
            thread = service.users().threads().get(
                userId="me", id=thread_id
            ).execute()
        except Exception as e:
            logger.warning("Could not fetch thread %s: %s", thread_id, e)
            continue

        messages = thread.get("messages", [])
        if len(messages) < 2:
            continue  # No reply yet

        # Most recent message is the reply
        reply_body = _decode_body(messages[-1].get("payload", {}))
        first_line = reply_body.strip().split("\n")[0][:200]

        if YES_PATTERN.search(first_line):
            sb.table("yt_viral_videos").update({"suitable": True}).eq(
                "id", record_id
            ).execute()
            logger.info("Approval YES: %s", record_id)
            updated += 1
        elif NO_PATTERN.search(first_line):
            sb.table("yt_viral_videos").update({"suitable": False}).eq(
                "id", record_id
            ).execute()
            logger.info("Approval NO: %s", record_id)
            updated += 1

    return updated
