"""Daily viral video discovery — monitors competitor channels for newly viral uploads.

Picks the single best new video from established channels in the niche, deduped
against past discoveries, and emails one approval request.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from supabase import create_client

from execution.email_sender import send_approval_email

logger = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
YT_API_BASE = "https://www.googleapis.com/youtube/v3"


def _get_active_api_key(sb: Any) -> str:
    res = sb.table("yt_api_accounts").select("api_key").eq(
        "quota_exhausted", False
    ).limit(1).execute()
    if not res.data:
        raise RuntimeError("No unexhausted YouTube API keys")
    return res.data[0]["api_key"]


def _get_thresholds(sb: Any) -> dict:
    res = sb.table("yt_workflow_settings").select("setting_value").eq(
        "setting_key", "viral_threshold"
    ).limit(1).execute()
    if not res.data:
        return {"minViews": 7000, "earlyHours": 12, "earlyViews": 4000, "maxAgeHours": 48}
    val = res.data[0]["setting_value"]
    return val if isinstance(val, dict) else json.loads(val)


def _parse_iso_duration(d: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    h, mn, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mn * 60 + s


def _hours_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def _is_viral(video: dict, t: dict) -> bool:
    views = int(video.get("statistics", {}).get("viewCount", 0))
    age = _hours_since(video["snippet"]["publishedAt"])
    if age > t["maxAgeHours"]:
        return False
    if views >= t["minViews"]:
        return True
    if views >= t["earlyViews"] and age <= t["earlyHours"]:
        return True
    return False


def _is_short(video: dict) -> bool:
    dur = _parse_iso_duration(video.get("contentDetails", {}).get("duration", ""))
    if dur <= 60:
        return True
    text = (video["snippet"]["title"] + video["snippet"].get("description", "")).lower()
    return "#shorts" in text and dur <= 180


def _resolve_channel_id(api_key: str, username: str) -> str | None:
    """Look up a channel ID from a @handle or channel name. Returns None on miss."""
    r = requests.get(
        f"{YT_API_BASE}/search",
        params={"part": "snippet", "q": username, "type": "channel",
                "maxResults": 1, "key": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    items = r.json().get("items", [])
    if not items:
        return None
    return items[0]["id"].get("channelId")


def _get_uploads_playlist(api_key: str, channel_id: str) -> str | None:
    r = requests.get(
        f"{YT_API_BASE}/channels",
        params={"part": "contentDetails", "id": channel_id, "key": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    items = r.json().get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _get_recent_uploads(api_key: str, playlist_id: str) -> list[str]:
    r = requests.get(
        f"{YT_API_BASE}/playlistItems",
        params={"part": "contentDetails", "playlistId": playlist_id,
                "maxResults": 25, "key": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        return []
    return [item["contentDetails"]["videoId"] for item in r.json().get("items", [])]


def _fetch_video_stats(api_key: str, video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    r = requests.get(
        f"{YT_API_BASE}/videos",
        params={"part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids[:50]), "key": api_key},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def _format_video_row(video: dict, channel_username: str) -> dict:
    sn = video["snippet"]
    st = video["statistics"]
    cd = video["contentDetails"]
    return {
        "video_id": video["id"],
        "url": f"https://www.youtube.com/watch?v={video['id']}",
        "title": sn.get("title", ""),
        "channel_title": sn.get("channelTitle", ""),
        "channel_id": sn.get("channelId", ""),
        "channel_username": channel_username,
        "published_at": sn.get("publishedAt"),
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
        "comments": int(st.get("commentCount", 0)),
        "duration": cd.get("duration", ""),
        "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url"),
        "tags": sn.get("tags", []),
        "description": (sn.get("description") or "")[:1000],
        "age_hours": round(_hours_since(sn["publishedAt"]), 2),
        "source_type": "channel",
        "source_value": channel_username,
        "status": "queued",
        "suitable": None,
    }


def discover_and_email() -> int:
    """Run discovery, pick best candidate, email for approval. Returns 1 if email sent, 0 if nothing found."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    api_key = _get_active_api_key(sb)
    thresholds = _get_thresholds(sb)

    competitors = sb.table("yt_competitors").select(
        "channel_username,channel_id"
    ).eq("is_active", True).execute().data
    if not competitors:
        logger.warning("No active competitors configured")
        return 0

    existing = sb.table("yt_viral_videos").select("video_id").execute()
    seen_ids = {r["video_id"] for r in existing.data}

    candidates: list[dict] = []
    for comp in competitors:
        username = comp["channel_username"]
        channel_id = comp.get("channel_id")
        try:
            if not channel_id:
                channel_id = _resolve_channel_id(api_key, username)
                if not channel_id:
                    logger.warning("Could not resolve channel for %s", username)
                    continue
                sb.table("yt_competitors").update(
                    {"channel_id": channel_id}
                ).eq("channel_username", username).execute()

            playlist_id = _get_uploads_playlist(api_key, channel_id)
            if not playlist_id:
                logger.warning("No uploads playlist for %s", username)
                continue

            video_ids = _get_recent_uploads(api_key, playlist_id)
            video_ids = [v for v in video_ids if v not in seen_ids]
            if not video_ids:
                continue

            videos = _fetch_video_stats(api_key, video_ids)
            for v in videos:
                if _is_short(v):
                    continue
                if _parse_iso_duration(v["contentDetails"]["duration"]) > 1800:
                    continue
                if _is_viral(v, thresholds):
                    candidates.append(_format_video_row(v, username))
        except Exception as exc:
            logger.warning("Channel %s failed: %s", username, exc)
            continue

    if not candidates:
        logger.info("Discovery: no viral videos found this run")
        return 0

    best = max(candidates, key=lambda v: v["views"])
    logger.info(
        "Discovery: best candidate '%s' from %s with %d views (age %.1fh)",
        best["title"][:50], best["channel_username"], best["views"], best["age_hours"],
    )

    inserted = sb.table("yt_viral_videos").insert(best).execute()
    record = inserted.data[0]

    thread_id = send_approval_email(record)
    if thread_id:
        sb.table("yt_viral_videos").update({"thread_id": thread_id}).eq(
            "id", record["id"]
        ).execute()

    return 1
