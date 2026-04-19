"""
orchestration/pipeline.py
Column names verified against live Supabase schema:
  yt_video_transcripts : video_record_id, video_id, content, type, provider, language_code
  yt_scripts           : viral_video_id, sentence_number, sentence_text
  yt_audio_files       : viral_video_id, batch_number, file_url, file_path, start_sentence_number, end_sentence_number, chunk_size, sentence_count
  yt_image_generation_jobs : viral_video_id, sentence_number, formatted_prompt
  yt_batch_jobs        : viral_video_id, batch_job_name, status, media_type, images_generated, images_failed
  yt_results           : video_id, gcs_video_url
  yt_agent_prompts     : agent_name, prompt_content
"""

import logging
import os
import re
import json
from typing import Any

import requests
from anthropic import Anthropic
from supabase import Client, create_client

log = logging.getLogger("pipeline")

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GCP_PROJECT_ID: str = os.environ["GCP_PROJECT_ID"]
IMAGE_CF_URL: str = os.environ["IMAGE_CF_URL"]
GENERATE_VIDEO_CF_URL: str = os.environ["GENERATE_VIDEO_CF_URL"]
SUPADATA_API_KEY: str = os.environ.get("SUPADATA_API_KEY", "")

MODEL = "claude-sonnet-4-6"
GCP_LOCATION = "us-central1"
CHUNK_SIZE = 5


def _sb() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _set_status(sb: Client, viral_video_id: str, status: str, notes: str | None = None) -> None:
    payload: dict[str, Any] = {"status": status}
    if notes is not None:
        payload["production_notes"] = notes
    sb.table("yt_viral_videos").update(payload).eq("id", viral_video_id).execute()
    log.info(f"[{viral_video_id[:8]}] status -> {status}")


