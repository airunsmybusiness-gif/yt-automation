"""Daily viral video discovery — picks the single best new video and emails for approval.

Translation of Nour's n8n Viral_Videos_Finder_Workflow.json, simplified to one
candidate per run.
"""

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
    return val if isinstance(val, dict) else __import__("json").loads(val)


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


def _search_by_keyword(api_key: str, keyword: str, t: dict) -> list[str]:
    pub_after = (datetime.now(timezone.utc) - timedelta(hours=t["maxAgeHours"])).isoformat()
    r = requests.get(
        f"{YT_API_BASE}/search",
        params={
            "part": "snippet", "q": keyword, "type": "video", "order": "date",
            "maxResults": 25, "publishedAfter": pub_after, "key": api_key,
        }, timeout=30,
    )
    r.raise_for_status()
    return [item["id"]["videoId"] for item in r.json().get("items", []) if "videoId" in item.get("id", {})]


def _fetch_video_stats(api_key: str, video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    r = requests.get(
        f"{YT_API_BASE}/videos",
        params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids[:50]), "key": api_key,
        }, timeout=30,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def _format_video_row(video: dict, source_type: str, source_value: str) -> dict:
    sn = video["snippet"]
    st = video["statistics"]
    cd = video["contentDetails"]
    return {
        "video_id": video["id"],
        "url": f"https://www.youtube.com/watch?v={video['id']}",
        "title": sn.get("title", ""),
        "channel_title": sn.get("channelTitle", ""),
        "channel_id": sn.get("channelId", ""),
        "published_at": sn.get("publishedAt"),
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
        "comments": int(st.get("commentCount", 0)),
        "duration": cd.get("duration", ""),
        "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url"),
        "tags": sn.get("tags", []),
        "description": (sn.get("description") or "")[:1000],
        "age_hours": round(_hours_since(sn["publishedAt"]), 2),
        "source_type": source_type,
        "source_value": source_value,
        "status": "queued",
        "suitable": None,
    }


def discover_and_email() -> int:
    """Run discovery, pick best candidate, email for approval. Returns 1 if email sent, 0 if nothing found."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    api_key = _get_active_api_key(sb)
    thresholds = _get_thresholds(sb)

    # Get all keywords; pick top by priority
    kw_rows = sb.table("yt_search_keywords").select("keyword,priority").eq(
        "is_active", True
    ).order("priority", desc=True).limit(8).execute()
    keywords = [r["keyword"] for r in kw_rows.data]

    # Existing videos to dedupe
    existing = sb.table("yt_viral_videos").select("video_id").execute()
    seen_ids = {r["video_id"] for r in existing.data}

    candidates: list[dict] = []
    for kw in keywords:
        try:
            video_ids = _search_by_keyword(api_key, kw, thresholds)
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
                    candidates.append(_format_video_row(v, "search", kw))
        except Exception as e:
            logger.warning("Keyword search failed for '%s': %s", kw, e)
            continue

    if not candidates:
        logger.info("Discovery: no viral videos found this run")
        return 0

    # Pick the highest view count candidate
    best = max(candidates, key=lambda v: v["views"])
    logger.info(
        "Discovery: best candidate '%s' with %d views (age %.1fh)",
        best["title"][:50], best["views"], best["age_hours"],
    )

    # Insert into Supabase
    inserted = sb.table("yt_viral_videos").insert(best).execute()
    record = inserted.data[0]

    # Send approval email
    thread_id = send_approval_email(record)
    if thread_id:
        sb.table("yt_viral_videos").update({"thread_id": thread_id}).eq(
            "id", record["id"]
        ).execute()

    return 1
