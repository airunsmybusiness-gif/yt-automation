"""
Microsoft Edge TTS — free neural voice for MindSeam videos.
Voice: en-US-AndrewMultilingualNeural
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from pathlib import Path

import edge_tts

log = logging.getLogger(__name__)

VOICE: str = "en-US-AndrewMultilingualNeural"
MAX_RETRIES: int = 3


class EdgeTTSError(RuntimeError):
    pass


async def _synthesize(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(output_path))


def _run_async(text: str, output_path: Path) -> None:
    def _in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_synthesize(text, output_path))
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(_in_thread).result()


def _generate_with_retry(text: str, output_path: Path) -> None:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            _run_async(text, output_path)
            if output_path.stat().st_size < 500:
                raise EdgeTTSError(f"Audio too small: {output_path.stat().st_size} bytes")
            return
        except Exception as exc:
            last_err = exc
            log.warning(f"TTS attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
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
