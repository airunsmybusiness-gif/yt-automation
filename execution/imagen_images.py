"""Replicate Flux Dev image generator."""

import logging
import os
import time
from pathlib import Path
from typing import Any

import replicate

logger = logging.getLogger(__name__)

REPLICATE_API_TOKEN = os.environ["REPLICATE_API_TOKEN"]
STYLE_PREFIX = (
    "hand-drawn folk-art ink illustration, single navy blue stick figure, "
    "rough sketchy lines, cream paper background, minimalist, no text, "
    "no color except navy ink, child-like simplicity, "
)
MIN_VALID_BYTES = 1024  # PNG smaller than 1KB is broken
THROTTLE_SECONDS = 8


def _extract_bytes(output: Any) -> bytes:
    """Replicate SDK returns a list of FileOutput; .read() yields the PNG bytes."""
    if isinstance(output, list) and output:
        item = output[0]
    else:
        item = output

    if hasattr(item, "read"):
        return item.read()
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)

    # Fallback: SDK gave us a URL string
    if isinstance(item, str) and item.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(item, timeout=60) as r:
            return r.read()

    raise RuntimeError(f"Cannot extract bytes from Replicate output: {type(item)!r}")


def generate_image(prompt: Any, output_path: Path, max_retries: int = 3) -> bool:
    if isinstance(prompt, dict):
        prompt = prompt.get("formatted_prompt", prompt.get("prompt", str(prompt)))
    full_prompt = STYLE_PREFIX + str(prompt)

    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

    for attempt in range(max_retries):
        try:
            output = replicate.run(
                "black-forest-labs/flux-dev",
                input={
                    "prompt": full_prompt,
                    "num_outputs": 1,
                    "aspect_ratio": "1:1",
                    "output_format": "png",
                    "guidance": 3.5,
                    "num_inference_steps": 28,
                },
            )
            data = _extract_bytes(output)
            if len(data) < MIN_VALID_BYTES:
                raise RuntimeError(f"Image bytes too small: {len(data)}")
            output_path.write_bytes(data)
            return True
        except Exception as e:
            logger.error("Replicate attempt %d failed: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False


def generate_batch(prompts: list, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    success = failed = skipped = 0
    total = len(prompts)
    for i, prompt in enumerate(prompts):
        path = output_dir / f"img_{i:03d}.png"
        if path.exists() and path.stat().st_size >= MIN_VALID_BYTES:
            skipped += 1
            success += 1
            continue
        if generate_image(prompt, path):
            success += 1
            logger.info("Replicate Flux Dev: %d/%d (success=%d)", i + 1, total, success)
        else:
            failed += 1
            logger.error("Replicate Flux Dev: %d/%d FAILED (failed=%d)", i + 1, total, failed)
        time.sleep(THROTTLE_SECONDS)
    return {"success": success, "skipped": skipped, "failed": failed}
