"""Gmail API service — approval emails and reply polling."""

import base64
import json
import logging
import tempfile
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config.settings import Settings
from execution.services.supabase_client import get_queued_videos, update_viral_video
from execution.utils.exceptions import GmailError

logger = logging.getLogger(__name__)


def _build_gmail_service(settings: Settings) -> Any:
    """Build authenticated Gmail API service from base64-encoded credentials.

    The credentials and token are stored as base64-encoded JSON in env vars
    to avoid filesystem dependencies on Railway.
    """
    try:
        token_data = json.loads(base64.b64decode(settings.gmail_token_json))
        creds = Credentials.from_authorized_user_info(token_data)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logger.info("Gmail token refreshed")

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        logger.critical("Failed to build Gmail service: %s", e)
        raise GmailError(f"Gmail auth failed: {e}") from e


# ---------------------------------------------------------------------------
# Send approval email
# ---------------------------------------------------------------------------

def send_approval_email(
    settings: Settings,
    video: dict[str, Any],
) -> str:
    """Send an approval email for a viral video candidate.

    Args:
        settings: App settings with Gmail credentials.
        video: Viral video record from Supabase.

    Returns:
        Gmail thread_id for reply matching.
    """
    service = _build_gmail_service(settings)

    video_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    subject = f"[YT Pipeline] Approve: {video.get('title', 'Unknown')[:80]}"
    body = (
        f"New viral video detected:\n\n"
        f"Title: {video.get('title', 'N/A')}\n"
        f"Channel: {video.get('channel_title', 'N/A')}\n"
        f"Views: {video.get('views', 0):,}\n"
        f"Likes: {video.get('likes', 0):,}\n"
        f"Comments: {video.get('comments', 0):,}\n"
        f"Published: {video.get('published_at', 'N/A')}\n"
        f"Link: {video_url}\n\n"
        f"Reply YES to approve or NO to reject."
    )

    message = MIMEText(body)
    message["to"] = settings.gmail_approval_to
    message["from"] = settings.gmail_sender_email
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        thread_id = sent.get("threadId", "")
        logger.info(
            "Approval email sent for video %s, thread_id=%s",
            video["video_id"], thread_id,
        )
        return thread_id
    except Exception as e:
        logger.error("Failed to send approval email: %s", e)
        raise GmailError(f"Send failed: {e}") from e


# ---------------------------------------------------------------------------
# Poll for replies
# ---------------------------------------------------------------------------

def poll_approval_replies(
    supabase_client: Any,
    settings: Settings,
) -> int:
    """Poll Gmail for replies to approval threads.

    Matches replies by thread_id stored on yt_viral_videos records.
    Updates suitable=true/false based on "yes"/"no" reply content.

    Returns:
        Number of videos updated.
    """
    service = _build_gmail_service(settings)
    pending_videos = get_queued_videos(supabase_client)

    if not pending_videos:
        return 0

    # Build thread_id → video mapping
    thread_map: dict[str, dict[str, Any]] = {}
    for v in pending_videos:
        tid = v.get("thread_id")
        if tid:
            thread_map[tid] = v

    if not thread_map:
        return 0

    updated = 0
    for thread_id, video in thread_map.items():
        try:
            reply_text = _get_latest_reply(service, thread_id)
            if reply_text is None:
                continue

            decision = _parse_decision(reply_text)
            if decision is None:
                logger.warning(
                    "Unrecognized reply for thread %s: %s",
                    thread_id, reply_text[:100],
                )
                continue

            update_viral_video(
                supabase_client,
                video["id"],
                {"suitable": decision},
            )
            action = "approved" if decision else "rejected"
            logger.info("Video %s %s via email", video["video_id"], action)
            updated += 1

        except Exception as e:
            logger.error("Error polling thread %s: %s", thread_id, e)

    return updated


def _get_latest_reply(service: Any, thread_id: str) -> str | None:
    """Get the body text of the latest reply in a thread.

    Returns None if there's only the original message (no reply yet).
    """
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
    except Exception as e:
        logger.debug("Could not fetch thread %s: %s", thread_id, e)
        return None

    messages = thread.get("messages", [])
    if len(messages) < 2:
        return None  # No reply yet

    latest = messages[-1]
    return _extract_body(latest)


def _extract_body(message: dict[str, Any]) -> str:
    """Extract plain text body from a Gmail message."""
    payload = message.get("payload", {})

    # Direct body
    body_data = payload.get("body", {}).get("data")
    if body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

    # Multipart — find text/plain
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    return ""


def _parse_decision(reply_text: str) -> bool | None:
    """Parse a yes/no decision from email reply text.

    Returns True for yes, False for no, None for unrecognized.
    """
    cleaned = reply_text.strip().lower()
    # Take just the first line (ignore quoted text)
    first_line = cleaned.split("\n")[0].strip()

    if first_line in ("yes", "y", "approve", "approved"):
        return True
    if first_line in ("no", "n", "reject", "rejected", "skip"):
        return False

    # Check if the first word is yes/no
    first_word = first_line.split()[0] if first_line.split() else ""
    if first_word in ("yes", "y"):
        return True
    if first_word in ("no", "n"):
        return False

    return None


# ---------------------------------------------------------------------------
# Error notification
# ---------------------------------------------------------------------------

def send_error_alert(
    settings: Settings,
    subject: str,
    error_details: str,
) -> None:
    """Send an error alert email to the pipeline owner."""
    service = _build_gmail_service(settings)

    message = MIMEText(f"Pipeline Error:\n\n{error_details}")
    message["to"] = settings.gmail_approval_to
    message["from"] = settings.gmail_sender_email
    message["subject"] = f"[YT Pipeline ERROR] {subject}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info("Error alert sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send error alert: %s", e)
