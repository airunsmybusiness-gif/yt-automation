"""
Edge TTS (Microsoft) audio generation.
Replaces Google Cloud TTS. Free, no API key, no signup.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import edge_tts

log = logging.getLogger(__name__)

VOICE: str = "en-US-AvaMultilingualNeural"
RATE: str = "+0%"
VOLUME: str = "+0%"


class EdgeTTSError(RuntimeError):
    """Raised when Edge TTS generation fails."""


async def _generate_async(text: str, output_path: Path) -> None:
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, volume=VOLUME)
    await communicate.save(str(output_path))


def generate_audio(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_generate_async(text, output_path))
    except Exception as exc:
        raise EdgeTTSError(f"Edge TTS failed: {exc}") from exc
    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise EdgeTTSError(f"Edge TTS produced empty file: {output_path}")


def generate_chunk(sentences: list[dict], output_path: Path) -> dict:
    combined_text = " ".join(s["sentence_text"] for s in sentences)
    generate_audio(combined_text, output_path)
    return {
        "text": combined_text,
        "character_count": len(combined_text),
        "sentence_count": len(sentences),
    }
