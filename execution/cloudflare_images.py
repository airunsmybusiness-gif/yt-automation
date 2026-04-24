"""
Cloudflare Workers AI image generation.
Replaces Google Imagen. Free, fast, no GCS.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CLOUDFLARE_ACCOUNT_ID: str = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CLOUDFLARE_API_TOKEN: str = os.environ["CLOUDFLARE_API_TOKEN"]
MODEL: str = "@cf/black-forest-labs/flux-1-schnell"
API_URL: str = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{MODEL}"
)
MAX_RETRIES: int = 3
RETRY_DELAY_SEC: int = 5
RATE_LIMIT_SLEEP_SEC: float = 0.3


class CloudflareImageError(RuntimeError):
    """Raised when Cloudflare image generation fails."""


def _call_cloudflare(prompt: str) -> bytes:
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"prompt": prompt[:2000]},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    img_b64 = data.get("result", {}).get("image")
    if not img_b64:
        raise CloudflareImageError(f"No image in response: {data}")
    return base64.b64decode(img_b64)


def _generate_with_retry(prompt: str, key: int) -> bytes:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_cloudflare(prompt)
        except (requests.RequestException, CloudflareImageError) as exc:
            last_err = exc
            log.warning(
                f"Image {key} attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}"
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC * (attempt + 1))
    raise CloudflareImageError(f"Image {key} failed after {MAX_RETRIES} retries: {last_err}")


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
                log.info(f"Cloudflare Flux: {idx}/{total} (success={success_count})")
        except CloudflareImageError as exc:
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
