"""Video render service — FFmpeg via Cloud Function + post-processing."""

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


def render_video(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
    bg_music_name: str = "audio1.mp3",
    bg_volume: float = 0.15,
) -> str | None:
    """Call the generate_video Cloud Function to render the final MP4.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID (also GCS bucket name).
        bg_music_name: Background music filename in background-audio bucket.
        bg_volume: Background music volume (0.0 - 1.0).

    Returns:
        GCS URL of the final video, or None on failure.
    """
    function_url = os.environ.get("VIDEO_RENDER_FUNCTION_URL")
    if not function_url:
        logger.error("VIDEO_RENDER_FUNCTION_URL not set")
        return None

    payload = {
        "viral_video_id": video_record_id,
        "video_id": video_id,
        "bg_music_name": bg_music_name,
        "bg_volume": bg_volume,
    }

    try:
        logger.info("Calling generate_video for %s", video_id)
        resp = requests.post(function_url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("Video render Cloud Function failed: %s", e)
        return None

    if not data.get("success"):
        logger.error("Video render failed: %s", data.get("error", "unknown"))
        return None

    gcs_url = data.get("url", "")
    chunks = data.get("chunks_processed", 0)
    render_time = data.get("processing_time_seconds", 0)

    logger.info(
        "Video rendered: %d chunks, %.1fs, url=%s",
        chunks, render_time, gcs_url[:80],
    )

    # Save to yt_results
    supabase_client.table("yt_results").insert({
        "gcs_video_url": gcs_url,
        "video_id": video_id,
    }).execute()

    return gcs_url


def generate_thumbnail(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> str | None:
    """Generate a thumbnail via Gemini using the thumbnail_style prompt.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID.

    Returns:
        GCS URI of the thumbnail, or None on failure.
    """
    from execution.agents.agent_runner import _load_prompt

    # Get strategist data for the title
    strat_resp = (
        supabase_client.table("yt_strategist_results")
        .select("title_options, thumbnail_concept")
        .eq("video_record_id", video_record_id)
        .limit(1)
        .execute()
    )
    strat_data = strat_resp.data[0] if strat_resp.data else {}
    title_options = strat_data.get("title_options", [])
    thumbnail_concept = strat_data.get("thumbnail_concept", {})

    # Load thumbnail_style prompt
    style_resp = (
        supabase_client.table("yt_agent_prompts")
        .select("prompt_content")
        .eq("agent_name", "thumbnail_style")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    style_prompt = style_resp.data[0]["prompt_content"] if style_resp.data else ""

    # Generate thumbnail via Gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set for thumbnail generation")
        return None

    prompt_text = (
        f"{style_prompt}\n\n"
        f"Title options: {title_options}\n"
        f"Thumbnail concept: {thumbnail_concept}\n"
        f"Generate a YouTube thumbnail image for this video."
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.0-flash:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generation_config": {
            "response_modalities": ["IMAGE"],
            "image_config": {"aspect_ratio": "16:9"},
        },
    }

    try:
        resp = requests.post(url, params={"key": api_key}, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        # Extract image
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        image_b64 = None
        for part in parts:
            if "inlineData" in part:
                image_b64 = part["inlineData"].get("data")
                break
            if "inline_data" in part:
                image_b64 = part["inline_data"].get("data")
                break

        if not image_b64:
            logger.warning("No image data in Gemini thumbnail response")
            return None

        # Upload to GCS
        import base64
        from execution.services.gcs_client import upload_bytes

        image_bytes = base64.b64decode(image_b64)
        gcs_uri = upload_bytes(
            video_id, "thumbnail.jpg", image_bytes, "image/jpeg"
        )

        # Update yt_results
        supabase_client.table("yt_results").update(
            {"thumbnail_link": gcs_uri}
        ).eq("video_id", video_id).execute()

        logger.info("Thumbnail generated and uploaded: %s", gcs_uri)
        return gcs_uri

    except requests.RequestException as e:
        logger.error("Thumbnail generation failed: %s", e)
        return None


def upload_to_youtube(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> str | None:
    """Call the upload_video Cloud Function to upload to YouTube.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID (GCS bucket name).

    Returns:
        YouTube video URL, or None on failure.
    """
    function_url = os.environ.get("VIDEO_UPLOAD_FUNCTION_URL")
    if not function_url:
        logger.error("VIDEO_UPLOAD_FUNCTION_URL not set")
        return None

    # Get strategist data for title/description/tags
    strat_resp = (
        supabase_client.table("yt_strategist_results")
        .select("*")
        .eq("video_record_id", video_record_id)
        .limit(1)
        .execute()
    )
    strat = strat_resp.data[0] if strat_resp.data else {}

    title_options = strat.get("title_options", [])
    title = title_options[0] if title_options else f"Video {video_id}"
    if isinstance(title, dict):
        title = title.get("title", title.get("text", str(title)))

    video_metadata = strat.get("video_metadata", {})
    description = video_metadata.get("description", "") if isinstance(video_metadata, dict) else ""
    tags = video_metadata.get("tags", []) if isinstance(video_metadata, dict) else []

    payload = {
        "bucket_name": f"yt-{video_id.lower()}",
        "file_name": f"final_videos/{video_id}.mp4",
        "title": str(title)[:100],
        "description": str(description)[:5000],
        "category_id": "27",  # Education
        "privacy_status": "private",
        "tags": tags if isinstance(tags, list) else [],
        "thumbnail_file": "thumbnail.jpg",
    }

    try:
        logger.info("Uploading to YouTube: %s", title[:60])
        resp = requests.post(function_url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("YouTube upload Cloud Function failed: %s", e)
        return None

    if not data.get("success"):
        logger.error("YouTube upload failed: %s", data.get("error", "unknown"))
        return None

    yt_url = data.get("video_url", "")
    yt_video_id = data.get("video_id", "")

    logger.info("YouTube upload complete: %s", yt_url)

    # Update status to done
    supabase_client.table("yt_viral_videos").update({
        "status": "done",
        "production_completed_at": datetime.now(timezone.utc).isoformat(),
        "production_notes": f"YouTube: {yt_url}",
    }).eq("id", video_record_id).execute()

    return yt_url
