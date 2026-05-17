"""Pipeline orchestrator — the single function that runs an entire video.

This is the heart of the automation. Called by process_next scheduler job
when a video has suitable=true and status=queued.
"""

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from config.settings import Settings

logger = logging.getLogger(__name__)


def process_video(
    supabase_client: Any,
    settings: Settings,
    video: dict[str, Any],
) -> bool:
    """Run the full pipeline for one approved video.

    Stages:
        1. Data collection (transcript, comments, thumbnail desc)
        2. Agent pipeline (analyzer → strategist → script → optimizer)
        3. Media generation (TTS + images, cost-capped)
        4. Render (FFmpeg, crossfade transitions)
        5. Upload (YouTube + thumbnail)
        6. Cleanup

    Args:
        supabase_client: Supabase client.
        settings: Validated settings.
        video: yt_viral_videos record with suitable=true.

    Returns:
        True if pipeline completed and video uploaded.
    """
    video_id = video["video_id"]
    record_id = video["id"]
    work_dir = Path(tempfile.mkdtemp(prefix=f"yt_{video_id}_"))

    from execution.agents.agent_runner import run_agent_pipeline, transform_scene
    from execution.services.tts_edge import generate_sentence_audio
    from execution.services.image_replicate import generate_batch
    from execution.services.video_render import render_video
    from execution.services.youtube_upload import upload_video
    logger.info("=== PIPELINE START: %s ===", video_id)
    logger.info("Work dir: %s", work_dir)

    try:
        # Mark production started
        _update_video(supabase_client, record_id, {
            "status": "production_started",
            "production_started_at": _now_iso(),
        })

        # --- STAGE 1: Data Collection ---
        transcript = _collect_transcript(supabase_client, video)
        if not transcript:
            raise RuntimeError("Transcript unavailable from all sources")

        comments = _collect_comments(supabase_client, video)

        # --- STAGE 2: Agent Pipeline ---
        agent_result = run_agent_pipeline(
            supabase_client,
            settings.anthropic_api_key,
            video,
            transcript,
            comments,
        )
        sentences = agent_result["sentences"]
        strategist_raw = agent_result["strategist_result"]

        if len(sentences) < 20:
            raise RuntimeError(f"Script too short: {len(sentences)} sentences")

        # --- STAGE 3: Media Generation ---
        # 3a: TTS — one audio per sentence
        audio_dir = work_dir / "audio"
        audio_results = generate_sentence_audio(
            sentences, audio_dir, settings.edge_tts_voice,
        )
        if len(audio_results) < len(sentences) * 0.8:
            logger.warning(
                "TTS coverage low: %d/%d",
                len(audio_results), len(sentences),
            )

        # 3b: Pair sentences for images (2 per image)
        pairs = _pair_sentences(audio_results)

        # 3c: Scene transform + image generation
        image_dir = work_dir / "images"
        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        image_jobs = []
        for pair in pairs:
            scene_texts = [s["text"] for s in pair["sentences"]]
            scene_prompt = transform_scene(anthropic_client, scene_texts)
            image_jobs.append({
                "pair_number": pair["pair_number"],
                "prompt": scene_prompt,
            })

        img_result = generate_batch(
            image_jobs, image_dir,
            max_cost=settings.max_cost_per_video,
            max_images=settings.max_images_per_video,
        )

        # Build render pairs (match images to audio)
        render_pairs = _build_render_pairs(pairs, img_result["generated"], image_dir)

        if not render_pairs:
            raise RuntimeError("No render pairs — image generation failed")

        # 3d: Thumbnail
        thumbnail_path = _generate_thumbnail(
            anthropic_client, strategist_raw, image_dir, settings,
        )

        # --- STAGE 4: Render ---
        final_mp4 = render_video(render_pairs, work_dir)
        logger.info("Final video: %s", final_mp4)

        # --- STAGE 5: Upload ---
        strategy = _parse_strategy(strategist_raw)
        upload_result = upload_video(
            video_path=final_mp4,
            title=strategy.get("title", f"Psychology Facts: {video.get('title', 'Unknown')}")[:100],
            description=strategy.get("description", "")[:5000],
            tags=strategy.get("tags", ["psychology", "self improvement"])[:20],
            category_id=strategy.get("category_id", "27"),
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret,
            refresh_token=settings.youtube_refresh_token,
            thumbnail_path=thumbnail_path,
            privacy_status="private",
        )

        logger.info("Uploaded: %s", upload_result["video_url"])

        # Save result
        supabase_client.table("yt_results").insert({
            "video_id": video["video_id"],
            "gcs_video_url": upload_result["video_url"],
            "thumbnail_link": str(thumbnail_path) if thumbnail_path else None,
        }).execute()

        _update_video(supabase_client, record_id, {
            "status": "done",
            "production_completed_at": _now_iso(),
            "production_notes": json.dumps({
                "youtube_id": upload_result["video_id"],
                "youtube_url": upload_result["video_url"],
                "sentences": len(sentences),
                "images": len(img_result["generated"]),
                "image_cost": img_result["total_cost"],
                "thumbnail_uploaded": upload_result["thumbnail_uploaded"],
            }),
        })

        logger.info("=== PIPELINE COMPLETE: %s → %s ===", video_id, upload_result["video_url"])
        return True

    except Exception as e:
        logger.error("ANNEALING: Pipeline failed for %s: %s", video_id, e, exc_info=True)
        _update_video(supabase_client, record_id, {
            "production_notes": f"Failed: {str(e)[:500]}",
        })
        return False

    finally:
        # --- STAGE 6: Cleanup ---
        try:
            shutil.rmtree(work_dir)
            logger.info("Cleaned up %s", work_dir)
        except Exception as e:
            logger.warning("Cleanup failed: %s", e)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_video(supabase_client: Any, record_id: str, fields: dict) -> None:
    """Update a yt_viral_videos record."""
    supabase_client.table("yt_viral_videos").update(fields).eq("id", record_id).execute()


