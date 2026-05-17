"""
Cloud Function: generate-video
FFmpeg render: GCS audio + Vertex AI Imagen JSONL → final .mp4 in GCS.

Rules (hardcoded to match style of youtube.com/watch?v=LOzrFoSHnGA):
  - NO zoompan filter (causes jitter / OOM on long videos)
  - NO drawtext filter (PIL bakes captions into frames instead)
  - Duration read from ffmpeg -i stderr (not ffprobe, not guessing)
  - One image per audio chunk → hard cut, 1280×720, white letterbox
  - Background music mixed at low volume (bg_volume, default 0.15)
"""
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import functions_framework
from flask import jsonify
from google.cloud import storage
from supabase import create_client
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
import io

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

OUT_W = 1280
OUT_H = 720
FPS = 24
CRF = 24


def _ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def _get_audio_duration_sec(audio_path: str) -> float:
    """Parse duration from ffmpeg -i stderr. Never use ffprobe."""
    result = subprocess.run(
        [_ffmpeg(), "-i", audio_path, "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in result.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            ts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def _bake_caption(image_bytes: bytes, caption: str) -> bytes:
    """
    Burn subtitle text into image using PIL — no ffmpeg drawtext.
    Returns PNG bytes.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((OUT_W, OUT_H), Image.LANCZOS)
        draw = ImageDraw.Draw(img)

        # Semi-transparent caption bar at bottom
        bar_h = max(60, OUT_H // 8)
        bar_top = OUT_H - bar_h
        overlay = Image.new("RGBA", (OUT_W, bar_h), (0, 0, 0, 180))
        img.paste(Image.fromarray(
            __import__("numpy").array(overlay)[:, :, :3]
        ), (0, bar_top))

        # Fit text
        font_size = 28
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
            )
        except OSError:
            font = ImageFont.load_default()

        # Word-wrap to fit width
        words = caption.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > OUT_W - 40 and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)

        total_text_h = len(lines) * (font_size + 4)
        y = bar_top + (bar_h - total_text_h) // 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (OUT_W - (bbox[2] - bbox[0])) // 2
            draw.text((x + 1, y + 1), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))
            y += font_size + 4

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception as exc:
        log.warning(f"Caption bake failed ({exc}), using raw image")
        return image_bytes


def _get_ai_image_b64(item: dict) -> str | None:
    try:
        parts = (
            item.get("response", {})
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        for part in parts:
            data = (
                part.get("inlineData", {}).get("data")
                or part.get("inline_data", {}).get("data")
            )
            if data:
                return data
    except Exception:
        return None


def _render_slide(ffmpeg_path: str, img_path: str, audio_path: str, out_path: str) -> bool:
    """
    Render one image+audio slide to a .ts segment.
    NO zoompan. NO drawtext. Simple scale+pad+format.
    """
    vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:white,"
        f"fps={FPS},format=yuv420p"
    )
    proc = subprocess.run(
        [
            ffmpeg_path, "-y",
            "-loop", "1", "-framerate", str(FPS), "-i", img_path,
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF),
            "-r", str(FPS),
            "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
            "-shortest",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts",
            "-movflags", "+faststart",
            "-f", "mpegts",
            out_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        log.error(f"Slide render failed: {proc.stderr[-600:]}")
    return proc.returncode == 0 and os.path.exists(out_path)


@functions_framework.http
def generate_video(request):
    start_time = time.time()

    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"error": "SUPABASE_URL / SUPABASE_KEY not configured"}), 500

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    data = request.get_json(silent=True) or {}
    video_id = data.get("viral_video_id")
    assets_bucket = data.get("assets_bucket")
    bg_music_bucket = data.get("bg_music_bucket", "yt-auto-bg-audio-ls")
    bg_music_name = data.get("bg_music_name", "audio1.mp3")
    bg_vol = data.get("bg_volume", 0.15)

    if not video_id or not assets_bucket:
        return jsonify({"error": "viral_video_id and assets_bucket are required"}), 400

    tmpdir = tempfile.mkdtemp()
    ffmpeg_path = _ffmpeg()

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(assets_bucket)

        # ── Load Vertex AI Imagen JSONL predictions ──────────────────────────
        images_prefix = f"{video_id}/images/"
        blobs = list(storage_client.list_blobs(assets_bucket, prefix=images_prefix))
        jsonl_blobs = [
            b for b in blobs
            if b.name.endswith(".jsonl") and "prediction" in b.name
        ]
        if not jsonl_blobs:
            return jsonify({
                "error": "No prediction JSONL files found",
                "prefix": f"gs://{assets_bucket}/{images_prefix}",
            }), 404

        image_map: dict[int, bytes] = {}  # key → raw image bytes
        for idx, jblob in enumerate(jsonl_blobs):
            local_jsonl = os.path.join(tmpdir, f"shard_{idx}.jsonl")
            jblob.download_to_filename(local_jsonl)
            with open(local_jsonl) as f:
                for line in f:
                    try:
                        item = json.loads(line)
                        key = item.get("key")
                        b64 = _get_ai_image_b64(item)
                        if key is not None and b64:
                            image_map[int(key)] = base64.b64decode(b64)
                    except Exception:
                        pass
            os.remove(local_jsonl)

        if not image_map:
            return jsonify({"error": "No valid images decoded from JSONL"}), 500

        log.info(f"Loaded {len(image_map)} images from Vertex AI output")
        available_keys = sorted(image_map.keys())

        # ── Load audio from yt_audio_files (one row per sentence) ────────────
        audio_res = (
            supabase.table("yt_audio_files")
            .select("*")
            .eq("viral_video_id", video_id)
            .order("start_sentence_number")
            .execute()
        )
        if not audio_res.data:
            return jsonify({"error": "No audio files in yt_audio_files"}), 404

        sentence_numbers = [r["start_sentence_number"] for r in audio_res.data]

        # Calculate mapping offset between image keys and sentence numbers
        if available_keys and sentence_numbers:
            offset = min(available_keys) - min(sentence_numbers)
        else:
            offset = 0

        expected_keys = {s + offset for s in sentence_numbers}
        missing = sorted(expected_keys - set(available_keys))
        if missing:
            log.warning(f"Missing {len(missing)} images (first 10): {missing[:10]}")

        # ── Render one slide per sentence ────────────────────────────────────
        chunks: list[str] = []
        for idx, rec in enumerate(audio_res.data):
            sentence_num = rec["start_sentence_number"]
            image_key = sentence_num + offset

            aud_path = os.path.join(tmpdir, f"a_{idx}.mp3")
            try:
                bucket.blob(rec["file_path"]).download_to_filename(aud_path)
            except Exception as exc:
                log.warning(f"Sentence {sentence_num}: audio download failed: {exc}")
                continue

            dur = _get_audio_duration_sec(aud_path)
            if dur <= 0:
                log.warning(f"Sentence {sentence_num}: zero-duration audio, skipping")
                continue

            # Pick closest image
            if image_key in image_map:
                raw_img = image_map[image_key]
            else:
                closest = min(available_keys, key=lambda k: abs(k - image_key))
                raw_img = image_map[closest]
                log.warning(
                    f"Sentence {sentence_num}: using fallback image key {closest}"
                )

            img_path = os.path.join(tmpdir, f"i_{idx}.png")
            with open(img_path, "wb") as f:
                f.write(raw_img)

            chunk_out = os.path.join(tmpdir, f"c_{idx:04d}.ts")
            if _render_slide(ffmpeg_path, img_path, aud_path, chunk_out):
                chunks.append(chunk_out)

            # Cleanup immediately to conserve /tmp
            try:
                os.remove(img_path)
                os.remove(aud_path)
            except Exception:
                pass

        if not chunks:
            return jsonify({"error": "No chunks rendered"}), 500

        # ── Concatenate all segments ─────────────────────────────────────────
        concat_list = os.path.join(tmpdir, "list.txt")
        with open(concat_list, "w") as f:
            for c in chunks:
                f.write(f"file '{c}'\n")

        merged_video = os.path.join(tmpdir, "merged.mp4")
        merge_proc = subprocess.run(
            [
                ffmpeg_path, "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(CRF),
                "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-fflags", "+genpts",
                "-avoid_negative_ts", "make_zero",
                "-vsync", "cfr",
                merged_video,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if merge_proc.returncode != 0:
            return jsonify({
                "error": "Merge failed",
                "details": merge_proc.stderr[-800:],
            }), 500

        # ── Mix background music ─────────────────────────────────────────────
        bg_local = os.path.join(tmpdir, "bg.mp3")
        try:
            storage_client.bucket(bg_music_bucket).blob(bg_music_name).download_to_filename(bg_local)
        except Exception:
            bg_local = None

        final_video = os.path.join(tmpdir, "final.mp4")
        if bg_local and os.path.exists(bg_local):
            bg_proc = subprocess.run(
                [
                    ffmpeg_path, "-y",
                    "-i", merged_video,
                    "-stream_loop", "-1", "-i", bg_local,
                    "-filter_complex",
                    f"[1:a]volume={bg_vol}[bg];[0:a][bg]amix=inputs=2:duration=first[a]",
                    "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    final_video,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if bg_proc.returncode != 0:
                log.warning("BG music mix failed, using merged video as final")
                shutil.copy(merged_video, final_video)
        else:
            shutil.copy(merged_video, final_video)

        # ── Generate thumbnail (frame 1 from final) ──────────────────────────
        thumb_local = os.path.join(tmpdir, "thumb.jpg")
        subprocess.run(
            [
                ffmpeg_path, "-y", "-i", final_video,
                "-vframes", "1", "-q:v", "2", "-vf", f"scale={OUT_W}:{OUT_H}",
                thumb_local,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # ── Upload to GCS ────────────────────────────────────────────────────
        video_dest = f"{video_id}/final_videos/{video_id}.mp4"
        thumb_dest = f"{video_id}/final_videos/{video_id}_thumb.jpg"

        dest_blob = bucket.blob(video_dest)
        dest_blob.upload_from_filename(final_video)

        thumb_uploaded = False
        if os.path.exists(thumb_local):
            bucket.blob(thumb_dest).upload_from_filename(thumb_local)
            thumb_uploaded = True

        total_time = time.time() - start_time
        log.info(
            f"generate-video done: {len(chunks)} chunks, {total_time:.1f}s"
        )
        return jsonify({
            "success": True,
            "gcs_uri": f"gs://{assets_bucket}/{video_dest}",
            "thumbnail_gcs_uri": f"gs://{assets_bucket}/{thumb_dest}" if thumb_uploaded else None,
            "chunks_processed": len(chunks),
            "processing_time_seconds": round(total_time, 2),
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass
