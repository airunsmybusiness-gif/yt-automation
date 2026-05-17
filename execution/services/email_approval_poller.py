"""Poll Gmail for approval replies on queued videos."""

import logging
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


def poll_approval_emails(supabase: Any, settings: Settings) -> None:
    """Check Gmail for yes/no replies and update suitable flag."""
    try:
        from execution.services.gmail_service import poll_approval_replies
        count = poll_approval_replies(supabase, settings)
        if count:
            logger.info("Processed %d approval replies", count)
    except Exception as e:
        logger.error("Approval polling failed: %s", e, exc_info=True)
