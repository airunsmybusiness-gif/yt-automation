"""TTS service — Gemini batch TTS for sentence-by-sentence voiceover.

Flow:
1. Group sentences into chunks
2. Build JSONL batch request
3. Submit to Gemini batch API
4. Poll until complete
5. Call Cloud Function to extract WAV files to GCS
6. Save records to yt_audio_files
"""

import json
import logging
import os
import time
from typing import Any

import requests

from execution.services.gcs_client import ensure_bucket_exists, upload_string

logger = logging.getLogger(__name__)

GEMINI_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:batchGenerateContent"
CHUNK_SIZE = 5  # sentences per audio file
MAX_POLL_ATTEMPTS = 20  # 20 * 30s = 10 minutes
POLL_INTERVAL_SECONDS = 30


def run_tts_pipeline(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> int:
    """Run the full TTS pipeline for a video.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        video_id: YouTube video ID (also used as GCS bucket name).

    Returns:
        Number of audio files generated.
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
        logger.warning("No sentences found for TTS: %s", video_record_id)
        return 0

    logger.info("TTS pipeline: %d sentences for %s", len(sentences), video_id)

    # Group into chunks
    chunks = _group_sentences(sentences, CHUNK_SIZE)

    # Build JSONL
    jsonl_content = _build_tts_jsonl(chunks)

    # Upload JSONL to GCS
    bucket_name = f"yt-{video_id.lower()}"
    ensure_bucket_exists(bucket_name)
    ts = int(time.time())
    input_path = f"tts_input/batch-tts-{ts}.jsonl"
    input_uri = upload_string(bucket_name, input_path, jsonl_content)

    # Submit batch job
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set for TTS")

    batch_job = _submit_batch_job(api_key, input_uri, bucket_name)
    batch_name = batch_job.get("name", "")

    # Track in yt_batch_jobs
    supabase_client.table("yt_batch_jobs").insert({
        "batch_job_name": batch_name,
        "status": "pending",
        "viral_video_id": video_record_id,
        "media_type": "audio",
    }).execute()

    logger.info("TTS batch submitted: %s", batch_name)

    # Poll for completion
    result = _poll_batch_job(api_key, batch_name)
    if not result:
        supabase_client.table("yt_batch_jobs").update(
            {"status": "failed"}
        ).eq("batch_job_name", batch_name).execute()
        logger.error("TTS batch job failed or timed out: %s", batch_name)
        return 0

    # Extract audio via Cloud Function
    audio_count = _extract_audio_files(
        supabase_client, video_record_id, video_id, bucket_name, result
    )

    # Update batch job status
    supabase_client.table("yt_batch_jobs").update({
        "status": "completed",
        "images_generated": audio_count,
    }).eq("batch_job_name", batch_name).execute()

    logger.info("TTS pipeline complete: %d audio files for %s", audio_count, video_id)
    return audio_count


def _group_sentences(
    sentences: list[dict[str, Any]], chunk_size: int
) -> list[dict[str, Any]]:
    """Group sentences into chunks for TTS.

    Each chunk has:
        - start_sentence_number
        - end_sentence_number
        - combined_text
    """
    chunks = []
    for i in range(0, len(sentences), chunk_size):
        group = sentences[i : i + chunk_size]
        chunks.append({
            "start_sentence_number": group[0]["sentence_number"],
            "end_sentence_number": group[-1]["sentence_number"],
            "combined_text": " ".join(s["sentence_text"] for s in group),
            "sentence_count": len(group),
        })
    return chunks


def _build_tts_jsonl(chunks: list[dict[str, Any]]) -> str:
    """Build JSONL content for Gemini TTS batch.

    Each line has key = start_sentence_number.
    """
    lines = []
    for chunk in chunks:
        entry = {
            "key": str(chunk["start_sentence_number"]),
            "request": {
                "contents": [{
                    "parts": [{
                        "text": chunk["combined_text"],
                    }],
                }],
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": "Kore",
                            },
                        },
                    },
                },
            },
        }
        lines.append(json.dumps(entry))
    return "\n".join(lines)


def _submit_batch_job(
    api_key: str, input_uri: str, output_bucket: str
) -> dict[str, Any]:
    """Submit a Gemini batch TTS job."""
    url = "https://generativelanguage.googleapis.com/v1beta/batchJobs"
    payload = {
        "displayName": f"tts-batch-{int(time.time())}",
        "model": "models/gemini-2.0-flash",
        "src": input_uri,
        "dest": f"gs://{output_bucket}/tts_output/",
    }
    resp = requests.post(
        url,
        params={"key": api_key},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _poll_batch_job(api_key: str, batch_name: str) -> dict[str, Any] | None:
    """Poll a Gemini batch job until completion or timeout."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{batch_name}"

    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            resp = requests.get(url, params={"key": api_key}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            state = data.get("state", "")

            if state == "JOB_STATE_SUCCEEDED":
                logger.info("TTS batch completed: %s", batch_name)
                return data
            if state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                logger.error("TTS batch %s: %s", state, batch_name)
                return None

            logger.debug(
                "TTS batch poll %d/%d: %s", attempt + 1, MAX_POLL_ATTEMPTS, state
            )
        except requests.RequestException as e:
            logger.warning("TTS poll request failed: %s", e)

    logger.error("TTS batch timed out after %d attempts: %s", MAX_POLL_ATTEMPTS, batch_name)
    return None


def _extract_audio_files(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
    bucket_name: str,
    batch_result: dict[str, Any],
) -> int:
    """Call the upload-audio-to-gcs Cloud Function to extract WAV files.

    Then save records to yt_audio_files.
    """
    cloud_function_url = os.environ.get("AUDIO_EXTRACT_FUNCTION_URL")
    if not cloud_function_url:
        logger.warning("AUDIO_EXTRACT_FUNCTION_URL not set, skipping extraction")
        return 0

    dest_response = batch_result.get("dest", "")
    # The batch result file name from Gemini
    result_file = batch_result.get("name", "")

    payload = {
        "bucket_name": bucket_name,
        "file_name": result_file,
        "folder_path": f"audio_files/",
    }

    try:
        resp = requests.post(cloud_function_url, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("Audio extraction Cloud Function failed: %s", e)
        return 0

    uploaded = data.get("uploaded_files", [])

    # Save to yt_audio_files
    batch_num = 1
    for file_info in uploaded:
        key = int(file_info.get("key", 0))
        gcs_uri = file_info.get("gcs_uri", "")

        supabase_client.table("yt_audio_files").insert({
            "viral_video_id": video_record_id,
            "batch_number": batch_num,
            "file_url": gcs_uri,
            "file_path": gcs_uri.replace(f"gs://{bucket_name}/", ""),
            "start_sentence_number": key,
            "end_sentence_number": key + CHUNK_SIZE - 1,
            "chunk_size": CHUNK_SIZE,
        }).execute()

    logger.info("Extracted %d audio files for %s", len(uploaded), video_id)
    return len(uploaded)
