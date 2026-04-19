"""
orchestration/pipeline.py
─────────────────────────
Single source of truth for all pipeline stage transitions.
Each stage is idempotent — safe to retry.

Stage order:
  queued
    → fetch_transcript
    → run_agents          (4 Claude agents)
    → generate_tts        (Vertex AI Gemini TTS)
    → generate_images     (Imagen 3.0 via image-batch-requests CF)
    → render_video        (generate-video CF — auto-uploads to YouTube)
    → done
"""

import logging
import os
import time
from typing import Any

import requests
from anthropic import Anthropic
from supabase import Client, create_client

log = logging.getLogger("pipeline")

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GCP_PROJECT_ID: str = os.environ["GCP_PROJECT_ID"]
IMAGE_CF_URL: str = os.environ["IMAGE_CF_URL"]
GENERATE_VIDEO_CF_URL: str = os.environ["GENERATE_VIDEO_CF_URL"]
SUPADATA_API_KEY: str = os.environ.get("SUPADATA_API_KEY", "")

MODEL = "claude-sonnet-4-6"
GCP_LOCATION = "us-central1"


def _sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _set_status(
    sb: Client,
    viral_video_id: str,
    status: str,
    notes: str | None = None,
) -> None:
    payload: dict[str, Any] = {"status": status}
    if status == "production_started":
        payload["production_started_at"] = "now()"
    if status == "done":
        payload["production_completed_at"] = "now()"
    if notes is not None:
        payload["production_notes"] = notes
    sb.table("yt_viral_videos").update(payload).eq("id", viral_video_id).execute()
    log.info(f"[{viral_video_id[:8]}] status → {status}")


def _get_agent_prompt(sb: Client, agent_name: str) -> str:
    row = (
        sb.table("yt_agent_prompts")
        .select("prompt_content")
        .eq("agent_name", agent_name)
        .eq("is_active", True)
        .single()
        .execute()
    )
    return row.data["prompt_content"]


