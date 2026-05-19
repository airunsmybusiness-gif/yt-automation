"""
YouTube viral video discovery — searches trending psychology/mindset content
and queues the best candidates in yt_viral_videos.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from supabase import Client, create_client

log = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
YOUTUBE_API_KEY: str = os.environ.get("YOUTUBE_API_KEY", "")

SEARCH_QUERIES = [
    "psychology explained",
    "how your mind works",
    "why people do this",
    "human behavior explained",
    "mindset shift",
    "dark psychology",
    "emotional intelligence",
    "stoicism explained",
    "cognitive bias explained",
    "why you procrastinate",
    "social psychology",
    "how manipulation works",
    "narcissist psychology",
    "anxiety explained simply",
]

MIN_VIEWS = 100_000
MAX_RESULTS_PER_QUERY = 5


def _youtube_client():
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY not set — cannot run discovery")
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _search_videos(yt, query: str) -> list[dict]:
    try:
        resp = yt.search().list(
            q=query,
            part="id,snippet",
            type="video",
            videoDuration="medium",  # 4–20 minutes
            order="viewCount",
            maxResults=MAX_RESULTS_PER_QUERY,
            relevanceLanguage="en",
            safeSearch="moderate",
        ).execute()
        return resp.get("items", [])
    except HttpError as e:
        log.warning(f"Search failed for '{query}': {e}")
        return []


def _get_stats(yt, video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    resp = yt.videos().list(
        part="statistics,contentDetails",
        id=",".join(video_ids),
    ).execute()
    return {item["id"]: item for item in resp.get("items", [])}


def _existing_video_ids(sb: Client) -> set[str]:
    rows = sb.table("yt_viral_videos").select("video_id").execute()
    return {r["video_id"] for r in (rows.data or [])}


def discover_and_queue(sb: Client | None = None) -> int:
    if sb is None:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        yt = _youtube_client()
    except RuntimeError as e:
        log.warning(f"Discovery skipped: {e}")
        return 0

    existing = _existing_video_ids(sb)
    candidates: dict[str, dict] = {}

    for query in SEARCH_QUERIES:
        items = _search_videos(yt, query)
        for item in items:
            vid_id = item["id"].get("videoId")
            if not vid_id or vid_id in existing or vid_id in candidates:
                continue
            candidates[vid_id] = {
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "query": query,
            }

    if not candidates:
        log.info("Discovery: no new candidates found")
        return 0

    stats = _get_stats(yt, list(candidates.keys()))
    queued = 0

    for vid_id, meta in candidates.items():
        stat = stats.get(vid_id, {}).get("statistics", {})
        views = int(stat.get("viewCount", 0))
        if views < MIN_VIEWS:
            continue

        try:
            sb.table("yt_viral_videos").insert({
                "id": str(uuid.uuid4()),
                "video_id": vid_id,
                "title": meta["title"],
                "channel_title": meta["channel"],
                "view_count": views,
                "status": "queued",
                "suitable": True,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "production_notes": f"auto-discovered via: {meta['query']}",
            }).execute()
            log.info(f"Discovery: queued '{meta['title'][:60]}' ({views:,} views)")
            queued += 1
        except Exception as e:
            log.warning(f"Discovery: failed to insert {vid_id}: {e}")

    log.info(f"Discovery: queued {queued} new videos")
    return queued
