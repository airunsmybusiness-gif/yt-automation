"""Full pipeline orchestrator — end-to-end from approval to YouTube upload.

Phase 2: Data collection + Agent pipeline
Phase 3: TTS audio + Image generation
Phase 4: Video render + YouTube upload + notification

This is the orchestration layer: it reasons and routes, never computes.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from execution.agents.agent_runner import (
    run_agent1_analyzer,
    run_agent2_strategist,
    run_agent3_script_writer,
    run_agent4_optimizer,
    save_script_to_db,
)
from execution.services.comment_scraper import scrape_comments
from execution.services.gmail_service import send_error_alert
from execution.services.supabase_client import update_viral_video
from execution.services.thumbnail_describer import describe_thumbnail
from execution.services.transcript_service import fetch_transcript

logger = logging.getLogger(__name__)

DATA_COLLECTION_TIMEOUT = 300  # 5 minutes


def run_pipeline_for_video(
    supabase_client: Any,
    settings: Any,
    video: dict[str, Any],
) -> bool:
    """Run the full pipeline for one approved video.

    Phases:
        2. Data collection + Agent pipeline
        3. TTS + Image generation
        4. Video render + YouTube upload

    Args:
        supabase_client: Supabase client.
        settings: App settings.
        video: yt_viral_videos record (suitable=true).

    Returns:
        True if pipeline completed successfully.
    """
    video_id = video["video_id"]
    record_id = video["id"]

    logger.info("=== PIPELINE START: %s ===", video_id)

    # Step 1: Mark production started
    update_viral_video(supabase_client, record_id, {
        "status": "production_started",
        "production_started_at": datetime.now(timezone.utc).isoformat(),
    })

    # ===== PHASE 2: Data Collection + Agents =====
    try:
        transcript, comments = _run_data_collection(supabase_client, video)
    except Exception as e:
        _handle_pipeline_error(supabase_client, settings, video, "Data Collection", e)
        return False

    if not transcript:
        _handle_pipeline_error(
            supabase_client, settings, video, "Data Collection",
            Exception("Transcript unavailable — cannot proceed"),
        )
        return False

    try:
        _run_agent_pipeline(supabase_client, video, transcript, comments)
    except Exception as e:
        _handle_pipeline_error(supabase_client, settings, video, "Agent Pipeline", e)
        return False

    # ===== PHASE 3: TTS + Images =====
    try:
        _run_media_generation(supabase_client, video)
    except Exception as e:
        _handle_pipeline_error(supabase_client, settings, video, "Media Generation", e)
        return False

    # ===== PHASE 4: Render + Upload =====
    try:
        _run_render_and_upload(supabase_client, settings, video)
    except Exception as e:
        _handle_pipeline_error(supabase_client, settings, video, "Render & Upload", e)
        return False

    logger.info("=== PIPELINE COMPLETE: %s ===", video_id)
    return True


# ---------------------------------------------------------------------------
# Phase 2: Data Collection
# ---------------------------------------------------------------------------

def _run_data_collection(
    supabase_client: Any,
    video: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run 3 parallel data collection jobs."""
    record_id = video["id"]
    video_id = video["video_id"]
    thumbnail_url = video.get("thumbnail", "")

    transcript_result: str | None = None
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                fetch_transcript, supabase_client, record_id, video_id
            ): "transcript",
            executor.submit(
                scrape_comments, supabase_client, record_id, video_id
            ): "comments",
            executor.submit(
                describe_thumbnail, supabase_client, record_id, thumbnail_url
            ): "thumbnail",
        }

        for future in as_completed(futures, timeout=DATA_COLLECTION_TIMEOUT):
            job_name = futures[future]
            try:
                result = future.result()
                if job_name == "transcript":
                    transcript_result = result
                logger.info("Data collection '%s' completed", job_name)
            except Exception as e:
                errors.append(f"{job_name}: {e}")
                logger.error("Data collection '%s' failed: %s", job_name, e)

    # Fetch comments from DB
    resp = (
        supabase_client.table("yt_comments")
        .select("*")
        .eq("video_record_id", record_id)
        .execute()
    )
    comments_result = resp.data or []

    if errors:
        logger.warning("Data collection errors: %s", errors)

    return transcript_result, comments_result


# ---------------------------------------------------------------------------
# Phase 2: Agent Pipeline
# ---------------------------------------------------------------------------

