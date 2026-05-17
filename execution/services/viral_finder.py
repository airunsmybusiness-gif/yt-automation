"""Viral video discovery — thin wrapper around youtube_api.discover_viral_videos."""

import logging
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


def discover_viral_videos(supabase: Any, settings: Settings) -> None:
    """Discover viral videos from competitors and keywords."""
    try:
        from execution.services.youtube_api import discover_viral_videos as _discover
        _discover(supabase)
    except Exception as e:
        logger.error("Viral discovery failed: %s", e, exc_info=True)