def _now_iso() -> str:
    """Current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _collect_transcript(supabase_client: Any, video: dict) -> str | None:
    """Fetch transcript from yt_video_transcripts or generate via API."""
    resp = (
        supabase_client.table("yt_video_transcripts")
        .select("content")
        .eq("video_record_id", video["id"])
        .eq("type", "source")
        .limit(1)
        .execute()
    )
    if resp.data and resp.data[0].get("content"):
        return resp.data[0]["content"]

    # TODO: Supadata API fallback, then Gemini fallback
    logger.warning("No transcript found for %s", video["video_id"])
    return None


def _collect_comments(supabase_client: Any, video: dict) -> list[dict]:
    """Fetch comments from yt_comments."""
    resp = (
        supabase_client.table("yt_comments")
        .select("content, like_count")
        .eq("video_record_id", video["id"])
        .order("like_count", desc=True)
        .limit(50)
        .execute()
    )
    return resp.data or []


def _pair_sentences(
    audio_results: list[dict],
) -> list[dict]:
    """Group audio results into pairs of 2 for image assignment."""
    pairs = []
    pair_num = 1
    for i in range(0, len(audio_results), 2):
        chunk = audio_results[i:i + 2]
        pairs.append({
            "pair_number": pair_num,
            "sentences": chunk,
            "audio_paths": [s["path"] for s in chunk],
        })
        pair_num += 1
    return pairs


def _build_render_pairs(
    pairs: list[dict],
    generated_images: list[dict],
    image_dir: Path,
) -> list[dict]:
    """Match generated images to audio pairs for rendering."""
    img_map = {g["pair_number"]: g["path"] for g in generated_images}

    # Find a fallback image (last successfully generated)
    fallback_img = None
    if generated_images:
        fallback_img = generated_images[-1]["path"]

    render_pairs = []
    for pair in pairs:
        img_path = img_map.get(pair["pair_number"])
        if not img_path and fallback_img:
            img_path = fallback_img
        if not img_path:
            logger.warning("No image for pair %d, skipping", pair["pair_number"])
            continue

        render_pairs.append({
            "pair_number": pair["pair_number"],
            "image_path": img_path,
            "audio_paths": pair["audio_paths"],
        })

    return render_pairs


def _generate_thumbnail(
    anthropic_client: anthropic.Anthropic,
    strategist_raw: str,
    image_dir: Path,
    settings: Settings,
) -> Path | None:
    """Generate thumbnail from Strategist brief via Replicate."""
    try:
        from execution.services.image_replicate import generate_single_image

        # Extract thumbnail brief from strategist output
        thumb_prompt = transform_scene(
            anthropic_client,
            [f"YouTube thumbnail for psychology video. {strategist_raw[:500]}"],
        )

        thumb_path = image_dir / "thumbnail.jpg"
        result = generate_single_image(
            thumb_prompt, thumb_path,
            style_prefix=(
                "YouTube thumbnail, bold dramatic composition, high contrast, "
                "warm saturated colors, cinematic, 16:9, no text"
            ),
        )
        return result
    except Exception as e:
        logger.error("Thumbnail generation failed: %s", e)
        return None


def _parse_strategy(strategist_raw: str) -> dict:
    """Try to parse strategist output as JSON, fall back to defaults."""
    try:
        return json.loads(strategist_raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON block in output
    if "{" in strategist_raw and "}" in strategist_raw:
        start = strategist_raw.index("{")
        end = strategist_raw.rindex("}") + 1
        try:
            return json.loads(strategist_raw[start:end])
        except json.JSONDecodeError:
            pass

    return {
        "title": "Psychology Facts You Need to Know",
        "description": "",
        "tags": ["psychology", "self improvement", "mental health"],
        "category_id": "27",
    }
