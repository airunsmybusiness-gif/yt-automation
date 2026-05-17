"""Poll Gmail for approval replies on queued videos."""

import logging
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


def poll_approval_emails(supabase: Any, settings: Settings) -> None:
    """Check Gmail for yes/no replies and update suitable flag."""
    try:
        from execution.services.gmail_service import get_approval_replies
        replies = get_approval_replies(settings)
        for reply in replies:
            thread_id = reply.get("thread_id")
            answer = reply.get("answer", "").lower().strip()
            if not thread_id:
                continue
            resp = supabase.table("yt_viral_videos").select("id").eq("thread_id", thread_id).limit(1).execute()
            if not resp.data:
                continue
            record_id = resp.data[0]["id"]
            if "yes" in answer:
                supabase.table("yt_viral_videos").update({"suitable": True}).eq("id", record_id).execute()
                logger.info("Approved: thread %s", thread_id)
            elif "no" in answer:
                supabase.table("yt_viral_videos").update({"suitable": False, "status": "rejected"}).eq("id", record_id).execute()
                logger.info("Rejected: thread %s", thread_id)
    except Exception as e:
        logger.error("Approval polling failed: %s", e, exc_info=True)
