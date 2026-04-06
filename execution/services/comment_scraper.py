"""Comment scraper — YouTube commentThreads API → yt_comments table."""

import logging
from typing import Any

import requests

from execution.services.supabase_client import get_available_api_key, mark_key_exhausted

logger = logging.getLogger(__name__)

YT_COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"


def scrape_comments(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
    max_pages: int = 5,
) -> int:
    """Scrape all comments for a video and save to yt_comments.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID.
        max_pages: Max pages to fetch (50 comments per page).

    Returns:
        Total comments inserted.
    """
    account = get_available_api_key(supabase_client)
    if not account:
        logger.warning("No API keys available for comment scraping")
        return 0

    api_key = account["api_key"]
    account_id = account["id"]
    total_inserted = 0
    next_page_token = None

    for page in range(max_pages):
        try:
            comments, next_page_token = _fetch_comment_page(
                api_key, video_id, next_page_token
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                mark_key_exhausted(supabase_client, account_id)
                logger.warning("ANNEALING: API key exhausted during comment scraping")
                break
            if e.response is not None and e.response.status_code == 404:
                logger.info("Comments disabled for video %s", video_id)
                break
            logger.error("Comment fetch error: %s", e)
            break

        if not comments:
            break

        rows = _format_comment_rows(comments, video_record_id, video_id)
        _insert_comments(supabase_client, rows)
        total_inserted += len(rows)

        if not next_page_token:
            break

    logger.info(
        "Scraped %d comments for video %s", total_inserted, video_id
    )

    # Update comments_status on the viral video
    supabase_client.table("yt_viral_videos").update(
        {"comments_status": "completed"}
    ).eq("id", video_record_id).execute()

    return total_inserted


def _fetch_comment_page(
    api_key: str, video_id: str, page_token: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one page of comment threads."""
    params: dict[str, Any] = {
        "part": "snippet,replies",
        "videoId": video_id,
        "maxResults": 100,
        "order": "relevance",
        "key": api_key,
    }
    if page_token:
        params["pageToken"] = page_token

    resp = requests.get(YT_COMMENT_THREADS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", []), data.get("nextPageToken")


def _format_comment_rows(
    threads: list[dict[str, Any]],
    video_record_id: str,
    video_id: str,
) -> list[dict[str, Any]]:
    """Flatten comment threads into rows for yt_comments table."""
    rows: list[dict[str, Any]] = []

    for thread in threads:
        top = thread.get("snippet", {}).get("topLevelComment", {})
        top_snippet = top.get("snippet", {})

        rows.append({
            "video_record_id": video_record_id,
            "video_id": video_id,
            "comment_id": top.get("id", ""),
            "parent_id": None,
            "author_name": top_snippet.get("authorDisplayName", ""),
            "author_channel_id": top_snippet.get("authorChannelId", {}).get("value", ""),
            "content": top_snippet.get("textOriginal", ""),
            "like_count": top_snippet.get("likeCount", 0),
            "is_reply": False,
            "published_at": top_snippet.get("publishedAt"),
            "updated_at": top_snippet.get("updatedAt"),
        })

        # Add replies
        for reply in thread.get("replies", {}).get("comments", []):
            r_snippet = reply.get("snippet", {})
            rows.append({
                "video_record_id": video_record_id,
                "video_id": video_id,
                "comment_id": reply.get("id", ""),
                "parent_id": top.get("id", ""),
                "author_name": r_snippet.get("authorDisplayName", ""),
                "author_channel_id": r_snippet.get("authorChannelId", {}).get("value", ""),
                "content": r_snippet.get("textOriginal", ""),
                "like_count": r_snippet.get("likeCount", 0),
                "is_reply": True,
                "published_at": r_snippet.get("publishedAt"),
                "updated_at": r_snippet.get("updatedAt"),
            })

    return rows


def _insert_comments(
    supabase_client: Any,
    rows: list[dict[str, Any]],
) -> None:
    """Insert comment rows, skipping duplicates via on_conflict."""
    if not rows:
        return
    try:
        supabase_client.table("yt_comments").upsert(
            rows, on_conflict="comment_id"
        ).execute()
    except Exception as e:
        logger.error("Failed to insert comments batch: %s", e)