class Pipeline:
    def __init__(self) -> None:
        self.sb = _sb()
        self.claude = Anthropic(api_key=ANTHROPIC_API_KEY)

    def process_next(self) -> None:
        rows = (
            self.sb.table("yt_viral_videos")
            .select("*")
            .eq("status", "queued")
            .eq("suitable", "true")
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
            self._run_full_pipeline(vid_id, yt_id, title)
        except Exception as exc:
            log.exception(f"[{vid_id[:8]}] Pipeline error: {exc}")
            _set_status(self.sb, vid_id, "queued", f"Error: {exc}")

    def _run_full_pipeline(self, vid_id: str, yt_id: str, title: str) -> None:
        _set_status(self.sb, vid_id, "production_started")
        transcript = self._ensure_transcript(vid_id, yt_id)
        if not transcript:
            raise RuntimeError("Transcript unavailable")
        comments = self._get_comments(vid_id)
        script_sentences = self._ensure_scripts(vid_id, title, transcript, comments)
        if not script_sentences:
            raise RuntimeError("Script generation failed")
        self._ensure_audio(vid_id, yt_id, script_sentences)
        self._ensure_images(vid_id, yt_id)
        self._render_and_upload(vid_id, yt_id, title)
        _set_status(self.sb, vid_id, "done")
        log.info(f"[{vid_id[:8]}] Pipeline complete")

    def _ensure_transcript(self, vid_id: str, yt_id: str) -> str | None:
        existing = (
            self.sb.table("yt_video_transcripts")
            .select("content")
            .eq("video_record_id", vid_id)
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
                    "video_record_id": vid_id,
                    "video_id": yt_id,
                    "type": "source",
                    "provider": "supadata",
                    "content": content,
                    "language_code": "en",
                }).execute()
                log.info(f"[{vid_id[:8]}] Transcript saved via Supadata")
                return content
        except Exception as exc:
            log.warning(f"[{vid_id[:8]}] Supadata failed: {exc}")

        log.info(f"[{vid_id[:8]}] Transcript fallback via Gemini")
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            result = model.generate_content(
                f"Summarize the key topics and content of this YouTube video for scriptwriting: "
                f"https://www.youtube.com/watch?v={yt_id}. Return only the content summary."
            )
            content = result.text
            if content:
                self.sb.table("yt_video_transcripts").insert({
                    "video_record_id": vid_id,
                    "video_id": yt_id,
                    "type": "source",
                    "provider": "gemini",
                    "content": content,
                    "language_code": "en",
                }).execute()
                log.info(f"[{vid_id[:8]}] Transcript saved via Gemini")
                return content
        except Exception as exc:
            log.warning(f"[{vid_id[:8]}] Gemini transcript failed: {exc}")
        return None

    def _get_comments(self, vid_id: str) -> list[dict]:
        rows = (
            self.sb.table("yt_comments")
            .select("content,like_count")
            .eq("viral_video_id", vid_id)
            .order("like_count", desc=True)
            .limit(30)
            .execute()
        )
        return rows.data or []

    def _ensure_scripts(self, vid_id: str, title: str, transcript: str, comments: list[dict]) -> list[dict]:
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

        log.info(f"[{vid_id[:8]}] Running script writer agent")
        prompt = (
            self.sb.table("yt_agent_prompts")
            .select("prompt_content")
            .eq("agent_name", "agent3_script_writer")
            .execute()
        ).data[0]["prompt_content"]

        image_prompt_template = (
            self.sb.table("yt_agent_prompts")
            .select("prompt_content")
            .eq("agent_name", "image_generator")
            .execute()
        ).data[0]["prompt_content"]

        user_content = (
            f"Video title: {title}\n\n"
            f"Source transcript:\n{transcript[:6000]}\n\n"
            f"Top comments:\n"
            + "\n".join(f"- {c['content']}" for c in comments[:20])
        )

        resp = self.claude.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": f"{prompt}\n\n{user_content}"}],
        )

        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            sentences: list[dict] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Script JSON parse failed: {exc}\nRaw: {raw[:500]}")

        if len(sentences) < 50:
            raise RuntimeError(f"Script too short: {len(sentences)} sentences")

        self.sb.table("yt_scripts").delete().eq("viral_video_id", vid_id).execute()
        self.sb.table("yt_scripts").insert([
            {
                "viral_video_id": vid_id,
                "sentence_number": s["sentence_number"],
                "sentence_text": s["sentence_text"],
            }
            for s in sentences
        ]).execute()
        log.info(f"[{vid_id[:8]}] {len(sentences)} sentences saved")
        self._save_image_jobs(vid_id, sentences, image_prompt_template)
        return sentences

    def _save_image_jobs(self, vid_id: str, sentences: list[dict], template: str) -> None:
        self.sb.table("yt_image_generation_jobs").delete().eq("viral_video_id", vid_id).execute()
        jobs = [
            {
                "viral_video_id": vid_id,
                "sentence_number": s["sentence_number"],
                "formatted_prompt": template.replace("{{scene_description}}", s["sentence_text"]),
            }
            for s in sentences
        ]
        self.sb.table("yt_image_generation_jobs").insert(jobs).execute()
        log.info(f"[{vid_id[:8]}] {len(jobs)} image jobs saved")

    def _ensure_audio(self, vid_id: str, yt_id: str, sentences: list[dict]) -> None:
        existing = (
            self.sb.table("yt_audio_files")
            .select("start_sentence_number")
            .eq("viral_video_id", vid_id)
            .execute()
        )
        done_chunks = {r["start_sentence_number"] for r in (existing.data or [])}
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

        for idx, chunk in enumerate(chunks):
            start = chunk[0]["sentence_number"]
            if start in done_chunks:
                continue
            text = " ".join(s["sentence_text"] for s in chunk)
            log.info(f"[{vid_id[:8]}] TTS chunk {idx+1}/{len(chunks)} start={start}")
            try:
                response = tts_client.synthesize_speech(
                    input=tts.SynthesisInput(text=text),
                    voice=tts.VoiceSelectionParams(language_code="en-US", name="en-US-Journey-D"),
                    audio_config=tts.AudioConfig(
                        audio_encoding=tts.AudioEncoding.MP3,
                        speaking_rate=1.0,
                    ),
                )
                file_path = f"audio/chunk_{start:04d}.mp3"
                storage_client.bucket(bucket_name).blob(file_path).upload_from_string(
                    response.audio_content, content_type="audio/mpeg"
                )
                self.sb.table("yt_audio_files").insert({
                    "viral_video_id": vid_id,
                    "batch_number": idx,
                    "file_url": f"gs://{bucket_name}/{file_path}",
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

    def _ensure_images(self, vid_id: str, yt_id: str) -> None:
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
                unique_jobs.append({"sentence_number": k, "formatted_prompt": row["formatted_prompt"]})

        bucket_name = f"yt-{yt_id.lower()}"
        log.info(f"[{vid_id[:8]}] Calling image CF: {len(unique_jobs)} jobs")

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
            f"[{vid_id[:8]}] Images: success={result.get('success_count')} "
            f"skipped={result.get('skipped_count')} failed={result.get('failure_count')}"
        )
        if not result.get("success") and result.get("success_count", 0) == 0 and result.get("skipped_count", 0) == 0:
            raise RuntimeError(f"Image generation produced no images: {result}")

        self.sb.table("yt_batch_jobs").insert({
            "viral_video_id": vid_id,
            "batch_job_name": result.get("batch_job_name", ""),
            "status": "completed",
            "media_type": "image",
            "images_generated": result.get("success_count", 0),
            "images_failed": result.get("failure_count", 0),
        }).execute()

    def _render_and_upload(self, vid_id: str, yt_id: str, title: str) -> None:
        description = (
            "The psychology behind everything you feel.\n\n"
            "Subscribe to MindSeam: https://www.youtube.com/@MindSeam\n\n"
            "Educational purposes only. Not a substitute for professional mental health advice.\n\n"
            "#psychology #psychologyfacts #mindseam #humanbehavior #selfimprovement"
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
        log.info(f"[{vid_id[:8]}] Uploaded: {yt_upload.get('video_url', 'unknown')}")

        self.sb.table("yt_results").insert({
            "video_id": yt_id,
            "gcs_video_url": result.get("gcs_uri", ""),
        }).execute()
