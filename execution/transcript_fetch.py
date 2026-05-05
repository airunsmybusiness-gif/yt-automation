"""Fetch YouTube transcript for a video.

Tries youtube-transcript-api first (free, no key, public captions).
Falls back to video description as a last resort so the pipeline never blocks.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fetch_transcript(yt_video_id: str, fallback_description: str = "") -> tuple[str, str, str]:
    """Return (content, language_code, provider).

    Raises RuntimeError only if both captions and fallback description are empty.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

        try:
            segs = YouTubeTranscriptApi.get_transcript(yt_video_id, languages=["en", "en-US", "en-GB"])
            text = " ".join(s["text"].strip() for s in segs if s.get("text"))
            if text:
                logger.info("Transcript fetched via youtube-transcript-api: %d chars", len(text))
                return text, "en", "supadata"
        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            logger.warning("No public captions for %s: %s", yt_video_id, exc)
        except Exception as exc:
            logger.warning("youtube-transcript-api failed for %s: %s", yt_video_id, exc)
    except ImportError:
        logger.error("youtube-transcript-api not installed")

    if fallback_description and len(fallback_description) > 200:
        logger.warning("Falling back to description as transcript for %s", yt_video_id)
        return fallback_description, "en", "supadata"

    raise RuntimeError(f"No transcript or usable description for {yt_video_id}")
