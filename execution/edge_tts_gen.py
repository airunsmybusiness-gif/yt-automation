"""
ElevenLabs TTS — natural human voice for MindSeam videos.
Model: eleven_turbo_v2_5 (~$0.05/1k chars)
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_KEY: str = os.environ["ELEVENLABS_API_KEY"]
VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
MODEL: str = "eleven_turbo_v2_5"
MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5


class EdgeTTSError(RuntimeError):
    pass


def _call_elevenlabs(text: str) -> bytes:
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
        headers={
            "xi-api-key": API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": MODEL,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.2,
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    resp.raise_for_status()
    if len(resp.content) < 500:
        raise EdgeTTSError(f"ElevenLabs returned too little audio: {len(resp.content)} bytes")
    return resp.content


def _generate_with_retry(text: str, output_path: Path) -> None:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            audio = _call_elevenlabs(text)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio)
            return
        except Exception as exc:
            last_err = exc
            log.warning(f"TTS attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise EdgeTTSError(f"TTS failed after {MAX_RETRIES} retries: {last_err}")


def generate_audio(text: str, output_path: Path) -> None:
    _generate_with_retry(text, output_path)


def generate_chunk(sentences: list[dict], output_path: Path) -> dict:
    combined_text = " ".join(s["sentence_text"] for s in sentences)
    _generate_with_retry(combined_text, output_path)
    return {
        "text": combined_text,
        "character_count": len(combined_text),
        "sentence_count": len(sentences),
    }
