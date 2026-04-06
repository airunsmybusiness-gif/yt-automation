"""Thumbnail describer — Gemini Vision for thumbnail analysis."""

import base64
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def describe_thumbnail(
    supabase_client: Any,
    video_record_id: str,
    thumbnail_url: str,
) -> str:
    """Download thumbnail and generate a text description via Gemini Vision.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        thumbnail_url: URL of the YouTube thumbnail.

    Returns:
        Text description of the thumbnail.
    """
    if not thumbnail_url:
        description = "No thumbnail available"
        _save_description(supabase_client, video_record_id, description)
        return description

    # Download thumbnail
    image_b64 = _download_image_b64(thumbnail_url)
    if not image_b64:
        description = "Thumbnail download failed"
        _save_description(supabase_client, video_record_id, description)
        return description

    # Generate description via Gemini Vision
    description = _gemini_describe(image_b64)
    if not description:
        description = "Thumbnail description generation failed"

    _save_description(supabase_client, video_record_id, description)
    logger.info(
        "Thumbnail described for record %s (%d chars)",
        video_record_id, len(description),
    )
    return description


def _download_image_b64(url: str) -> str | None:
    """Download image and return as base64 string."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("utf-8")
    except requests.RequestException as e:
        logger.error("Failed to download thumbnail: %s", e)
        return None


def _gemini_describe(image_b64: str) -> str | None:
    """Use Gemini Vision to describe a thumbnail image."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set for thumbnail description")
        return None

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.0-flash:generateContent"
    )

    payload = {
        "contents": [{
            "parts": [
                {
                    "text": (
                        "Describe this YouTube video thumbnail in detail. "
                        "Include: colors, text overlays, facial expressions, "
                        "composition, style, mood, and any notable visual elements. "
                        "Be specific about fonts, positioning, and design choices. "
                        "Keep under 300 words."
                    )
                },
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": image_b64,
                    }
                },
            ]
        }],
    }

    try:
        resp = requests.post(
            url, params={"key": api_key}, json=payload, timeout=30
        )
        if resp.status_code != 200:
            logger.warning("Gemini Vision returned %d", resp.status_code)
            return None

        data = resp.json()
        return (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
    except requests.RequestException as e:
        logger.error("Gemini Vision request failed: %s", e)
        return None


def _save_description(
    supabase_client: Any,
    video_record_id: str,
    description: str,
) -> None:
    """Save thumbnail description to yt_viral_videos."""
    supabase_client.table("yt_viral_videos").update(
        {"thumbnail_description": description}
    ).eq("id", video_record_id).execute()
