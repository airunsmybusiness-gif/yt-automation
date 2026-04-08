"""Image generation service — Vertex AI Imagen batch per sentence.

Flow:
1. Load sentences from yt_scripts
2. Generate image prompt per sentence via the image_generator agent
3. Get reference image (channel style) as base64
4. Build JSONL (key = sentence_number)
5. Call process_batch_images Cloud Function
6. Track in yt_batch_jobs and yt_image_generation_jobs
"""

import json
import logging
import os
import time
from typing import Any

import anthropic
import requests

from execution.agents.agent_runner import _get_claude_client, _load_prompt, _call_claude
from execution.services.gcs_client import ensure_bucket_exists

logger = logging.getLogger(__name__)


def run_image_pipeline(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> str | None:
    """Run the full image generation pipeline for a video.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID (also GCS bucket name).

    Returns:
        Batch job name if submitted successfully, None on failure.
    """
    # Load sentences
    resp = (
        supabase_client.table("yt_scripts")
        .select("*")
        .eq("viral_video_id", video_record_id)
        .order("sentence_number")
        .execute()
    )
    sentences = resp.data
    if not sentences:
        logger.warning("No sentences found for image gen: %s", video_record_id)
        return None

    logger.info("Image pipeline: %d sentences for %s", len(sentences), video_id)

    # Generate image prompts via Claude
    image_jobs = _generate_image_prompts(supabase_client, sentences)
    if not image_jobs:
        logger.error("No image prompts generated for %s", video_id)
        return None

    # Save image generation jobs to DB
    _save_image_jobs(supabase_client, video_record_id, image_jobs)

    # Get reference image
    reference_b64 = _get_reference_image(supabase_client, video_record_id)

    # Call Cloud Function
    batch_name = _submit_image_batch(
        video_id, image_jobs, reference_b64, video_record_id
    )

    if batch_name:
        # Track in yt_batch_jobs
        supabase_client.table("yt_batch_jobs").insert({
            "batch_job_name": batch_name,
            "status": "pending",
            "viral_video_id": video_record_id,
            "media_type": "image",
        }).execute()
        logger.info("Image batch submitted: %s", batch_name)

    return batch_name


def _generate_image_prompts(
    supabase_client: Any,
    sentences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate an image prompt for each sentence using the image_generator agent.

    Returns list of dicts with sentence_number and formatted_prompt.
    """
    client = _get_claude_client()
    prompt = _load_prompt(supabase_client, "image_generator")

    image_jobs = []
    # Process in batches to avoid excessive API calls
    batch_size = 5
    for i in range(0, len(sentences), batch_size):
        batch = sentences[i : i + batch_size]
        batch_text = json.dumps(
            [{"sentence_number": s["sentence_number"], "sentence_text": s["sentence_text"]}
             for s in batch],
            indent=2,
        )

        user_msg = (
            f"Generate image prompts for each of these sentences. "
            f"Return a JSON array where each item has: "
            f"sentence_number (int), formatted_prompt (string).\n\n"
            f"SENTENCES:\n{batch_text}"
        )

        try:
            result = _call_claude(client, prompt, user_msg)
            prompts = result if isinstance(result, list) else result.get("prompts", [])
            image_jobs.extend(prompts)
        except Exception as e:
            logger.error("Image prompt generation failed for batch %d: %s", i, e)
            # Generate fallback prompts
            for s in batch:
                image_jobs.append({
                    "sentence_number": s["sentence_number"],
                    "formatted_prompt": (
                        f"Create a hand-drawn folk art illustration depicting: "
                        f"{s['sentence_text'][:200]}. "
                        f"Style: warm melancholic, stick figures, bold text overlays."
                    ),
                })

    logger.info("Generated %d image prompts", len(image_jobs))
    return image_jobs


def _save_image_jobs(
    supabase_client: Any,
    video_record_id: str,
    image_jobs: list[dict[str, Any]],
) -> None:
    """Save image generation jobs to yt_image_generation_jobs."""
    rows = [
        {
            "sentence_number": job["sentence_number"],
            "formatted_prompt": job["formatted_prompt"],
            "status": "pending",
            "viral_video_id": video_record_id,
        }
        for job in image_jobs
    ]
    # Insert in batches
    for i in range(0, len(rows), 50):
        batch = rows[i : i + 50]
        supabase_client.table("yt_image_generation_jobs").insert(batch).execute()


def _get_reference_image(
    supabase_client: Any,
    video_record_id: str,
) -> str:
    """Get the reference image (thumbnail) as base64 for style matching.

    Falls back to a default style reference if thumbnail unavailable.
    """
    resp = (
        supabase_client.table("yt_viral_videos")
        .select("thumbnail")
        .eq("id", video_record_id)
        .limit(1)
        .execute()
    )
    thumbnail_url = resp.data[0].get("thumbnail", "") if resp.data else ""

    if thumbnail_url:
        try:
            img_resp = requests.get(thumbnail_url, timeout=15)
            img_resp.raise_for_status()
            import base64
            return base64.b64encode(img_resp.content).decode("utf-8")
        except requests.RequestException as e:
            logger.warning("Could not download reference image: %s", e)

    # Return a minimal 1x1 white pixel as fallback
    return (
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////"
        "////////////////////////////////////////2wBDAf//////////////////"
        "////////////////////////////////////////////////////wAARCAABAAED"
        "ASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAA"
        "AAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/a"
        "AAwDAQACEQMRAD8AKwA="
    )


def _submit_image_batch(
    video_id: str,
    image_jobs: list[dict[str, Any]],
    reference_b64: str,
    video_record_id: str,
) -> str | None:
    """Submit image batch via the process_batch_images Cloud Function.

    Returns batch_job_name or None on failure.
    """
    function_url = os.environ.get("IMAGE_BATCH_FUNCTION_URL")
    if not function_url:
        logger.error("IMAGE_BATCH_FUNCTION_URL not set")
        return None

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        logger.error("GCP_PROJECT_ID not set")
        return None

    payload = {
        "image_jobs": image_jobs,
        "reference_image_base64": reference_b64,
        "model": "gemini-3-pro-preview",
        "project_id": project_id,
        "location": "us-central1",
        "input_bucket": f"gs://yt-{video_id.lower()}/images/input",
        "output_bucket": f"gs://yt-{video_id.lower()}/images/",
    }

    try:
        resp = requests.post(function_url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("batch_job_name")
    except requests.RequestException as e:
        logger.error("Image batch Cloud Function failed: %s", e)
        return None