def _run_agent_pipeline(
    supabase_client: Any,
    video: dict[str, Any],
    transcript: str,
    comments: list[dict[str, Any]],
) -> None:
    """Run 4 sequential agents."""
    record_id = video["id"]

    # Re-fetch video with latest data
    resp = (
        supabase_client.table("yt_viral_videos")
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    video = resp.data[0] if resp.data else video

    analyzer_result = run_agent1_analyzer(supabase_client, video, transcript, comments)
    strategist_result = run_agent2_strategist(supabase_client, video, analyzer_result)
    sentences = run_agent3_script_writer(supabase_client, video, analyzer_result, strategist_result)
    optimized = run_agent4_optimizer(supabase_client, video, sentences)
    count = save_script_to_db(supabase_client, record_id, optimized)
    logger.info("Agent pipeline complete: %d final sentences", count)


# ---------------------------------------------------------------------------
# Phase 3: Media Generation
# ---------------------------------------------------------------------------

def _run_media_generation(
    supabase_client: Any,
    video: dict[str, Any],
) -> None:
    """Run TTS and image generation in parallel."""
    from execution.services.image_service import run_image_pipeline
    from execution.services.tts_service import run_tts_pipeline

    record_id = video["id"]
    video_id = video["video_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        tts_future = executor.submit(
            run_tts_pipeline, supabase_client, record_id, video_id
        )
        img_future = executor.submit(
            run_image_pipeline, supabase_client, record_id, video_id
        )

        tts_count = tts_future.result(timeout=900)
        img_batch = img_future.result(timeout=900)

    logger.info(
        "Media generation: %d audio files, image batch=%s",
        tts_count, img_batch,
    )

    if tts_count == 0:
        raise RuntimeError("TTS produced 0 audio files")
    if not img_batch:
        raise RuntimeError("Image batch submission failed")

    # Wait for image batch to complete
    _wait_for_image_batch(supabase_client, img_batch)


def _wait_for_image_batch(
    supabase_client: Any,
    batch_name: str,
    max_wait_seconds: int = 900,
    poll_interval: int = 30,
) -> None:
    """Poll yt_batch_jobs until image batch completes."""
    elapsed = 0
    while elapsed < max_wait_seconds:
        resp = (
            supabase_client.table("yt_batch_jobs")
            .select("status")
            .eq("batch_job_name", batch_name)
            .limit(1)
            .execute()
        )
        if resp.data:
            status = resp.data[0].get("status", "pending")
            if status == "completed":
                logger.info("Image batch completed: %s", batch_name)
                return
            if status == "failed":
                raise RuntimeError(f"Image batch failed: {batch_name}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise RuntimeError(f"Image batch timed out after {max_wait_seconds}s: {batch_name}")


# ---------------------------------------------------------------------------
# Phase 4: Render + Upload
# ---------------------------------------------------------------------------

def _run_render_and_upload(
    supabase_client: Any,
    settings: Any,
    video: dict[str, Any],
) -> None:
    """Render video, generate thumbnail, upload to YouTube, notify."""
    from execution.services.video_render_service import (
        generate_thumbnail,
        render_video,
        upload_to_youtube,
    )

    record_id = video["id"]
    video_id = video["video_id"]

    gcs_url = render_video(supabase_client, record_id, video_id)
    if not gcs_url:
        raise RuntimeError("Video rendering failed")

    thumb_uri = generate_thumbnail(supabase_client, record_id, video_id)
    if not thumb_uri:
        logger.warning("Thumbnail failed, uploading without it")

    yt_url = upload_to_youtube(supabase_client, record_id, video_id)
    if not yt_url:
        raise RuntimeError("YouTube upload failed")

    # Send notification
    try:
        send_error_alert(
            settings,
            f"Video Published: {video.get('title', 'Unknown')[:60]}",
            (
                f"Your new video is ready!\n\n"
                f"Title: {video.get('title', 'N/A')}\n"
                f"YouTube URL: {yt_url}\n"
                f"Source: https://youtube.com/watch?v={video['video_id']}\n"
                f"Status: Private (review before making public)"
            ),
        )
    except Exception as e:
        logger.error("Failed to send completion notification: %s", e)


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

def _handle_pipeline_error(
    supabase_client: Any,
    settings: Any,
    video: dict[str, Any],
    stage: str,
    error: Exception,
) -> None:
    """Handle pipeline failure: log, update DB, alert."""
    video_id = video.get("video_id", "unknown")
    record_id = video.get("id")

    logger.error(
        "ANNEALING: Pipeline failed at '%s' for %s: %s",
        stage, video_id, error, exc_info=True,
    )

    if record_id:
        update_viral_video(supabase_client, record_id, {
            "production_notes": f"Failed at {stage}: {str(error)[:500]}",
        })

    try:
        send_error_alert(
            settings,
            f"Pipeline Failed: {stage}",
            f"Video: {video_id}\nStage: {stage}\nError: {error}",
        )
    except Exception as e:
        logger.error("Could not send error alert: %s", e)
