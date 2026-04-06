"""YouTube Data API v3 — viral video discovery matching Nour's n8n workflow.

Flow (matches n8n exactly):
1. Fetch competitors → resolve missing channel IDs
2. Get uploads playlist per channel → fetch recent playlist items
3. Get video stats in batches → filter by viral threshold + duration
4. Exclude Shorts (<60s) and long videos (>30min)
5. If no viral from channels → fallback to keyword search
6. Deduplicate against existing DB records
7. Insert new viral videos
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config.settings import Settings
from execution.services.supabase_client import (
    get_available_api_key,
    get_competitors,
    get_search_keywords,
    get_viral_video_by_video_id,
    get_workflow_settings,
    insert_viral_video,
    mark_key_exhausted,
)
from execution.utils.exceptions import QuotaExhaustedError, VideoNotFoundError

logger = logging.getLogger(__name__)

YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YT_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YT_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

def _get_api_key(client: Any) -> tuple[str, str]:
    """Get an available API key. Returns (api_key, account_id)."""
    account = get_available_api_key(client)
    if not account:
        raise QuotaExhaustedError("All YouTube API keys exhausted")
    return account["api_key"], account["id"]


def _handle_quota_error(client: Any, account_id: str, context: str) -> None:
    """Mark key exhausted on 403 quota error."""
    mark_key_exhausted(client, account_id)
    logger.warning("ANNEALING: API key %s exhausted during %s", account_id, context)


def _is_quota_error(e: requests.HTTPError) -> bool:
    """Check if an HTTPError is a quota exceeded error."""
    return e.response is not None and e.response.status_code == 403


# ---------------------------------------------------------------------------
# Channel ID resolution (matches n8n "Resolve Channel by Username")
# ---------------------------------------------------------------------------

def resolve_missing_channel_ids(client: Any, api_key: str) -> None:
    """Resolve channel IDs for competitors that only have usernames.

    Uses YouTube Search API to find the channel by username,
    then saves the channel_id back to yt_competitors.
    """
    resp = (
        client.table("yt_competitors")
        .select("*")
        .is_("channel_id", "null")
        .execute()
    )
    missing = resp.data or []
    if not missing:
        return

    logger.info("Resolving %d missing channel IDs", len(missing))

    for comp in missing:
        username = comp["channel_username"]
        try:
            params = {
                "part": "snippet",
                "q": username,
                "type": "channel",
                "maxResults": 1,
                "key": api_key,
            }
            r = requests.get(YT_SEARCH_URL, params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                logger.warning("Could not resolve channel: %s", username)
                continue

            channel_id = items[0]["id"].get("channelId", "")
            channel_name = items[0]["snippet"].get("title", "")

            client.table("yt_competitors").update({
                "channel_id": channel_id,
                "channel_name": channel_name,
            }).eq("id", comp["id"]).execute()

            logger.info("Resolved %s → %s (%s)", username, channel_id, channel_name)

        except requests.HTTPError as e:
            logger.error("Failed to resolve channel %s: %s", username, e)
            if _is_quota_error(e):
                raise


# ---------------------------------------------------------------------------
# Uploads playlist approach (matches n8n exactly)
# ---------------------------------------------------------------------------

def get_uploads_playlist_id(api_key: str, channel_id: str) -> str | None:
    """Get the uploads playlist ID for a channel.

    Every YouTube channel has a hidden uploads playlist.
    The ID is the channel ID with 'UC' replaced by 'UU'.
    This API call confirms it exists and gets the exact ID.
    """
    params = {
        "part": "contentDetails",
        "id": channel_id,
        "key": api_key,
    }
    resp = requests.get(YT_CHANNELS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None
    return (
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )


def get_recent_uploads(
    api_key: str, playlist_id: str, max_results: int = 50
) -> list[dict[str, Any]]:
    """Fetch recent videos from a channel's uploads playlist."""
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": max_results,
        "key": api_key,
    }
    resp = requests.get(YT_PLAYLIST_ITEMS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_video_details(
    api_key: str, video_ids: list[str]
) -> list[dict[str, Any]]:
    """Fetch statistics, snippet, and contentDetails for video IDs."""
    if not video_ids:
        return []
    params = {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids[:50]),
        "key": api_key,
    }
    resp = requests.get(YT_VIDEOS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


# ---------------------------------------------------------------------------
# Duration parsing + filtering (matches n8n Filter Viral logic)
# ---------------------------------------------------------------------------

def _parse_duration_seconds(duration: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    if not duration:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    h, m, s = (int(match.group(i) or 0) for i in (1, 2, 3))
    return h * 3600 + m * 60 + s


def _is_short(video: dict[str, Any]) -> bool:
    """Check if a video is a YouTube Short (matches n8n logic)."""
    duration_s = _parse_duration_seconds(
        video.get("contentDetails", {}).get("duration", "")
    )
    if duration_s <= 60:
        return True
    title = (video.get("snippet", {}).get("title", "") or "").lower()
    desc = (video.get("snippet", {}).get("description", "") or "").lower()
    if "#shorts" in title or "#shorts" in desc:
        return duration_s <= 180
    return False


def is_viral(
    video: dict[str, Any],
    threshold: dict[str, Any],
) -> bool:
    """Check if a video meets the viral threshold.

    Also filters by duration: exclude Shorts (<60s) and videos >30min.
    Uses threshold dict from yt_workflow_settings.setting_value.
    """
    duration_s = _parse_duration_seconds(
        video.get("contentDetails", {}).get("duration", "")
    )

    # Exclude shorts and long videos (matches n8n exactly)
    if _is_short(video):
        return False
    if duration_s < 60 or duration_s > 1800:
        return False

    stats = video.get("statistics", {})
    views = int(stats.get("viewCount", 0))
    published_str = video.get("snippet", {}).get("publishedAt", "")

    if not published_str:
        return False

    try:
        published_at = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    except ValueError:
        return False

    age_hours = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
    max_age = threshold.get("maxAgeHours", 48)
    min_views = threshold.get("minViews", 7000)
    early_views = threshold.get("earlyViews", 4000)
    early_hours = threshold.get("earlyHours", 12)

    if age_hours > max_age:
        return False
    if views >= min_views:
        return True
    if age_hours <= early_hours and views >= early_views:
        return True

    return False


# ---------------------------------------------------------------------------
# Main discovery pipeline (matches n8n flow exactly)
# ---------------------------------------------------------------------------

def discover_viral_videos(
    supabase_client: Any,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Run the full viral video discovery cycle.

    Matches Nour's n8n workflow:
    1. Get API key
    2. Get competitors + resolve missing channel IDs
    3. Get uploads playlist → recent uploads → video stats → filter viral
    4. If no viral from channels → keyword search fallback
    5. Deduplicate against DB
    6. Insert new viral videos
    """
    api_key, account_id = _get_api_key(supabase_client)

    # Load thresholds from yt_workflow_settings
    ws = get_workflow_settings(supabase_client)
    threshold = ws.get("setting_value", {})
    if not threshold:
        threshold = {
            "minViews": settings.viral_min_views_48h,
            "earlyViews": settings.viral_min_views_12h,
            "earlyHours": 12,
            "maxAgeHours": settings.max_video_age_hours,
        }

    max_age_hours = threshold.get("maxAgeHours", 48)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    # Step 1: Resolve missing channel IDs
    try:
        resolve_missing_channel_ids(supabase_client, api_key)
    except QuotaExhaustedError:
        logger.error("Quota exhausted during channel resolution")
        return []
    except requests.HTTPError:
        pass  # Non-fatal, continue with channels that have IDs

    # Step 2: Channel pipeline — uploads playlist approach
    competitors = get_competitors(supabase_client)
    viral_videos: list[dict[str, Any]] = []

    for comp in competitors:
        channel_id = comp.get("channel_id")
        if not channel_id:
            continue

        try:
            # Get uploads playlist
            playlist_id = get_uploads_playlist_id(api_key, channel_id)
            if not playlist_id:
                continue

            # Get recent uploads
            uploads = get_recent_uploads(api_key, playlist_id)

            # Filter by age
            recent_ids = []
            for item in uploads:
                pub_str = (
                    item.get("snippet", {}).get("publishedAt")
                    or item.get("contentDetails", {}).get("videoPublishedAt")
                )
                if pub_str:
                    try:
                        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                        if pub >= cutoff:
                            vid_id = (
                                item.get("snippet", {}).get("resourceId", {}).get("videoId")
                                or item.get("contentDetails", {}).get("videoId")
                            )
                            if vid_id:
                                recent_ids.append(vid_id)
                    except ValueError:
                        pass

            if not recent_ids:
                continue

            # Get full video stats
            details = get_video_details(api_key, recent_ids)

            # Filter viral
            for video in details:
                if is_viral(video, threshold):
                    record = _build_record(video, "channel", comp.get("channel_username", ""))
                    viral_videos.append(record)

        except requests.HTTPError as e:
            if _is_quota_error(e):
                _handle_quota_error(supabase_client, account_id, "channel_pipeline")
                try:
                    api_key, account_id = _get_api_key(supabase_client)
                except QuotaExhaustedError:
                    logger.error("All API keys exhausted during channel pipeline")
                    break
            else:
                logger.error("Error processing channel %s: %s", channel_id, e)

    # Step 3: Keyword search fallback (if no viral from channels)
    if not viral_videos:
        logger.info("No viral from channels, falling back to keyword search")
        viral_videos = _keyword_search_fallback(
            supabase_client, api_key, account_id, threshold, max_age_hours
        )

    # Step 4: Deduplicate and insert
    inserted = _deduplicate_and_insert(supabase_client, viral_videos)
    logger.info("Discovery complete: %d new viral videos inserted", len(inserted))
    return inserted


def _keyword_search_fallback(
    supabase_client: Any,
    api_key: str,
    account_id: str,
    threshold: dict[str, Any],
    max_age_hours: int,
) -> list[dict[str, Any]]:
    """Search by keywords when no viral videos found from channels."""
    keywords = get_search_keywords(supabase_client)
    if not keywords:
        return []

    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()
    viral_videos: list[dict[str, Any]] = []

    for kw in keywords:
        keyword = kw.get("keyword")
        if not keyword:
            continue

        try:
            params = {
                "part": "snippet",
                "q": keyword,
                "type": "video",
                "order": "date",
                "maxResults": 50,
                "publishedAfter": published_after,
                "key": api_key,
            }
            resp = requests.get(YT_SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("items", [])

            video_ids = [
                item["id"]["videoId"]
                for item in items
                if item.get("id", {}).get("videoId")
            ]

            if not video_ids:
                continue

            # Get full details in batches
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i : i + 50]
                details = get_video_details(api_key, batch)
                for video in details:
                    if is_viral(video, threshold):
                        record = _build_record(video, "search", keyword)
                        viral_videos.append(record)

        except requests.HTTPError as e:
            if _is_quota_error(e):
                _handle_quota_error(supabase_client, account_id, "keyword_search")
                try:
                    api_key, account_id = _get_api_key(supabase_client)
                except QuotaExhaustedError:
                    break
            else:
                logger.error("Keyword search error for '%s': %s", keyword, e)

    return viral_videos


# ---------------------------------------------------------------------------
# Record building + dedup + insert
# ---------------------------------------------------------------------------

def _build_record(
    video: dict[str, Any], source_type: str, source_value: str
) -> dict[str, Any]:
    """Build a yt_viral_videos record from YouTube API video data."""
    snippet = video.get("snippet", {})
    stats = video.get("statistics", {})
    content = video.get("contentDetails", {})
    published_str = snippet.get("publishedAt", "")
    video_id = video["id"]

    age_hours = 0.0
    if published_str:
        try:
            pub = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            age_hours = round((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 2)
        except ValueError:
            pass

    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": snippet.get("title", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "channel_username": source_value if source_type == "channel" else None,
        "published_at": published_str or None,
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "duration": content.get("duration", ""),
        "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
        "tags": snippet.get("tags", []),
        "description": (snippet.get("description", "") or "")[:1000],
        "age_hours": age_hours,
        "source_type": source_type,
        "source_value": source_value,
        "status": "queued",
    }


def _deduplicate_and_insert(
    supabase_client: Any,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate against DB and insert new videos."""
    if not candidates:
        return []

    inserted: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in candidates:
        vid = record["video_id"]
        if vid in seen:
            continue
        seen.add(vid)

        existing = get_viral_video_by_video_id(supabase_client, vid)
        if existing:
            logger.debug("Skipping duplicate: %s", vid)
            continue

        try:
            result = insert_viral_video(supabase_client, record)
            inserted.append(result)
        except Exception as e:
            logger.error("Failed to insert video %s: %s", vid, e)

    return inserted


# ---------------------------------------------------------------------------
# Manual URL submission
# ---------------------------------------------------------------------------

def fetch_single_video(
    supabase_client: Any,
    settings: Settings,
    video_url: str,
) -> dict[str, Any] | None:
    """Fetch and insert a single video from a manual URL submission."""
    video_id = _extract_video_id(video_url)
    if not video_id:
        logger.warning("Could not extract video ID from: %s", video_url)
        return None

    if get_viral_video_by_video_id(supabase_client, video_id):
        logger.info("Video already exists: %s", video_id)
        return None

    api_key, account_id = _get_api_key(supabase_client)
    details = get_video_details(api_key, [video_id])
    if not details:
        raise VideoNotFoundError(f"Video not found: {video_id}")

    record = _build_record(details[0], "manual", video_url)
    return insert_viral_video(supabase_client, record)


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url

    return None
