"""
Free TTS via gTTS (Google Translate) — no API key, no billing.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from gtts import gTTS

log = logging.getLogger(__name__)

MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5


class EdgeTTSError(RuntimeError):
    pass


def _generate_with_retry(text: str, output_path: Path) -> None:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            tts = gTTS(text=text, lang="en", slow=False)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tts.save(str(output_path))
            if not output_path.exists() or output_path.stat().st_size < 500:
                raise EdgeTTSError("Output file too small")
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
