"""
Replicate Flux image generation — high quality, hard cap at MAX_IMAGES.
Model: flux-schnell ($0.003/image, 50 images = $0.15/video max)
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import replicate
import requests

log = logging.getLogger(__name__)

MAX_IMAGES: int = 50
MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5
WIDTH: int = 1280
HEIGHT: int = 720
MODEL: str = "black-forest-labs/flux-schnell"


class ImageGenerationError(RuntimeError):
    pass


def _call_replicate(prompt: str) -> bytes:
    api_token = os.environ["REPLICATE_API_KEY"]
    client = replicate.Client(api_token=api_token)
    output = client.run(
        MODEL,
        input={
            "prompt": prompt,
            "width": WIDTH,
            "height": HEIGHT,
            "num_outputs": 1,
            "output_format": "jpg",
            "output_quality": 85,
            "go_fast": True,
        },
    )
    url = output[0] if isinstance(output, list) else str(output)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    if len(resp.content) < 1000:
        raise ImageGenerationError(f"Image too small: {len(resp.content)} bytes")
    return resp.content


def _generate_with_retry(prompt: str, key: int) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_replicate(prompt)
        except Exception as exc:
            last_err = exc
            log.warning(f"Image {key} attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise ImageGenerationError(f"Image {key} failed after {MAX_RETRIES} retries: {last_err}")


def generate_image(prompt: str, output_path: Path) -> None:
    img_bytes = _generate_with_retry(prompt, 0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(img_bytes)


def generate_batch(jobs: list[dict], output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    success_count = 0
    failure_count = 0
    skipped_count = 0
    failed_keys: list[int] = []

    # Hard cap: spread evenly across full video
    capped_jobs = _spread_jobs(jobs, MAX_IMAGES)
    total = len(capped_jobs)
    log.info(f"Replicate: generating {total}/{len(jobs)} images (hard cap {MAX_IMAGES})")

    for idx, job in enumerate(capped_jobs, start=1):
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
                log.info(f"Replicate: {idx}/{total} (success={success_count})")
        except ImageGenerationError as exc:
            failure_count += 1
            failed_keys.append(key)
            log.error(f"Image {key} permanently failed: {exc}")

    return {
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failure_count": failure_count,
        "failed_keys": failed_keys,
        "total": total,
    }


def _spread_jobs(jobs: list[dict], cap: int) -> list[dict]:
    if len(jobs) <= cap:
        return jobs
    step = len(jobs) / cap
    return [jobs[int(i * step)] for i in range(cap)]