class Pipeline:
    def __init__(self) -> None:
        self.sb = _sb()
        self.claude = Anthropic(api_key=ANTHROPIC_API_KEY)

    # ── Public entry point ────────────────────────────────────────────────
    def process_next(self) -> None:
        """Pick one queued+suitable video and advance it one stage."""
        rows = (
            self.sb.table("yt_viral_videos")
            .select("*")
            .eq("status", "queued")
            .eq("suitable", True)
            .limit(1)
            .execute()
        )
        if not rows.data:
            return
        video = rows.data[0]
        vid_id = video["id"]
        yt_id = video["video_id"]
        title = video.get("title", "")
        log.info(f"Processing: [{vid_id[:8]}] {title}")
        try:
            self._run_full_pipeline(vid_id, yt_id, title, video)
        except Exception as exc:
            log.exception(f"[{vid_id[:8]}] Pipeline error: {exc}")
            _set_status(self.sb, vid_id, "queued", f"Error: {exc}")

    # ── Full pipeline ─────────────────────────────────────────────────────
    def _run_full_pipeline(
        self,
        vid_id: str,
        yt_id: str,
        title: str,
        video: dict,
    ) -> None:
        _set_status(self.sb, vid_id, "production_started")

        # Stage 1 — transcript
        transcript = self._ensure_transcript(vid_id, yt_id)
        if not transcript:
            raise RuntimeError("Transcript unavailable")

        # Stage 2 — comments
        comments = self._get_comments(vid_id)

        # Stage 3 — agents
        script_sentences = self._ensure_scripts(vid_id, title, transcript, comments)
        if not script_sentences:
            raise RuntimeError("Script generation failed")

        # Stage 4 — TTS
        self._ensure_audio(vid_id, yt_id, script_sentences)

        # Stage 5 — image prompts + image generation
        self._ensure_images(vid_id, yt_id, script_sentences)

        # Stage 6 — render + upload (CF handles both)
        self._render_and_upload(vid_id, yt_id, title)

        _set_status(self.sb, vid_id, "done")
        log.info(f"[{vid_id[:8]}] ✅ Pipeline complete")

    # ── Stage 1: Transcript ───────────────────────────────────────────────
    def _ensure_transcript(self, vid_id: str, yt_id: str) -> str | None:
        existing = (
            self.sb.table("yt_video_transcripts")
            .select("content")
            .eq("viral_video_id", vid_id)
            .eq("type", "source")
            .limit(1)
            .execute()
        )
        if existing.data:
            log.info(f"[{vid_id[:8]}] Transcript cached")
            return existing.data[0]["content"]

        log.info(f"[{vid_id[:8]}] Fetching transcript via Supadata")
        try:
            resp = requests.get(
                "https://api.supadata.ai/v1/youtube/transcript",
                params={"videoId": yt_id, "text": True},
                headers={"x-api-key": SUPADATA_API_KEY},
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json().get("content", "")
            if content:
                self.sb.table("yt_video_transcripts").insert({
                    "viral_video_id": vid_id,
                    "type": "source",
                    "provider": "supadata",
                    "content": content,
                }).execute()
                return content
        except Exception as exc:
            log.warning(f"[{vid_id[:8]}] Supadata failed: {exc}")

        # Fallback — Gemini
        log.info(f"[{vid_id[:8]}] Transcript fallback via Gemini")
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            result = model.generate_content(
                f"Extract the full spoken transcript from this YouTube video: https://www.youtube.com/watch?v={yt_id}. Return only the spoken words."
            )
            content = result.text
            if content:
                self.sb.table("yt_video_transcripts").insert({
                    "viral_video_id": vid_id,
                    "type": "source",
                    "provider": "gemini",
                    "content": content,
                }).execute()
                return content
        except Exception as exc:
            log.warning(f"[{vid_id[:8]}] Gemini transcript failed: {exc}")
        return None

    # ── Stage 2: Comments ─────────────────────────────────────────────────
    def _get_comments(self, vid_id: str) -> list[dict]:
        rows = (
            self.sb.table("yt_comments")
            .select("text,likes")
            .eq("viral_video_id", vid_id)
            .order("likes", desc=True)
            .limit(50)
            .execute()
        )
        return rows.data or []

    # ── Stage 3: Agents ───────────────────────────────────────────────────
    def _ensure_scripts(
        self,
        vid_id: str,
        title: str,
        transcript: str,
        comments: list[dict],
    ) -> list[dict]:
        existing = (
            self.sb.table("yt_scripts")
            .select("sentence_number,sentence_text")
            .eq("viral_video_id", vid_id)
            .order("sentence_number")
            .execute()
        )
        if existing.data and len(existing.data) >= 150:
            log.info(f"[{vid_id[:8]}] Scripts cached ({len(existing.data)} sentences)")
            return existing.data

        log.info(f"[{vid_id[:8]}] Running 4-agent pipeline")
        prompt3 = _get_agent_prompt(self.sb, "agent3_script_writer")
        image_prompt_template = _get_agent_prompt(self.sb, "image_generator")

        user_content = (
            f"Video title: {title}\n\n"
            f"Transcript:\n{transcript[:8000]}\n\n"
            f"Top comments:\n"
            + "\n".join(f"- {c['text']}" for c in comments[:20])
        )

        log.info(f"[{vid_id[:8]}] Agent 3: writing script")
        resp = self.claude.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[
                {"role": "user", "content": f"{prompt3}\n\n{user_content}"}
            ],
        )

        import json, re
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            sentences: list[dict] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Script JSON parse failed: {exc}\nRaw: {raw[:500]}")

        if len(sentences) < 50:
            raise RuntimeError(f"Script too short: {len(sentences)} sentences")

        rows_to_insert = [
            {
                "viral_video_id": vid_id,
                "sentence_number": s["sentence_number"],
                "sentence_text": s["sentence_text"],
            }
            for s in sentences
        ]
        self.sb.table("yt_scripts").delete().eq("viral_video_id", vid_id).execute()
        self.sb.table("yt_scripts").insert(rows_to_insert).execute()
        log.info(f"[{vid_id[:8]}] {len(sentences)} sentences saved")

        self._save_image_jobs(vid_id, sentences, image_prompt_template)
        return sentences

    def _save_image_jobs(
        self,
        vid_id: str,
        sentences: list[dict],
        template: str,
    ) -> None:
        self.sb.table("yt_image_generation_jobs").delete().eq("viral_video_id", vid_id).execute()
        jobs = []
        for s in sentences:
            prompt = template.replace(
                "{{scene_description}}", s["sentence_text"]
            )
            jobs.append({
                "viral_video_id": vid_id,
                "sentence_number": s["sentence_number"],
                "formatted_prompt": prompt,
            })
        self.sb.table("yt_image_generation_jobs").insert(jobs).execute()
        log.info(f"[{vid_id[:8]}] {len(jobs)} image jobs saved")

    # ── Stage 4: TTS ─────────────────────────────────────────────────────
    def _ensure_audio(
        self,
        vid_id: str,
        yt_id: str,
        sentences: list[dict],
    ) -> None:
        existing = (
            self.sb.table("yt_audio_files")
            .select("start_sentence_number")
            .eq("viral_video_id", vid_id)
            .execute()
        )
        done_chunks = {r["start_sentence_number"] for r in (existing.data or [])}

        CHUNK_SIZE = 5
        chunks = [sentences[i:i+CHUNK_SIZE] for i in range(0, len(sentences), CHUNK_SIZE)]
        bucket_name = f"yt-{yt_id.lower()}"

        from google.cloud import storage as gcs, texttospeech_v1 as tts
        storage_client = gcs.Client()
        tts_client = tts.TextToSpeechClient()

        try:
            storage_client.create_bucket(bucket_name, location="us-central1")
            log.info(f"[{vid_id[:8]}] Created bucket {bucket_name}")
        except Exception:
            pass

        for chunk in chunks:
            start = chunk[0]["sentence_number"]
            if start in done_chunks:
                continue

            text = " ".join(s["sentence_text"] for s in chunk)
            log.info(f"[{vid_id[:8]}] TTS chunk starting at sentence {start}")

            try:
                synthesis_input = tts.SynthesisInput(text=text)
                voice = tts.VoiceSelectionParams(
                    language_code="en-US",
                    name="en-US-Journey-D",
                )
                audio_config = tts.AudioConfig(
                    audio_encoding=tts.AudioEncoding.MP3,
                    speaking_rate=1.0,
                )
                response = tts_client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config,
                )

                file_path = f"audio/chunk_{start:04d}.mp3"
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(file_path)
                blob.upload_from_string(response.audio_content, content_type="audio/mpeg")

                file_url = f"gs://{bucket_name}/{file_path}"
                self.sb.table("yt_audio_files").insert({
                    "viral_video_id": vid_id,
                    "batch_number": start // CHUNK_SIZE,
                    "file_url": file_url,
                    "file_path": file_path,
                    "start_sentence_number": start,
                    "end_sentence_number": chunk[-1]["sentence_number"],
                    "chunk_size": CHUNK_SIZE,
                    "sentence_count": len(chunk),
                }).execute()

            except Exception as exc:
                log.error(f"[{vid_id[:8]}] TTS chunk {start} failed: {exc}")
                raise

        log.info(f"[{vid_id[:8]}] TTS complete")

    # ── Stage 5: Images ───────────────────────────────────────────────────
    def _ensure_images(
        self,
        vid_id: str,
        yt_id: str,
        sentences: list[dict],
    ) -> None:
        jobs_rows = (
            self.sb.table("yt_image_generation_jobs")
            .select("sentence_number,formatted_prompt")
            .eq("viral_video_id", vid_id)
            .order("sentence_number")
            .execute()
        )
        if not jobs_rows.data:
            raise RuntimeError("No image jobs found")

        seen: set[int] = set()
        unique_jobs = []
        for row in jobs_rows.data:
            k = row["sentence_number"]
            if k not in seen:
                seen.add(k)
                unique_jobs.append({
                    "sentence_number": row["sentence_number"],
                    "formatted_prompt": row["formatted_prompt"],
                })

        bucket_name = f"yt-{yt_id.lower()}"
        log.info(f"[{vid_id[:8]}] Calling image CF with {len(unique_jobs)} jobs")

        resp = requests.post(
            IMAGE_CF_URL,
            json={
                "viral_video_id": vid_id,
                "video_id": yt_id,
                "project_id": GCP_PROJECT_ID,
                "location": GCP_LOCATION,
                "input_bucket": bucket_name,
                "output_bucket": f"gs://{bucket_name}/images",
                "image_jobs": unique_jobs,
            },
            timeout=3600,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Image CF failed: {resp.status_code} {resp.text[:300]}")

        result = resp.json()
        log.info(
            f"[{vid_id[:8]}] Images: "
            f"success={result.get('success_count')} "
            f"skipped={result.get('skipped_count')} "
            f"failed={result.get('failure_count')}"
        )

        if not result.get("success") and result.get("success_count", 0) == 0 and result.get("skipped_count", 0) == 0:
            raise RuntimeError(f"Image generation produced no images: {result}")

    # ── Stage 6: Render + Upload ──────────────────────────────────────────
    def _render_and_upload(
        self,
        vid_id: str,
        yt_id: str,
        title: str,
    ) -> None:
        description = (
            f"The psychology behind everything you feel.\n\n"
            f"Subscribe to MindSeam: https://www.youtube.com/@MindSeam\n\n"
            f"Educational purposes only. Not a substitute for professional mental health advice.\n\n"
            f"#psychology #psychologyfacts #mindseam #humanbehavior #selfimprovement"
        )

        log.info(f"[{vid_id[:8]}] Calling generate-video CF")
        resp = requests.post(
            GENERATE_VIDEO_CF_URL,
            json={
                "viral_video_id": vid_id,
                "video_id": yt_id,
                "title": title,
                "description": description,
                "auto_upload": True,
            },
            timeout=3600,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Render CF failed: {resp.status_code} {resp.text[:300]}")

        result = resp.json()
        if not result.get("success"):
            raise RuntimeError(f"Render failed: {result}")

        yt_upload = result.get("youtube_upload", {})
        yt_video_id = yt_upload.get("video_id", "")
        yt_url = yt_upload.get("video_url", "")

        self.sb.table("yt_results").upsert({
            "viral_video_id": vid_id,
            "video_id": yt_id,
            "youtube_video_id": yt_video_id,
            "youtube_url": yt_url,
            "gcs_video_url": result.get("gcs_uri", ""),
            "status": "uploaded",
        }).execute()

        log.info(f"[{vid_id[:8]}] Rendered + uploaded: {yt_url}")
