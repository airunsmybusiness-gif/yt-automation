"""Typed Supabase client wrapper for the YouTube automation pipeline."""

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config.settings import Settings

logger = logging.getLogger(__name__)


def create_supabase_client(settings: Settings) -> Client:
    """Create and verify Supabase client connection.

    Args:
        settings: Application settings with Supabase credentials.

    Returns:
        Connected Supabase client.

    Raises:
        SystemExit: If connection fails.
    """
    try:
        client = create_client(settings.supabase_url, settings.supabase_key)
        # Verify connection with a lightweight query
        client.table("yt_workflow_settings").select("id").limit(1).execute()
        logger.info("Supabase connection verified")
        return client
    except Exception as e:
        logger.critical("Failed to connect to Supabase: %s", e)
        raise SystemExit(f"Supabase connection failed: {e}") from e


# ---------------------------------------------------------------------------
# yt_viral_videos
# ---------------------------------------------------------------------------

def get_viral_video_by_video_id(client: Client, video_id: str) -> dict[str, Any] | None:
    """Check if a video already exists in yt_viral_videos."""
    resp = client.table("yt_viral_videos").select("*").eq("video_id", video_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def insert_viral_video(client: Client, video: dict[str, Any]) -> dict[str, Any]:
    """Insert a new viral video record."""
    resp = client.table("yt_viral_videos").insert(video).execute()
    logger.info("Inserted viral video: %s", video.get("video_id"))
    return resp.data[0]


def update_viral_video(
    client: Client, record_id: int, updates: dict[str, Any]
) -> dict[str, Any]:
    """Update a viral video record by primary key."""
    resp = client.table("yt_viral_videos").update(updates).eq("id", record_id).execute()
    logger.info("Updated viral video id=%d: %s", record_id, list(updates.keys()))
    return resp.data[0]


def get_queued_videos(client: Client) -> list[dict[str, Any]]:
    """Get all videos with status='queued' and thread_id set (email sent, awaiting reply)."""
    resp = (
        client.table("yt_viral_videos")
        .select("*")
        .eq("status", "queued")
        .is_("suitable", "null")
        .not_.is_("thread_id", "null")
        .execute()
    )
    return resp.data


def get_approved_videos(client: Client) -> list[dict[str, Any]]:
    """Get videos approved (suitable=true) and ready for pipeline."""
    resp = (
        client.table("yt_viral_videos")
        .select("*")
        .eq("suitable", True)
        .eq("status", "queued")
        .execute()
    )
    return resp.data


# ---------------------------------------------------------------------------
# yt_competitors + yt_search_keywords
# ---------------------------------------------------------------------------

def get_competitors(client: Client) -> list[dict[str, Any]]:
    """Load all competitor channels."""
    resp = client.table("yt_competitors").select("*").execute()
    return resp.data


def get_search_keywords(client: Client) -> list[dict[str, Any]]:
    """Load all search keywords."""
    resp = client.table("yt_search_keywords").select("*").execute()
    return resp.data


# ---------------------------------------------------------------------------
# yt_api_accounts (quota tracking)
# ---------------------------------------------------------------------------

def get_available_api_key(client: Client) -> dict[str, Any] | None:
    """Get the first non-exhausted API key."""
    resp = (
        client.table("yt_api_accounts")
        .select("*")
        .eq("quota_exhausted", False)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def mark_key_exhausted(client: Client, account_id: int) -> None:
    """Mark an API key as quota-exhausted."""
    client.table("yt_api_accounts").update(
        {"quota_exhausted": True}
    ).eq("id", account_id).execute()
    logger.warning("ANNEALING: Marked API key id=%d as exhausted", account_id)


def reset_all_quotas(client: Client) -> int:
    """Reset quota_exhausted=false for all API accounts. Returns count."""
    resp = (
        client.table("yt_api_accounts")
        .update({"quota_exhausted": False})
        .neq("id", 0)  # update all
        .execute()
    )
    count = len(resp.data) if resp.data else 0
    logger.info("Reset quotas for %d API accounts", count)
    return count


# ---------------------------------------------------------------------------
# yt_workflow_settings
# ---------------------------------------------------------------------------

def get_workflow_settings(client: Client) -> dict[str, Any]:
    """Load workflow settings (viral thresholds, etc.)."""
    resp = client.table("yt_workflow_settings").select("*").limit(1).execute()
    if not resp.data:
        logger.warning("No workflow settings found, using defaults")
        return {}
    return resp.data[0]


# ---------------------------------------------------------------------------
# yt_agent_prompts
# ---------------------------------------------------------------------------

def get_agent_prompt(client: Client, prompt_name: str) -> str | None:
    """Load a specific agent prompt by name."""
    resp = (
        client.table("yt_agent_prompts")
        .select("prompt_text")
        .eq("prompt_name", prompt_name)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0].get("prompt_text")
    logger.warning("Agent prompt '%s' not found", prompt_name)
    return None
