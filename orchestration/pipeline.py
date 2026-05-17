"""
MindSeam pipeline — GCP Cloud Functions edition.

Produces YouTube content in the style of youtube.com/watch?v=LOzrFoSHnGA
(stick-figure illustrated psychology shorts).

Flow per video:
  1.  Pick queued video from yt_viral_videos
  2.  Fetch transcript + top comments from Supabase
  3.  Claude writes script → yt_scripts + yt_image_generation_jobs
  4.  Gemini TTS batch → Gemini Files API output
  5.  CF1  upload-audio-to-gcs  : process TTS results → GCS + yt_audio_files
  6.  CF2  image-batch-requests : submit Vertex AI Imagen batch
  7.  Vertex Guard              : poll with 30-min hard timeout + auto-cancel
  8.  CF3  generate-video       : FFmpeg render on GCS assets → final .mp4
  9.  CF4  upload-video         : YouTube upload → private, returns video URL
  10. Mark video done in Supabase
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

import anthropic
import requests
from google.cloud import storage
from supabase import Client, create_client

from orchestration.gemini_tts_batch import submit_tts_batch
from orchestration.gcp_auth import get_access_token
from orchestration.vertex_guard import poll_until_done

log = logging.getLogger(__name__)

# ── Env vars ─────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GCP_PROJECT_ID: str = os.environ["GCP_PROJECT_ID"]
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")

# GCS bucket that holds all pipeline assets (audio, images, final videos)
ASSETS_BUCKET: str = os.environ["ASSETS_BUCKET"]
# Background music bucket
BG_AUDIO_BUCKET: str = os.environ.get("BG_AUDIO_BUCKET", "yt-auto-bg-audio-ls")

# Reference image for Imagen (stick-figure style)
STICK_FIGURE_GCS_PATH: str = os.environ.get(
    "STICK_FIGURE_GCS_PATH", "reference/stickfigure.jpeg"
)

# Vertex AI region for image batch
VERTEX_LOCATION: str = os.environ.get("VERTEX_LOCATION", "us-central1")

# Cloud Function URLs (set on Railway)
CF_TTS_URL: str = os.environ["CF_TTS_URL"]            # upload-audio-to-gcs
CF_IMAGE_URL: str = os.environ["CF_IMAGE_URL"]         # image-batch-requests
CF_RENDER_URL: str = os.environ["CF_RENDER_URL"]       # generate-video
CF_UPLOAD_URL: str = os.environ["CF_UPLOAD_URL"]       # upload-video

# Script limits
CHUNK_SIZE: int = 5       # sentences per TTS audio chunk
MAX_SENTENCES: int = 130
CF_TIMEOUT: int = 600     # seconds — max wait for a synchronous CF call

IMAGEN_MODEL: str = os.environ.get("IMAGEN_MODEL", "gemini-3-pro-preview")


class Pipeline:
    def __init__(self) -> None:
        self.sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ── Public entry point ───────────────────────────────────────────────────

    def process_next(self) -> None:
        row = (
            self.sb.table("yt_viral_videos")
            .select("*")
            .eq("status", "queued")
            .eq("suitable", True)
            .limit(1)
            .execute()
        )
        if not row.data:
            return

        video = row.data[0]
        vid_id: str = video["id"]
        yt_id: str = video["video_id"]
        title: str = video["title"]
        log.info(f"[{vid_id[:8]}] Processing '{title[:60]}'")

        try:
            self._mark_status(vid_id, "processing")
            transcript = self._ensure_transcript(vid_id, yt_id)
            comments = self._fetch_comments(vid_id)
            sentences = self._ensure_scripts(vid_id, title, transcript, comments)
            if not sentences:
                raise RuntimeError("Script generation produced no sentences")

            # TTS → GCS audio
            audio_gcs_folder = self._run_tts(vid_id, sentences)

            # Vertex AI Imagen → GCS JSONL predictions
            images_output_prefix = self._run_imagen(vid_id, sentences)

            # FFmpeg render via CF3
            render_result = self._run_render(vid_id, images_output_prefix)

            # YouTube upload via CF4
            upload_result = self._run_upload(vid_id, title)

            self.sb.table("yt_results").upsert({
                "video_id": upload_result["video_id"],
                "gcs_video_url": upload_result["video_url"],
                "thumbnail_link": (
                    upload_result["video_url"]
                    if upload_result.get("thumbnail_uploaded")
                    else None
                ),
            }).execute()

            self._mark_status(
                vid_id, "done",
                notes=f"Uploaded: {upload_result['video_url']}"
            )
            log.info(f"[{vid_id[:8]}] DONE → {upload_result['video_url']}")

        except Exception as exc:
            log.exception(f"[{vid_id[:8]}] FAILED: {exc}")
            self._mark_status(vid_id, "queued", notes=f"Error: {exc}")

    # ── Status helpers ───────────────────────────────────────────────────────

    def _mark_status(self, vid_id: str, status: str, notes: str | None = None) -> None:
        patch: dict[str, Any] = {"status": status}
        if notes is not None:
            patch["production_notes"] = notes
        self.sb.table("yt_viral_videos").update(patch).eq("id", vid_id).execute()
        log.info(f"[{vid_id[:8]}] status → {status}")

    # ── Transcript / comments / script ───────────────────────────────────────

    def _ensure_transcript(self, vid_id: str, yt_id: str) -> str:
        existing = (
            self.sb.table("yt_video_transcripts")
            .select("content")
            .eq("video_record_id", vid_id)
            .execute()
        )
        if existing.data:
            return existing.data[0]["content"]
        raise RuntimeError(
            f"No transcript for {yt_id} — populate yt_video_transcripts first"
        )

    def _fetch_comments(self, vid_id: str) -> list[str]:
        rows = (
            self.sb.table("yt_comments")
            .select("content,like_count")
            .eq("video_record_id", vid_id)
            .order("like_count", desc=True)
            .limit(30)
            .execute()
        )
        return [r["content"] for r in (rows.data or []) if r.get("content")]

    def _ensure_scripts(
        self, vid_id: str, title: str, transcript: str, comments: list[str]
    ) -> list[dict]:
        existing = (
            self.sb.table("yt_scripts")
            .select("sentence_number,sentence_text")
            .eq("viral_video_id", vid_id)
            .order("sentence_number")
            .execute()
        )
        if existing.data and len(existing.data) >= 50:
            log.info(f"[{vid_id[:8]}] Scripts cached ({len(existing.data)} sentences)")
            self._rebuild_image_jobs_if_needed(vid_id, existing.data)
            return existing.data

        script_prompt = self._fetch_agent_prompt("agent3_script_writer")
        prompt_filled = (
            script_prompt
            .replace("{{title}}", title)
            .replace("{{transcript}}", transcript[:8000])
            .replace("{{comments}}", "\n".join(comments[:20]))
        )
        log.info(f"[{vid_id[:8]}] Calling Claude for script")
        resp = self.ai.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt_filled}],
        )
        raw = resp.content[0].text
        script_json = self._extract_json(raw)
        sentences = script_json.get("sentences") or script_json.get("script") or []
        if not sentences:
            raise RuntimeError(f"Script agent returned no sentences: {raw[:400]}")
        sentences = sentences[:MAX_SENTENCES]
        log.info(f"[{vid_id[:8]}] Script: {len(sentences)} sentences")

        image_prompt_template = self._fetch_agent_prompt("image_generator")
        rows_scripts = []
        rows_images = []
        for i, s in enumerate(sentences, start=1):
            text = (
                (s.get("sentence_text") or s.get("text") or str(s))
                if isinstance(s, dict)
                else str(s)
            )
            scene = (s.get("scene") if isinstance(s, dict) else None) or text
            rows_scripts.append({
                "viral_video_id": vid_id,
                "sentence_number": i,
                "sentence_text": text,
            })
            rows_images.append({
                "viral_video_id": vid_id,
                "sentence_number": i,
                "formatted_prompt": image_prompt_template.replace(
                    "{{scene_description}}", scene
                ),
            })

        self.sb.table("yt_scripts").insert(rows_scripts).execute()
        self.sb.table("yt_image_generation_jobs").insert(rows_images).execute()
        log.info(f"[{vid_id[:8]}] Saved {len(rows_scripts)} script rows + image jobs")
        return rows_scripts

    def _rebuild_image_jobs_if_needed(
        self, vid_id: str, sentences: list[dict]
    ) -> None:
        check = (
            self.sb.table("yt_image_generation_jobs")
            .select("sentence_number")
            .eq("viral_video_id", vid_id)
            .limit(1)
            .execute()
        )
        if check.data:
            return
        log.info(f"[{vid_id[:8]}] Rebuilding image jobs from cached script")
        template = self._fetch_agent_prompt("image_generator")
        rows = [
            {
                "viral_video_id": vid_id,
                "sentence_number": s["sentence_number"],
                "formatted_prompt": template.replace(
                    "{{scene_description}}", s["sentence_text"]
                ),
            }
            for s in sentences
        ]
        self.sb.table("yt_image_generation_jobs").insert(rows).execute()

    # ── Step 4+5: Gemini TTS → CF1 ──────────────────────────────────────────

    def _run_tts(self, vid_id: str, sentences: list[dict]) -> str:
        """
        Submit TTS batch to Gemini, then call CF1 to upload audio to GCS.
        Returns the GCS folder path where audio files landed.
        """
        # Check if audio already exists for this video
        existing_audio = (
            self.sb.table("yt_audio_files")
            .select("id")
            .eq("viral_video_id", vid_id)
            .limit(1)
            .execute()
        )
        if existing_audio.data:
            log.info(f"[{vid_id[:8]}] Audio already in GCS, skipping TTS")
            return f"{vid_id}/audio/"

        output_file_uri = submit_tts_batch(sentences, GEMINI_API_KEY)
        log.info(f"[{vid_id[:8]}] TTS batch done. Calling CF1 to upload audio to GCS")

        audio_folder = f"{vid_id}/audio/"
        payload = {
            "bucket_name": ASSETS_BUCKET,
            "file_name": output_file_uri,
            "folder_path": audio_folder,
        }
        resp = self._call_cf(CF_TTS_URL, payload, timeout=300)
        if not resp.get("success"):
            raise RuntimeError(f"CF1 (upload-audio-to-gcs) failed: {resp}")

        uploaded = resp.get("uploaded_files", [])
        log.info(
            f"[{vid_id[:8]}] CF1 uploaded {len(uploaded)} audio files to GCS"
        )

        # Save audio file records to yt_audio_files (one row per sentence)
        # Map uploaded keys (sentence numbers) back to sentence metadata
        sentence_map = {str(s["sentence_number"]): s for s in sentences}
        rows_audio = []
        for uf in uploaded:
            key = str(uf["key"])
            s = sentence_map.get(key)
            sn = int(key) if key.isdigit() else 0
            rows_audio.append({
                "viral_video_id": vid_id,
                "batch_number": 1,
                "file_url": uf["gcs_uri"],
                "file_path": uf["gcs_uri"].replace(f"gs://{ASSETS_BUCKET}/", ""),
                "start_sentence_number": sn,
                "end_sentence_number": sn,
                "chunk_size": 1,
                "sentence_count": 1,
            })
        if rows_audio:
            self.sb.table("yt_audio_files").insert(rows_audio).execute()
            log.info(f"[{vid_id[:8]}] Saved {len(rows_audio)} audio rows to Supabase")

        return audio_folder

    # ── Step 6+7: Vertex AI Imagen via CF2 ──────────────────────────────────

    def _run_imagen(self, vid_id: str, sentences: list[dict]) -> str:
        """
        Submit Vertex AI Imagen batch via CF2, poll with hard 30-min guard.
        Returns GCS prefix where prediction JSONL files are stored.
        """
        # Check if images already processed
        existing_batch = (
            self.sb.table("yt_batch_jobs")
            .select("batch_job_name,status")
            .eq("viral_video_id", vid_id)
            .eq("media_type", "image")
            .eq("status", "completed")
            .limit(1)
            .execute()
        )
        if existing_batch.data:
            job_name = existing_batch.data[0]["batch_job_name"]
            log.info(f"[{vid_id[:8]}] Images already generated, skipping Imagen")
            return self._images_output_prefix(vid_id, job_name)

        # Fetch image jobs from Supabase
        jobs_rows = (
            self.sb.table("yt_image_generation_jobs")
            .select("sentence_number,formatted_prompt")
            .eq("viral_video_id", vid_id)
            .order("sentence_number")
            .execute()
        )
        if not jobs_rows.data:
            raise RuntimeError("No image jobs in yt_image_generation_jobs")

        # Deduplicate
        seen: set[int] = set()
        unique_jobs = []
        for row in jobs_rows.data:
            k = row["sentence_number"]
            if k not in seen:
                seen.add(k)
                unique_jobs.append(row)

        # Load stickfigure reference image from GCS
        ref_b64 = self._load_reference_image_b64()

        images_output_prefix = f"gs://{ASSETS_BUCKET}/{vid_id}/images/"

        payload = {
            "image_jobs": unique_jobs,
            "reference_image_base64": ref_b64,
            "model": IMAGEN_MODEL,
            "project_id": GCP_PROJECT_ID,
            "location": VERTEX_LOCATION,
            "input_bucket": f"gs://{ASSETS_BUCKET}/{vid_id}/imagen-input/",
            "output_bucket": images_output_prefix,
        }

        log.info(
            f"[{vid_id[:8]}] Submitting {len(unique_jobs)} images to Vertex AI Imagen"
        )
        resp = self._call_cf(CF_IMAGE_URL, payload, timeout=120)
        if not resp.get("success"):
            raise RuntimeError(f"CF2 (image-batch-requests) failed: {resp}")

        batch_job_name: str = resp["batch_job_name"]
        log.info(f"[{vid_id[:8]}] Imagen batch job: {batch_job_name}")

        # Record batch job
        self.sb.table("yt_batch_jobs").insert({
            "batch_job_name": batch_job_name,
            "status": "pending",
            "viral_video_id": vid_id,
            "media_type": "image",
        }).execute()

        # ── CRITICAL: 30-min hard timeout + auto-cancel ──────────────────────
        poll_until_done(batch_job_name, location=VERTEX_LOCATION)

        # Mark complete in Supabase
        self.sb.table("yt_batch_jobs").update({"status": "completed"}).eq(
            "batch_job_name", batch_job_name
        ).execute()
        log.info(f"[{vid_id[:8]}] Imagen batch complete: {batch_job_name}")

        return images_output_prefix

    def _images_output_prefix(self, vid_id: str, batch_job_name: str) -> str:
        return f"gs://{ASSETS_BUCKET}/{vid_id}/images/"

    def _load_reference_image_b64(self) -> str:
        """Load stickfigure.jpeg from GCS and return base64 string."""
        client = storage.Client()
        blob = client.bucket(ASSETS_BUCKET).blob(STICK_FIGURE_GCS_PATH)
        img_bytes = blob.download_as_bytes()
        return base64.b64encode(img_bytes).decode("utf-8")

    # ── Step 8: FFmpeg render via CF3 ───────────────────────────────────────

    def _run_render(self, vid_id: str, images_output_prefix: str) -> dict:
        """Call CF3 to render video. Returns CF response dict."""
        payload = {
            "viral_video_id": vid_id,
            "assets_bucket": ASSETS_BUCKET,
            "bg_music_bucket": BG_AUDIO_BUCKET,
            "bg_music_name": "audio1.mp3",
            "bg_volume": 0.15,
        }
        log.info(f"[{vid_id[:8]}] Calling CF3 (generate-video)")
        resp = self._call_cf(CF_RENDER_URL, payload, timeout=CF_TIMEOUT)
        if not resp.get("success"):
            raise RuntimeError(f"CF3 (generate-video) failed: {resp}")
        log.info(
            f"[{vid_id[:8]}] CF3 done: {resp.get('chunks_processed')} chunks, "
            f"{resp.get('processing_time_seconds')}s"
        )
        return resp

    # ── Step 9: YouTube upload via CF4 ──────────────────────────────────────

    def _run_upload(self, vid_id: str, title: str) -> dict:
        """Call CF4 to upload video to YouTube. Returns upload response dict."""
        payload = {
            "bucket_name": ASSETS_BUCKET,
            "file_name": f"{vid_id}/final_videos/{vid_id}.mp4",
            "thumbnail_file": f"{vid_id}/final_videos/{vid_id}_thumb.jpg",
            "title": title[:100],
            "description": self._build_description(title),
            "tags": [
                "psychology", "psychologyfacts", "mindseam",
                "humanbehavior", "selfimprovement", "mentalhealth",
            ],
            "category_id": "27",
            "privacy_status": "private",
        }
        log.info(f"[{vid_id[:8]}] Calling CF4 (upload-video)")
        resp = self._call_cf(CF_UPLOAD_URL, payload, timeout=CF_TIMEOUT)
        if not resp.get("success"):
            raise RuntimeError(f"CF4 (upload-video) failed: {resp}")
        log.info(f"[{vid_id[:8]}] YouTube: {resp.get('video_url')}")
        return resp

    # ── Utilities ────────────────────────────────────────────────────────────

    def _call_cf(self, url: str, payload: dict, timeout: int = 300) -> dict:
        """
        Call a Cloud Function with IAM bearer token auth.
        Raises RuntimeError on HTTP error or non-JSON response.
        """
        token = get_access_token()
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"CF call to {url} failed HTTP {resp.status_code}: {resp.text[:400]}"
            )
        try:
            return resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"CF response from {url} is not JSON: {resp.text[:200]}"
            ) from exc

    def _fetch_agent_prompt(self, agent_name: str) -> str:
        row = (
            self.sb.table("yt_agent_prompts")
            .select("prompt_content")
            .eq("agent_name", agent_name)
            .execute()
        )
        if not row.data:
            raise RuntimeError(f"Missing agent prompt: {agent_name}")
        return row.data[0]["prompt_content"]

    def _extract_json(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise

    def _build_description(self, title: str) -> str:
        return (
            f"{title}\n\n"
            "Explore the psychology behind everything you feel on MindSeam.\n\n"
            "#psychology #psychologyfacts #mindseam #humanbehavior #mentalhealth\n"
        )
