"""Edge TTS service — free Microsoft neural voices, one MP3 per sentence."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import edge_tts

logger = logging.getLogger(__name__)

MIN_AUDIO_BYTES = 1024  # 1KB minimum for a valid audio file


async def _generate_single(
    text: str,
    output_path: Path,
    voice: str,
) -> Optional[Path]:
    """Generate TTS for a single sentence."""
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(str(output_path))

        if output_path.stat().st_size < MIN_AUDIO_BYTES:
            logger.warning("Audio too small for: %s", text[:50])
            return None

        return output_path
    except Exception as e:
        logger.error("Edge TTS failed for '%s': %s", text[:50], e)
        return None


def generate_sentence_audio(
    sentences: list[dict],
    output_dir: Path,
    voice: str = "en-US-GuyNeural",
) -> list[dict]:
    """Generate one MP3 per sentence. Returns list of {sentence_number, path, text}.

    Args:
        sentences: List of dicts with 'sentence_number' and 'sentence_text'.
        output_dir: Directory to write MP3 files.
        voice: Edge TTS voice name.

    Returns:
        List of dicts with sentence_number, path, text for successful generations.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    async def _run_all() -> None:
        for sent in sentences:
            num = sent["sentence_number"]
            text = sent["sentence_text"]
            out_path = output_dir / f"sent_{num:04d}.mp3"

            result = await _generate_single(text, out_path, voice)
            if result:
                results.append({
                    "sentence_number": num,
                    "path": result,
                    "text": text,
                })
            else:
                logger.warning("Skipping sentence %d — TTS failed", num)

    asyncio.run(_run_all())

    logger.info(
        "Edge TTS complete: %d/%d sentences",
        len(results), len(sentences),
    )
    return results
