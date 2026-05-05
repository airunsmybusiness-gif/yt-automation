"""Fetch top comments for a YouTube video via YouTube Data API v3."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

YT_API_BASE = "https://www.googleapis.com/youtube/v3"


def fetch_top_comments(yt_video_id: str, api_key: str, max_results: int = 30) -> list[dict[str, Any]]:
    """Return list of comment dicts shaped for yt_comments table insert.

    Returns empty list if comments are disabled or API fails — callers must handle empty.
    """
    try:
        resp = requests.get(
            f"{YT_API_BASE}/commentThreads",
            params={
                "part": "snippet",
                "videoId": yt_video_id,
                "maxResults": min(max_results, 100),
                "order": "relevance",
                "key": api_key,
                "textFormat": "plainText",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Comments API returned %d for %s: %s", resp.status_code, yt_video_id, resp.text[:200])
            return []
        items = resp.json().get("items", [])
        out = []
        for it in items:
            top = it["snippet"]["topLevelComment"]["snippet"]
            out.append({
                "comment_id": it["snippet"]["topLevelComment"]["id"],
                "parent_id": None,
                "author_name": top.get("authorDisplayName", ""),
                "author_channel_id": top.get("authorChannelId", {}).get("value", ""),
                "content": top.get("textDisplay", ""),
                "like_count": int(top.get("likeCount", 0)),
                "is_reply": False,
                "is_own_video": False,
                "published_at": top.get("publishedAt"),
                "updated_at": top.get("updatedAt"),
            })
        logger.info("Fetched %d comments for %s", len(out), yt_video_id)
        return out
    except Exception as exc:
        logger.warning("Comments fetch failed for %s: %s", yt_video_id, exc)
        return []
