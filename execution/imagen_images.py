"""Replicate Flux Dev image generator."""
import os
import logging
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPLICATE_API_TOKEN = os.environ["REPLICATE_API_TOKEN"]
STYLE_PREFIX = (
    "hand-drawn folk-art ink illustration, single navy blue stick figure, "
    "rough sketchy lines, cream paper background, minimalist, no text, "
    "no color except navy ink, child-like simplicity, "
)


def generate_image(prompt, output_path: Path, max_retries: int = 3) -> bool:
    if isinstance(prompt, dict):
        prompt = prompt.get("formatted_prompt", prompt.get("prompt", str(prompt)))
    full_prompt = STYLE_PREFIX + str(prompt)

    import replicate
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
            url = output[0].url if hasattr(output[0], "url") else str(output[0])
            urllib.request.urlretrieve(url, str(output_path))
            return True
        except Exception as e:
            logger.error(f"Replicate attempt {attempt+1} failed: {e}")
            if attempt == max_retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False


def generate_batch(prompts: list, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    success = failed = 0
    for i, prompt in enumerate(prompts):
        path = output_dir / f"img_{i:03d}.png"
        if path.exists():
            success += 1
            continue
        if generate_image(prompt, path):
            success += 1
            logger.info(f"Replicate Flux Dev: {i+1}/{len(prompts)} (success={success})")
        else:
            failed += 1
        time.sleep(0.5)
    return {"success": success, "skipped": 0, "failed": failed}
