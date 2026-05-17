"""Replicate Flux Dev image generation — $0.03/image, hard budget cap."""

import logging
import time
from pathlib import Path
from typing import Optional

import replicate

logger = logging.getLogger(__name__)

COST_PER_IMAGE = 0.003
MIN_VALID_BYTES = 5_000  # 5KB minimum for a real image
RATE_LIMIT_DELAY = 2.0   # seconds between requests


def generate_single_image(
    prompt: str,
    output_path: Path,
    style_prefix: str = (
        "Digital illustration, clean composition, cinematic lighting, "
        "16:9 aspect ratio, no text, no watermarks, no hands, "
        "psychology and self-improvement theme"
    ),
) -> Optional[Path]:
    """Generate one image via Replicate Flux Dev.

    Args:
        prompt: Scene description (already transformed by Claude).
        output_path: Where to save the JPG.
        style_prefix: Prepended to every prompt for consistency.

    Returns:
        Path if successful, None if failed.
    """
    full_prompt = f"{style_prefix}. {prompt}"

    try:
        output = replicate.run(
            "stability-ai/stable-diffusion:ac732df83cea7fff18b8472768c88ad041fa750ff7682a21affe81863cbe77e4",
            input={
                "prompt": full_prompt,
                "width": 1280,
                "height": 720,
                "num_outputs": 1,
                "negative_prompt": "blurry, ugly, text, watermark, low quality",
                "num_inference_steps": 25,
            },
        )

        # output is a list of FileOutput objects
        if not output:
            logger.error("Replicate returned empty output for: %s", prompt[:60])
            return None

        image_data = output[0].read()

        if len(image_data) < MIN_VALID_BYTES:
            logger.warning("Image too small (%d bytes): %s", len(image_data), prompt[:60])
            return None

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_data)

        logger.info("Generated image: %s (%d bytes)", output_path.name, len(image_data))
        return output_path

    except replicate.exceptions.ReplicateError as e:
        logger.error("Replicate API error: %s", e)
        return None
    except Exception as e:
        logger.error("Image generation failed: %s", e, exc_info=True)
        return None


def generate_batch(
    image_jobs: list[dict],
    output_dir: Path,
    max_cost: float = 2.00,
    max_images: int = 50,
    style_prefix: str = (
        "Digital illustration, clean composition, cinematic lighting, "
        "16:9 aspect ratio, no text, no watermarks, no hands, "
        "psychology and self-improvement theme"
    ),
) -> dict:
    """Generate images for all jobs within budget.

    Args:
        image_jobs: List of {pair_number, prompt}.
        output_dir: Directory for output JPGs.
        max_cost: Hard dollar cap.
        max_images: Hard image count cap.

    Returns:
        Dict with 'generated' (list of paths), 'failed' (count), 'total_cost'.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[dict] = []
    failed = 0
    total_cost = 0.0

    jobs_to_run = image_jobs[:max_images]

    for job in jobs_to_run:
        # Budget check before each image
        if total_cost + COST_PER_IMAGE > max_cost:
            logger.warning(
                "Budget cap reached: $%.2f spent, %d images generated",
                total_cost, len(generated),
            )
            break

        pair_num = job["pair_number"]
        prompt = job["prompt"]
        out_path = output_dir / f"img_{pair_num:04d}.jpg"

        result = generate_single_image(prompt, out_path, style_prefix)

        if result:
            generated.append({"pair_number": pair_num, "path": result})
            total_cost += COST_PER_IMAGE
        else:
            failed += 1

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

    logger.info(
        "Image batch complete: %d generated, %d failed, $%.2f spent",
        len(generated), failed, total_cost,
    )

    return {
        "generated": generated,
        "failed": failed,
        "total_cost": total_cost,
    }
