"""TTS pipeline using Gemini non-batch API (one call per sentence)."""

import base64
import io
import json
import logging
import os
import struct
import time
from typing import Any

import google.auth
import google.auth.transport.requests
import requests

from execution.services.gcs_client import ensure_bucket_exists, upload_bytes

logger = logging.getLogger(__name__)

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "youtube-automation-492419")
GCP_LOCATION = "us-central1"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
VERTEX_TTS_URL = (
    f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT}"
    f"/locations/{GCP_LOCATION}/publishers/google/models/{TTS_MODEL}:generateContent"
)
CHUNK_SIZE = 5
VOICE_NAME = "Kore"


def _get_vertex_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000) -> bytes:
    data_size = len(pcm_data)
    byte_rate = sample_rate * 2
    buf = io.BytesIO()
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', data_size + 36))
    buf.write(b'WAVE')
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))
    buf.write(struct.pack('<H', 1))
    buf.write(struct.pack('<H', 1))
    buf.write(struct.pack('<I', sample_rate))
    buf.write(struct.pack('<I', byte_rate))
    buf.write(struct.pack('<H', 2))
    buf.write(struct.pack('<H', 16))
    buf.write(b'data')
    buf.write(struct.pack('<I', data_size))
    buf.write(pcm_data)
    return buf.getvalue()


def _generate_audio_for_text(text: str) -> bytes | None:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": VOICE_NAME}
                }
            }
        }
    }
    try:
        token = _get_vertex_token()
        resp = requests.post(
            VERTEX_TTS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        b64_audio = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        pcm = base64.b64decode(b64_audio)
        return _pcm_to_wav(pcm)
    except Exception as e:
        logger.error("Vertex TTS generation failed: %s", e)
        return None


def run_tts_pipeline(
    supabase_client: Any,
    video_record_id: str,
    video_id: str,
) -> int:
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

    bucket_name = f"yt-{video_id.lower()}"
    ensure_bucket_exists(bucket_name)

    chunks = []
    for i in range(0, len(sentences), CHUNK_SIZE):
        chunks.append(sentences[i:i + CHUNK_SIZE])

    audio_count = 0
    for batch_num, chunk in enumerate(chunks):
        text = " ".join(s["sentence_text"] for s in chunk)
        start_num = chunk[0]["sentence_number"]
        end_num = chunk[-1]["sentence_number"]

        # Always sleep BEFORE call to stay under 10 req/min
        if batch_num > 0:
            time.sleep(2)

        logger.info("TTS chunk %d/%d (sentences %d-%d)", batch_num + 1, len(chunks), start_num, end_num)

        wav_bytes = _generate_audio_for_text(text)
        if not wav_bytes:
            logger.warning("TTS chunk %d failed, sleeping 60s and retrying once", batch_num)
            time.sleep(60)
            wav_bytes = _generate_audio_for_text(text)
            if not wav_bytes:
                logger.error("Skipping chunk %d (TTS failed after retry)", batch_num)
                continue

        file_path = f"audio_files/audio_{batch_num:04d}.wav"
        upload_bytes(bucket_name, file_path, wav_bytes, content_type="audio/wav")

        supabase_client.table("yt_audio_files").insert({
            "viral_video_id": video_record_id,
            "batch_number": batch_num,
            "file_url": f"gs://{bucket_name}/{file_path}",
            "file_path": file_path,
            "start_sentence_number": start_num,
            "end_sentence_number": end_num,
            "chunk_size": len(chunk),
            "sentence_count": len(chunk),
            "file_size_bytes": len(wav_bytes),
        }).execute()

        audio_count += 1

    logger.info("TTS pipeline complete: %d audio files", audio_count)
    return audio_count
