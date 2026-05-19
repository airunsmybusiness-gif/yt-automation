"""
Free image generation via Pollinations.ai — no API key, no billing.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path

import requests

log = logging.getLogger(__name__)

MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5
RATE_LIMIT_SLEEP_SEC: float = 0.5
WIDTH: int = 1280
HEIGHT: int = 720


class ImageGenerationError(RuntimeError):
    pass


def _call_pollinations(prompt: str) -> bytes:
    encoded = urllib.parse.quote(prompt[:500])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={WIDTH}&height={HEIGHT}&nologo=true&model=flux"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    if len(resp.content) < 1000:
        raise ImageGenerationError(f"Response too small: {len(resp.content)} bytes")
    return resp.content


def _generate_with_retry(prompt: str, key: int) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_pollinations(prompt)
        except (requests.RequestException, ImageGenerationError) as exc:
            last_err = exc
            log.warning(f"Image {key} attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise ImageGenerationError(f"Image {key} failed after {MAX_RETRIES} retries: {last_err}")


def generate_image(prompt: str, output_path: Path) -> None:
    img_bytes = _generate_with_retry(prompt, 0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(img_bytes)


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
                log.info(f"Pollinations: {idx}/{total} (success={success_count})")
        except ImageGenerationError as exc:
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
