"""
MindSeam pipeline — Railway-only edition.
No GCP. No Cloud Functions. No GCS. All execution on Railway disk.

Flow per video:
  1. Pick queued video from yt_viral_videos
  2. Fetch transcript + top comments
  3. Claude writes script -> yt_scripts
  4. Edge TTS generates audio chunks -> /tmp disk
  5. Cloudflare Flux generates images -> /tmp disk
  6. FFmpeg renders video -> /tmp disk
  7. YouTube upload -> private
  8. Mark video as done, cleanup /tmp
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from execution.gemini_text import GeminiMessageShim
from supabase import Client, create_client

from execution import imagen_images, openai_tts, video_render, youtube_upload

log = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")

CHUNK_SIZE: int = 8
MAX_SENTENCES: int = 180


class Pipeline:
    def __init__(self) -> None:
        self.sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.ai = GeminiMessageShim()

    def _check_cloudflare_quota(self) -> bool:
        import requests, os
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        if not token or not account:
            return True
        try:
            r = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"prompt": "x"},
                timeout=10,
            )
            if r.status_code == 429:
                log.warning("Cloudflare quota exceeded, skipping pipeline run")
                return False
            return True
        except Exception:
            return True

    def _uploaded_within_24h(self) -> bool:
        """Hard cap: skip processing if any video uploaded in the last 24h."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        try:
            res = (
                self.sb.table("yt_viral_videos")
                .select("id,production_completed_at")
                .eq("status", "done")
                .gte("production_completed_at", cutoff)
                .limit(1)
                .execute()
            )
            if res.data:
                log.info("24h upload cap: video uploaded recently, skipping run")
                return True
            return False
        except Exception as exc:
            log.warning(f"24h cap check failed (proceeding anyway): {exc}")
            return False

    def process_next(self) -> None:
        if not self._check_cloudflare_quota():
            return
        if self._uploaded_within_24h():
            return
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
        vid_id = video["id"]
        yt_id = video["video_id"]
        title = video["title"]
        log.info(f"[{vid_id[:8]}] Processing '{title[:60]}'")

        work_dir = Path(tempfile.mkdtemp(prefix=f"mindseam_{vid_id[:8]}_"))
        try:
            self._mark_status(vid_id, "processing")
            transcript = self._ensure_transcript(vid_id, yt_id)
            comments = self._fetch_comments(vid_id)
            sentences = self._ensure_scripts(vid_id, title, transcript, comments)
            if not sentences:
                raise RuntimeError("Script generation produced no sentences")

            audio_chunks = self._generate_audio(vid_id, sentences, work_dir / "audio")
            self._generate_images(vid_id, sentences, work_dir / "images")
            final_video = work_dir / f"{yt_id}.mp4"
            # Generate metadata FIRST so thumbnail uses optimized title
            metadata = self._generate_metadata(title, transcript, sentences)
            render_result = video_render.render_video(
                audio_chunks=audio_chunks,
                images_dir=work_dir / "images",
                work_dir=work_dir / "render",
                output_path=final_video,
                title=metadata["title"],
            )
            thumb_path = (
                Path(render_result["thumbnail_path"])
                if render_result.get("thumbnail_path")
                else None
            )
            upload_result = youtube_upload.upload_video(
                video_path=final_video,
                title=metadata["title"],
                description=metadata["description"],
                tags=metadata["tags"],
                thumbnail_path=thumb_path,
                privacy_status="private",
                category_id="27",
            )
            try:
                self.sb.table("yt_results").insert({
                    "video_id": upload_result["video_id"],
                    "gcs_video_url": upload_result["url"],
                    "thumbnail_link": (
                        upload_result["url"] if upload_result["thumbnail_uploaded"] else None
                    ),
                }).execute()
            except Exception as db_exc:
                log.warning(f"[{vid_id[:8]}] yt_results insert skipped: {db_exc}")
            self._mark_status(vid_id, "done", notes=f"Uploaded: {upload_result['url']}")
            log.info(f"[{vid_id[:8]}] DONE -> {upload_result['url']}")
        except Exception as exc:
            log.exception(f"[{vid_id[:8]}] FAILED: {exc}")
            self._mark_status(vid_id, "failed", notes=f"Error: {exc}")
        finally:
            try:
                shutil.rmtree(work_dir)
            except Exception as cleanup_exc:
                log.warning(f"Cleanup failed: {cleanup_exc}")

    def _mark_status(self, vid_id: str, status: str, notes: str | None = None) -> None:
        patch: dict[str, Any] = {"status": status}
        if notes is not None:
            patch["production_notes"] = notes
        self.sb.table("yt_viral_videos").update(patch).eq("id", vid_id).execute()
        log.info(f"[{vid_id[:8]}] status -> {status}")

    def _ensure_transcript(self, vid_id: str, yt_id: str) -> str:
        existing = (
            self.sb.table("yt_video_transcripts")
            .select("content")
            .eq("video_record_id", vid_id)
            .execute()
        )
        if existing.data:
            log.info(f"[{vid_id[:8]}] Transcript cached")
            return existing.data[0]["content"]

        log.info(f"[{vid_id[:8]}] Fetching transcript for {yt_id}")
        from execution.transcript_fetch import fetch_transcript
        row = (
            self.sb.table("yt_viral_videos")
            .select("description")
            .eq("id", vid_id)
            .execute()
        )
        desc = (row.data[0].get("description") if row.data else "") or ""
        content, lang, provider = fetch_transcript(yt_id, fallback_description=desc)
        self.sb.table("yt_video_transcripts").insert({
            "video_record_id": vid_id,
            "video_id": yt_id,
            "content": content,
            "language_code": lang,
            "type": "source",
            "provider": provider,
        }).execute()
        self.sb.table("yt_viral_videos").update({
            "transcript_status": "completed",
        }).eq("id", vid_id).execute()
        log.info(f"[{vid_id[:8]}] Transcript stored ({provider}, {len(content)} chars)")
        return content

    def _fetch_comments(self, vid_id: str) -> list[str]:
        rows = (
            self.sb.table("yt_comments")
            .select("content,like_count")
            .eq("video_record_id", vid_id)
            .order("like_count", desc=True)
            .limit(30)
            .execute()
        )
        if rows.data:
            return [r["content"] for r in rows.data if r.get("content")]

        log.info(f"[{vid_id[:8]}] Fetching comments")
        vid_row = (
            self.sb.table("yt_viral_videos")
            .select("video_id")
            .eq("id", vid_id)
            .execute()
        )
        if not vid_row.data:
            return []
        yt_id = vid_row.data[0]["video_id"]
        api_row = (
            self.sb.table("yt_api_accounts")
            .select("api_key")
            .eq("quota_exhausted", False)
            .limit(1)
            .execute()
        )
        if not api_row.data:
            log.warning(f"[{vid_id[:8]}] No active YT API key for comments")
            return []
        from execution.comments_fetch import fetch_top_comments
        comments = fetch_top_comments(yt_id, api_row.data[0]["api_key"])
        if comments:
            payload = [
                {**c, "video_record_id": vid_id, "video_id": yt_id}
                for c in comments
            ]
            self.sb.table("yt_comments").upsert(payload, on_conflict="comment_id").execute()
            self.sb.table("yt_viral_videos").update({
                "comments_status": "completed",
            }).eq("id", vid_id).execute()
        return [c["content"] for c in comments if c.get("content")]

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
        sentences = []
        for attempt in range(3):
            resp = self.ai.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt_filled}],
            )
            raw = resp.content[0].text
            script_json = self._extract_json(raw)
            # Handle both {"sentences": [...]} and bare [...] from different LLMs
            if isinstance(script_json, list):
                sentences = script_json
            else:
                sentences = script_json.get("sentences") or script_json.get("script") or []
            if not sentences:
                raise RuntimeError(f"Script agent returned no sentences: {raw[:400]}")
            sentences = sentences[:MAX_SENTENCES]
            log.info(f"[{vid_id[:8]}] Script attempt {attempt+1}: {len(sentences)} sentences")
            if len(sentences) >= 165:
                break
            log.warning(f"[{vid_id[:8]}] Only {len(sentences)} sentences, retrying with stronger prompt")
            prompt_filled = prompt_filled + f"\n\nCRITICAL: Your last attempt only produced {len(sentences)} sentences. You MUST write at least 150. Continue expanding every section with more examples, stories, and psychological insights until you reach 150+ sentences."
        log.info(f"[{vid_id[:8]}] Script final: {len(sentences)} sentences (cap {MAX_SENTENCES})")

        image_prompt_template = self._fetch_agent_prompt("image_generator")
        rows_scripts = []
        rows_images = []
        IMAGE_GROUP_SIZE = 3
        sentence_texts = []
        for i, s in enumerate(sentences, start=1):
            text = (s.get("sentence_text") or s.get("text") or str(s)) if isinstance(s, dict) else str(s)
            sentence_texts.append(text)
            rows_scripts.append({
                "viral_video_id": vid_id,
                "sentence_number": i,
                "sentence_text": text,
            })
        # Group 3 sentences per image for smoother pacing
        for group_start in range(0, len(sentence_texts), IMAGE_GROUP_SIZE):
            group = sentence_texts[group_start:group_start + IMAGE_GROUP_SIZE]
            sentence_blob = " ".join(group)
            # Transform script sentences into ONE concrete visual scene via Claude
            scene = self._scene_from_sentences(sentence_blob, image_prompt_template)
            rows_images.append({
                "viral_video_id": vid_id,
                "sentence_number": group_start + 1,
                "formatted_prompt": scene,
            })
        self.sb.table("yt_scripts").insert(rows_scripts).execute()
        self.sb.table("yt_image_generation_jobs").insert(rows_images).execute()
        log.info(f"[{vid_id[:8]}] Saved {len(rows_scripts)} script rows + image jobs")
        return rows_scripts

    def _rebuild_image_jobs_if_needed(self, vid_id: str, sentences: list[dict]) -> None:
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
        # Group sentences (3 per image) and use Claude to transform each group
        IMAGE_GROUP_SIZE = 3
        rows = []
        for group_start in range(0, len(sentences), IMAGE_GROUP_SIZE):
            group = sentences[group_start:group_start + IMAGE_GROUP_SIZE]
            sentence_blob = " ".join(s["sentence_text"] for s in group)
            scene = self._scene_from_sentences(sentence_blob, template)
            rows.append({
                "viral_video_id": vid_id,
                "sentence_number": group[0]["sentence_number"],
                "formatted_prompt": scene,
            })
        self.sb.table("yt_image_generation_jobs").insert(rows).execute()

    def _generate_audio(
        self, vid_id: str, sentences: list[dict], audio_dir: Path
    ) -> list[dict]:
        audio_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[dict] = []
        for start in range(0, len(sentences), CHUNK_SIZE):
            chunk = sentences[start:start + CHUNK_SIZE]
            start_sentence = chunk[0]["sentence_number"]
            out_path = audio_dir / f"chunk_{start_sentence:04d}.mp3"
            log.info(f"[{vid_id[:8]}] TTS chunk {start_sentence}/{len(sentences)}")
            openai_tts.generate_chunk(chunk, out_path)
            chunks.append({
                "start_sentence": start_sentence,
                "num_sentences": len(chunk),
                "local_audio_path": str(out_path),
            })
        log.info(f"[{vid_id[:8]}] Audio complete: {len(chunks)} chunks")
        return chunks

    def _generate_images(
        self, vid_id: str, sentences: list[dict], images_dir: Path
    ) -> None:
        jobs_rows = (
            self.sb.table("yt_image_generation_jobs")
            .select("sentence_number,formatted_prompt")
            .eq("viral_video_id", vid_id)
            .order("sentence_number")
            .execute()
        )
        if not jobs_rows.data:
            raise RuntimeError("No image jobs in database")
        seen: set[int] = set()
        unique: list[dict] = []
        for row in jobs_rows.data:
            k = row["sentence_number"]
            if k not in seen:
                seen.add(k)
                unique.append(row)
        # Cap images at 30 to control cost (~$1.20/video at Nano Banana 1 pricing)
        MAX_IMAGES = 30
        if len(unique) > MAX_IMAGES:
            step = len(unique) / MAX_IMAGES
            unique = [unique[int(i * step)] for i in range(MAX_IMAGES)]
            log.info(f"[{vid_id[:8]}] Capped images: {len(unique)} (evenly sampled)")
        log.info(f"[{vid_id[:8]}] Cloudflare Flux: {len(unique)} images")
        result = imagen_images.generate_batch(unique, images_dir)
        log.info(
            f"[{vid_id[:8]}] Images done: success={result['success']} "
            f"skipped={result.get('skipped', 0)} failed={result['failed']}"
        )
        if result["success"] == 0 and result.get("skipped", 0) == 0:
            raise RuntimeError(f"No images generated: {result}")

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

    def _scene_from_sentences(self, sentence_blob: str, template: str) -> str:
        """Use Claude to turn 1-3 script sentences into a literal stick-figure scene description."""
        prompt = template.replace("{{sentence}}", sentence_blob[:500])
        try:
            resp = self.ai.messages.create(
                model="claude-haiku-4-5",
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            scene = resp.content[0].text.strip()
            # Strip any quotes/preamble Claude might add
            scene = scene.strip('"').strip("'").strip()
            if scene.lower().startswith(("here", "scene:", "output:")):
                scene = scene.split(":", 1)[-1].strip()
            return scene[:500] if scene else f"A stick figure standing alone. Empty cream paper background."
        except Exception as exc:
            log.warning(f"Scene transform failed, using fallback: {exc}")
            return f"A stick figure standing alone, looking thoughtful. Empty cream paper background."

    def _generate_metadata(self, title: str, transcript: str, sentences: list[dict]) -> dict:
        """Call agent2_strategist for SEO-optimized title, description, tags."""
        try:
            prompt_template = self._fetch_agent_prompt("agent2_strategist")
            script_text = " ".join((s.get("sentence_text") or "")[:200] for s in sentences[:30])
            prompt = (
                prompt_template
                .replace("{{title}}", title)
                .replace("{{transcript}}", transcript[:3000])
                .replace("{{script}}", script_text[:3000])
                + "\n\nReturn ONLY valid JSON with keys: title, description, tags (array of 15+ strings)."
            )
            resp = self.ai.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            # Try to repair common JSON issues
            try:
                data = self._extract_json(raw)
            except Exception:
                # Strip trailing commas, fix unescaped quotes
                import re
                cleaned = re.sub(r',\s*([}\]])', r'\1', raw)
                data = self._extract_json(cleaned)
            return {
                "title": (data.get("title") or title)[:100],
                "description": data.get("description") or self._fallback_description(title),
                "tags": data.get("tags") or ["psychology", "mindseam", "humanbehavior", "psychologyfacts", "mentalhealth", "selfimprovement"],
            }
        except Exception as exc:
            log.warning(f"Strategist metadata failed, using fallback: {exc}")
            return {
                "title": title[:100],
                "description": self._fallback_description(title),
                "tags": ["psychology", "mindseam", "humanbehavior", "psychologyfacts", "mentalhealth", "selfimprovement", "neuroscience", "mentalhealthawareness", "selfawareness", "emotionalintelligence", "personalgrowth", "mindset", "psychologytips", "behaviorscience", "humanmind"],
            }

    def _fallback_description(self, title: str) -> str:
        return (
            f"{title}\n\n"
            "Explore the psychology behind everything you feel on MindSeam.\n\n"
            "Subscribe for more psychology insights every week.\n\n"
            "#psychology #psychologyfacts #mindseam #humanbehavior #mentalhealth #selfimprovement\n"
        )

    def _build_description(self, title: str) -> str:
        return self._fallback_description(title)
