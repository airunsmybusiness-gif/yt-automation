"""Transcript fetcher — Supadata API with Gemini fallback."""

import logging
import os
from typing import Any

import requests

from execution.utils.exceptions import TranscriptUnavailableError

logger = logging.getLogger(__name__)

SUPADATA_BASE_URL = "https://api.supadata.ai/v1"


def fetch_transcript(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> str:
    """Fetch transcript and save to yt_video_transcripts.

    Tries Supadata first, falls back to Gemini if unavailable.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID.

    Returns:
        Transcript text content.

    Raises:
        TranscriptUnavailableError: If all sources fail.
    """
    # Update status to processing
    supabase_client.table("yt_viral_videos").update(
        {"transcript_status": "processing"}
    ).eq("id", video_record_id).execute()

    # Try Supadata first
    transcript = _try_supadata(supabase_client, video_id)

    if transcript:
        provider = "supadata"
    else:
        # Fallback to Gemini
        logger.info("ANNEALING: Supadata failed for %s, falling back to Gemini", video_id)
        transcript = _try_gemini(video_id)
        provider = "gemini"

    if not transcript:
        supabase_client.table("yt_viral_videos").update(
            {"transcript_status": "failed"}
        ).eq("id", video_record_id).execute()
        raise TranscriptUnavailableError(
            f"Could not fetch transcript for {video_id} from any source"
        )

    # Save to yt_video_transcripts
    _save_transcript(supabase_client, video_record_id, video_id, transcript, provider)
    logger.info(
        "Transcript saved for %s via %s (%d chars)",
        video_id, provider, len(transcript),
    )
    return transcript


def _try_supadata(supabase_client: Any, video_id: str) -> str | None:
    """Attempt transcript fetch via Supadata API."""
    api_key = _get_supadata_key(supabase_client)
    if not api_key:
        logger.warning("No Supadata API keys available")
        return None

    try:
        resp = requests.get(
            f"{SUPADATA_BASE_URL}/youtube/transcript",
            params={"videoId": video_id, "text": "true"},
            headers={"x-api-key": api_key},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Supadata returns {content: "..."} or array of segments
            if isinstance(data, dict) and data.get("content"):
                return data["content"]
            if isinstance(data, list):
                return " ".join(
                    seg.get("text", "") for seg in data if seg.get("text")
                )
            return str(data) if data else None

        logger.warning("Supadata returned %d for %s", resp.status_code, video_id)
        return None

    except requests.RequestException as e:
        logger.error("Supadata request failed: %s", e)
        return None


def _try_gemini(video_id: str) -> str | None:
    """Attempt transcript extraction via Gemini (YouTube URL as input)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, cannot use Gemini fallback")
        return None

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        payload = {
            "contents": [{
                "parts": [
                    {"text": (
                        "You are a transcript extraction tool. Extract the complete "
                        "spoken transcript from this YouTube video. Return ONLY the "
                        "raw transcript text, no timestamps, no formatting, no "
                        "commentary. If you cannot access the video, return exactly "
                        "the text: TRANSCRIPT_UNAVAILABLE"
                    )},
                    {"file_data": {"file_uri": video_url, "mime_type": "video/*"}},
                ]
            }],
        }

        resp = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=120,
        )

        if resp.status_code != 200:
            logger.warning("Gemini transcript returned %d", resp.status_code)
            return None

        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )

        if "TRANSCRIPT_UNAVAILABLE" in text:
            return None

        return text.strip() if text.strip() else None

    except requests.RequestException as e:
        logger.error("Gemini transcript request failed: %s", e)
        return None


def _get_supadata_key(supabase_client: Any) -> str | None:
    """Get an available Supadata API key."""
    resp = (
        supabase_client.table("yt_supadata_api_keys")
        .select("api_key")
        .eq("quota_exhausted", False)
        .limit(1)
        .execute()
    )
    return resp.data[0]["api_key"] if resp.data else None


def _save_transcript(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
    content: str,
    provider: str,
) -> None:
    """Save transcript to yt_video_transcripts table."""
    supabase_client.table("yt_video_transcripts").upsert(
        {
            "video_record_id": video_record_id,
            "video_id": video_id,
            "content": content,
            "language_code": "en",
            "type": "source",
            "provider": provider,
        },
        on_conflict="video_record_id,type",
    ).execute()
