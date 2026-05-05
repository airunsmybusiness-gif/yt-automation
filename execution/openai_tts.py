"""
OpenAI TTS module — replaces Cloudflare TTS.
Cost: $0.015 per 1K characters with tts-1.
~$0.135 per 8-min video. ~37 videos per $5.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
TTS_URL: str = "https://api.openai.com/v1/audio/speech"
MODEL: str = "tts-1-hd"
VOICE: str = "nova"
RESPONSE_FORMAT: str = "mp3"
MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5
INTER_CHUNK_SLEEP_SEC: float = 1.0
MAX_CHARS_PER_REQUEST: int = 4000


class TTSError(RuntimeError):
    pass


def _call_tts(text: str) -> bytes:
    resp = requests.post(
        TTS_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "voice": VOICE,
            "input": text[:MAX_CHARS_PER_REQUEST],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise TTSError(f"OpenAI TTS HTTP {resp.status_code}: {resp.text[:300]}")
    if not resp.content or len(resp.content) < 500:
        raise TTSError(f"OpenAI TTS returned invalid audio: {len(resp.content)} bytes")
    return resp.content


def _generate_with_retry(text: str) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_tts(text)
        except (requests.RequestException, TTSError) as exc:
            last_err = exc
            log.warning(f"TTS attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise TTSError(f"TTS failed after {MAX_RETRIES} retries: {last_err}")


def generate_audio(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_bytes = _generate_with_retry(text)
    output_path.write_bytes(audio_bytes)
    if not output_path.exists() or output_path.stat().st_size < 500:
        raise TTSError(f"TTS produced empty file: {output_path}")


def generate_chunk(sentences: list[dict], output_path: Path) -> dict:
    time.sleep(INTER_CHUNK_SLEEP_SEC)
    combined_text = " ".join(s["sentence_text"] for s in sentences)
    generate_audio(combined_text, output_path)
    return {
        "text": combined_text,
        "character_count": len(combined_text),
        "sentence_count": len(sentences),
    }
