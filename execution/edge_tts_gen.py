"""
Cloudflare Workers AI TTS (Deepgram Aura-1).
Replaces Edge TTS which was blocked on Railway cloud IPs.
Free tier: 10,000 neurons/day, aura-1 uses ~0.1 neurons per second of audio.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CLOUDFLARE_ACCOUNT_ID: str = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CLOUDFLARE_API_TOKEN: str = os.environ["CLOUDFLARE_API_TOKEN"]
TTS_MODEL: str = "@cf/deepgram/aura-1"
TTS_URL: str = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{TTS_MODEL}"
)
SPEAKER: str = "luna"
MAX_RETRIES: int = 5
RETRY_DELAY_SEC: int = 15
MAX_CHARS_PER_REQUEST: int = 1800


class EdgeTTSError(RuntimeError):
    """Raised when TTS generation fails (name kept for backward compat)."""


def _call_tts(text: str) -> bytes:
    resp = requests.post(
        TTS_URL,
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "text": text[:MAX_CHARS_PER_REQUEST],
            "speaker": SPEAKER,
            "encoding": "mp3",
        },
        timeout=120,
    )
    resp.raise_for_status()
    if not resp.content or len(resp.content) < 500:
        raise EdgeTTSError(f"Cloudflare TTS returned invalid audio: {len(resp.content)} bytes")
    return resp.content


def _generate_with_retry(text: str) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_tts(text)
        except (requests.RequestException, EdgeTTSError) as exc:
            last_err = exc
            log.warning(f"TTS attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise EdgeTTSError(f"TTS failed after {MAX_RETRIES} retries: {last_err}")


def generate_audio(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_bytes = _generate_with_retry(text)
    output_path.write_bytes(audio_bytes)
    if not output_path.exists() or output_path.stat().st_size < 500:
        raise EdgeTTSError(f"TTS produced empty file: {output_path}")


def generate_chunk(sentences: list[dict], output_path: Path) -> dict:
    combined_text = " ".join(s["sentence_text"] for s in sentences)
    generate_audio(combined_text, output_path)
    return {
        "text": combined_text,
        "character_count": len(combined_text),
        "sentence_count": len(sentences),
    }
