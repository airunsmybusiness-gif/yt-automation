"""Fetch YouTube transcript: captions first, Whisper fallback, never description."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_transcript(yt_video_id: str, fallback_description: str = "") -> tuple[str, str, str]:
    """Return (content, language_code, provider).

    Tries youtube-transcript-api → Whisper. Provider is always 'supadata' (enum constraint).
    Description is ignored — it's promo copy, not video content.
    Raises RuntimeError only if both transcript paths fail.
    """
    # Path 1: public captions (cheap, instant)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
        try:
            segs = YouTubeTranscriptApi.get_transcript(
                yt_video_id, languages=["en", "en-US", "en-GB"]
            )
            text = " ".join(s["text"].strip() for s in segs if s.get("text"))
            if text and len(text) > 200:
                logger.info("Transcript via captions: %d chars", len(text))
                return text, "en", "supadata"
        except (TranscriptsDisabled, NoTranscriptFound):
            logger.info("No captions for %s, falling back to Whisper", yt_video_id)
        except Exception as exc:
            logger.warning("Caption fetch errored for %s: %s — trying Whisper", yt_video_id, exc)
    except ImportError:
        logger.error("youtube-transcript-api not installed")

    # Path 2: Whisper transcription of the video audio
    try:
        from execution.whisper_transcribe import transcribe_via_whisper
        text = transcribe_via_whisper(yt_video_id)
        return text, "en", "supadata"
    except Exception as exc:
        raise RuntimeError(f"Both caption and Whisper failed for {yt_video_id}: {exc}") from exc
