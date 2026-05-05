"""Transcribe a YouTube video's audio via Groq Whisper.

Downloads audio with yt-dlp, sends to Groq's whisper-large-v3-turbo, returns text.
Cleans up the temp file regardless of outcome.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def transcribe_via_whisper(yt_video_id: str) -> str:
    """Download audio for a YouTube video and transcribe it. Returns text or raises."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    try:
        import yt_dlp
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError(f"Whisper deps missing: {exc}")

    url = f"https://www.youtube.com/watch?v={yt_video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = str(Path(tmpdir) / "audio.%(ext)s")
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": out_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        logger.info("Whisper: downloading audio for %s", yt_video_id)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        mp3 = next(Path(tmpdir).glob("*.mp3"), None)
        if not mp3:
            raise RuntimeError(f"yt-dlp produced no mp3 for {yt_video_id}")

        size_mb = mp3.stat().st_size / 1_000_000
        logger.info("Whisper: audio %.1f MB, transcribing via Groq", size_mb)

        client = Groq(api_key=api_key)
        with open(mp3, "rb") as f:
            resp = client.audio.transcriptions.create(
                file=(mp3.name, f.read()),
                model="whisper-large-v3-turbo",
                response_format="text",
                language="en",
            )
        text = resp if isinstance(resp, str) else getattr(resp, "text", "")
        if not text or len(text) < 100:
            raise RuntimeError(f"Whisper returned suspiciously short text ({len(text)} chars)")
        logger.info("Whisper: transcribed %d chars for %s", len(text), yt_video_id)
        return text
