"""Viral video discovery — competitor channels + keyword search."""

import logging
from datetime import datetime, timezone
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)


def discover_viral_videos(supabase: Any, settings: Settings) -> None:
    """Discover viral videos and queue them for approval."""
    try:
        _discover_from_channels(supabase, settings)
    except Exception as e:
        logger.error("Viral discovery failed: %s", e, exc_info=True)


def _discover_from_channels(supabase: Any, settings: Settings) -> None:
    from execution.services.youtube_api import fetch_recent_channel_videos, is_viral
    
    # Get API key
    resp = supabase.table("yt_api_accounts").select("*").eq("quota_exhausted", False).limit(1).execute()
    if not resp.data:
        logger.warning("No available YouTube API keys")
        return
    api_key = resp.data[0]["api_key"]

    # Get thresholds
    thresh_resp = supabase.table("yt_workflow_settings").select("*").eq("setting_key", "viral_threshold").limit(1).execute()
    thresholds = thresh_resp.data[0]["setting_value"] if thresh_resp.data else {}

    # Get competitors
    competitors = supabase.table("yt_competitors").select("*").eq("is_active", True).execute().data or []

    found = 0
    for channel in competitors:
        channel_id = channel.get("channel_id")
        if not channel_id:
            continue
        try:
            videos = fetch_recent_channel_videos(api_key, channel_id, thresholds)
            for video in videos:
                _upsert_if_new(supabase, video)
                found += 1
        except Exception as e:
            logger.error("Channel %s failed: %s", channel_id, e)

    logger.info("Discovery complete: %d new videos found", found)


def _upsert_if_new(supabase: Any, video: dict) -> None:
    existing = supabase.table("yt_viral_videos").select("id").eq("video_id", video["video_id"]).limit(1).execute()
    if existing.data:
        return
    video["status"] = "queued"
    video["created_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("yt_viral_videos").insert(video).execute()
    logger.info("Queued new video: %s — %s", video["video_id"], video.get("title", ""))
