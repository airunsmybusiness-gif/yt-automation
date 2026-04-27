"""
OpenRouter image generation using Gemini 2.5 Flash Image Preview.
Uses a reference image to maintain consistent stick figure style (Nour's approach).
"""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

OPENROUTER_API_KEY: str = os.environ["OPENROUTER_API_KEY"]
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

MODEL: str = "google/gemini-2.5-flash-image-preview"
API_URL: str = "https://openrouter.ai/api/v1/chat/completions"
REFERENCE_BUCKET: str = "reference-image"
REFERENCE_FILE: str = "stickfigure.jpeg.png"

MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5
RATE_LIMIT_SLEEP_SEC: float = 1.0


class OpenRouterImageError(RuntimeError):
    """Raised when OpenRouter image generation fails."""


def _get_reference_image_b64() -> str:
    """Download stickfigure reference image from Supabase storage and return as base64."""
    url = f"{SUPABASE_URL}/storage/v1/object/public/{REFERENCE_BUCKET}/{REFERENCE_FILE}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")


_REFERENCE_CACHE: str | None = None


def _reference_image_b64() -> str:
    global _REFERENCE_CACHE
    if _REFERENCE_CACHE is None:
        _REFERENCE_CACHE = _get_reference_image_b64()
        log.info(f"Loaded reference image, {len(_REFERENCE_CACHE)} chars b64")
    return _REFERENCE_CACHE


def _call_openrouter(prompt: str) -> bytes:
    ref_b64 = _reference_image_b64()
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://mindseam.app",
            "X-Title": "MindSeam Pipeline",
        },
        json={
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt[:2000]},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{ref_b64}"
                            },
                        },
                    ],
                }
            ],
            "modalities": ["image", "text"],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise OpenRouterImageError(f"No choices in response: {data}")
    msg = choices[0].get("message", {})
    images = msg.get("images", [])
    if not images:
        raise OpenRouterImageError(f"No images in message: {msg}")
    img_url = images[0].get("image_url", {}).get("url", "")
    if not img_url.startswith("data:"):
        raise OpenRouterImageError(f"Unexpected image_url format: {img_url[:100]}")
    img_b64 = img_url.split(",", 1)[1]
    return base64.b64decode(img_b64)


def _generate_with_retry(prompt: str, key: int) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_openrouter(prompt)
        except (requests.RequestException, OpenRouterImageError) as exc:
            last_err = exc
            log.warning(
                f"Image {key} attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}"
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise OpenRouterImageError(
        f"Image {key} failed after {MAX_RETRIES} retries: {last_err}"
    )


def generate_batch(
    jobs: list[dict], output_dir: Path
) -> dict[str, int | list[int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    success_count = 0
    failure_count = 0
    skipped_count = 0
    failed_keys: list[int] = []
    total = len(jobs)

    for idx, job in enumerate(jobs, start=1):
        key = int(job["sentence_number"])
        prompt = job["formatted_prompt"]
        out_path = output_dir / f"{key:04d}.jpg"

        if out_path.exists() and out_path.stat().st_size > 1000:
            skipped_count += 1
            continue

        try:
            img_bytes = _generate_with_retry(prompt, key)
            out_path.write_bytes(img_bytes)
            success_count += 1
            if idx % 10 == 0 or idx == total:
                log.info(f"OpenRouter Gemini: {idx}/{total} (success={success_count})")
        except OpenRouterImageError as exc:
            failure_count += 1
            failed_keys.append(key)
            log.error(f"Image {key} permanently failed: {exc}")

        time.sleep(RATE_LIMIT_SLEEP_SEC)

    return {
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failure_count": failure_count,
        "failed_keys": failed_keys,
        "total": total,
    }
