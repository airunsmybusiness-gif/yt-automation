"""Imagen 4 Fast image generator via Gemini API."""
import os
import base64
import logging
import time
from pathlib import Path
import requests

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = "imagen-4.0-fast-generate-001"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:predict"
STYLE_PREFIX = (
    "hand-drawn folk-art ink illustration, single navy blue stick figure, "
    "rough sketchy lines, cream paper background, minimalist, no text, "
    "no color except navy ink, child-like simplicity, "
)


def generate_image(prompt: str, output_path: Path, max_retries: int = 3) -> bool:
    """Generate one image via Imagen 4 Fast. Returns True on success."""
    full_prompt = STYLE_PREFIX + prompt
    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
    }
    url = f"{ENDPOINT}?key={GEMINI_API_KEY}"

    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, timeout=60)
            if r.status_code == 429:
                wait = 2 ** attempt * 6
                logger.warning(f"Rate limit hit, sleep {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            b64 = data["predictions"][0]["bytesBase64Encoded"]
            output_path.write_bytes(base64.b64decode(b64))
            return True
        except Exception as e:
            logger.error(f"Imagen attempt {attempt+1} failed: {e}")
            if attempt == max_retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False


def generate_batch(prompts: list[str], output_dir: Path) -> dict:
    """Generate batch. Returns {success, skipped, failed} counts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    success = failed = 0
    for i, prompt in enumerate(prompts):
        path = output_dir / f"img_{i:03d}.png"
        if path.exists():
            success += 1
            continue
        if generate_image(prompt, path):
            success += 1
            logger.info(f"Imagen 4 Fast: {i+1}/{len(prompts)} (success={success})")
        else:
            failed += 1
        time.sleep(0.3)  # 10 IPM = 6s/image min, but we go fast and retry on 429
    return {"success": success, "skipped": 0, "failed": failed}
