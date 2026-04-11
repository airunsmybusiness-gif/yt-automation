"""
cloud_functions/generate-video/main.py
xfade crossfade transitions between slides. No background music.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

import functions_framework
import imageio_ffmpeg
from flask import jsonify
from google.cloud import storage
from supabase import create_client

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]
XFADE_DURATION: float = 0.5
CHUNK_FPS: int = 30
VIDEO_W: int = 1280
VIDEO_H: int = 720


def _get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _get_image_b64(item: dict) -> str | None:
    try:
        parts = item["response"]["candidates"][0]["content"]["parts"]
        for p in parts:
            data = (p.get("inlineData") or p.get("inline_data") or {}).get("data")
            if data:
                return data
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _dedupe_audio(records: list[dict]) -> list[dict]:
    seen: dict[int, dict] = {}
    for r in records:
        n = r["start_sentence_number"]
        if n not in seen or r["id"] > seen[n]["id"]:
            seen[n] = r
    return sorted(seen.values(), key=lambda x: x["start_sentence_number"])


def _probe_duration(ffmpeg_path: str, path: str) -> float:
    result = subprocess.run(
        [ffmpeg_path, "-i", path, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            parts = line.strip().split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 5.0


def _render_chunk(ffmpeg_path: str, img_path: str, aud_path: str,
                  out_path: str, audio_duration: float) -> bool:
    cmd = [
        ffmpeg_path, "-y",
        "-loop", "1", "-framerate", str(CHUNK_FPS), "-i", img_path,
        "-i", aud_path,
        "-vf", (f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                f"crop={VIDEO_W}:{VIDEO_H},format=yuv420p"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-r", str(CHUNK_FPS),
        "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
        "-t", str(audio_duration + XFADE_DURATION),
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        logger.error("Chunk render failed: %s", result.stderr[-300:])
        return False
    return True


def _merge_with_xfade(ffmpeg_path: str, chunk_paths: list[str],
                      durations: list[float], output_path: str) -> bool:
    n = len(chunk_paths)
    logger.info("Merging %d chunks with xfade", n)

    if n == 1:
        shutil.copy(chunk_paths[0], output_path)
        return True

    xd = XFADE_DURATION
    offsets: list[float] = []
    cumulative = 0.0
    for dur in durations[:-1]:
        cumulative += dur - xd
        offsets.append(round(cumulative, 4))

    parts: list[str] = []
    prev_v = "[0:v]"
    for i, offset in enumerate(offsets):
        next_v = f"[v{i}]" if i < len(offsets) - 1 else "[vout]"
        parts.append(f"{prev_v}[{i+1}:v]xfade=transition=fade:duration={xd}:offset={offset}{next_v}")
        prev_v = next_v

    audio_inputs = "".join(f"[{i}:a]" for i in range(n))
    parts.append(f"{audio_inputs}concat=n={n}:v=0:a=1[aout]")
    filter_str = ";".join(parts)

    inputs: list[str] = []
    for p in chunk_paths:
        inputs += ["-i", p]

    cmd = [
        ffmpeg_path, "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-r", str(CHUNK_FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        logger.error("xfade merge failed:\n%s", result.stderr[-500:])
        return False
    logger.info("Merge complete → %s", output_path)
    return True


@functions_framework.http
def generate_video(request):
    start_time = time.time()
    logger.info("STARTING VIDEO GENERATION (xfade)")

    sb = _get_supabase()
    data = request.get_json(silent=True) or {}
    video_id: str | None = data.get("viral_video_id")

    if not video_id:
        return jsonify({"error": "viral_video_id is required"}), 400

    tmpdir = tempfile.mkdtemp()
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    logger.info("FFmpeg: %s", ffmpeg_path)

    try:
        storage_client = storage.Client()
        bucket_name = f"yt-{data.get("video_id", video_id).lower()}"
        bucket = storage_client.bucket(bucket_name)

        jsonl_blobs = [
            b for b in storage_client.list_blobs(bucket_name, prefix="images/")
            if b.name.endswith(".jsonl") and "prediction" in b.name
        ]
        if not jsonl_blobs:
            return jsonify({"error": "No prediction JSONL files found"}), 404

        image_map: dict[int, str] = {}
        for idx, blob in enumerate(jsonl_blobs):
            local_path = os.path.join(tmpdir, f"shard_{idx}.jsonl")
            blob.download_to_filename(local_path)
            with open(local_path) as f:
                for line in f:
                    item = json.loads(line)
                    key = item.get("key")
                    b64 = _get_image_b64(item)
                    if key is not None and b64:
                        try:
                            image_map[int(key)] = b64
                        except (ValueError, TypeError):
                            logger.warning("Non-integer image key: %s", key)
            os.remove(local_path)

        if not image_map:
            return jsonify({"error": "No valid images found"}), 500

        logger.info("Loaded %d images", len(image_map))

        audio_rows = _dedupe_audio(
            sb.table("yt_audio_files").select("*")
            .eq("viral_video_id", video_id)
            .order("start_sentence_number")
            .execute().data or []
        )
        if not audio_rows:
            return jsonify({"error": "No audio files found"}), 404

        logger.info("Audio chunks: %d", len(audio_rows))

        available_keys = sorted(image_map.keys())
        sentence_numbers = [r["start_sentence_number"] for r in audio_rows]
        offset = min(available_keys) - min(sentence_numbers)
        logger.info("Image key offset: %d", offset)

        missing = sorted(set(s + offset for s in sentence_numbers) - set(available_keys))
        if missing:
            return jsonify({"error": f"Missing {len(missing)} images", "missing": missing[:20]}), 400

        chunk_paths: list[str] = []
        chunk_durations: list[float] = []

        for idx, rec in enumerate(audio_rows):
            sentence_num: int = rec["start_sentence_number"]
            aud_path = os.path.join(tmpdir, f"a_{idx}.wav")

            try:
                bucket.blob(rec["file_path"]).download_to_filename(aud_path)
            except Exception as exc:
                logger.error("Audio download failed idx=%d: %s", idx, exc)
                continue

            image_key = sentence_num + offset
            img_path = os.path.join(tmpdir, f"i_{idx}.png")
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(image_map[image_key]))

            duration = _probe_duration(ffmpeg_path, aud_path)
            chunk_out = os.path.join(tmpdir, f"c_{idx:04d}.mp4")

            if _render_chunk(ffmpeg_path, img_path, aud_path, chunk_out, duration):
                chunk_paths.append(chunk_out)
                chunk_durations.append(duration)
                logger.info("Chunk %d/%d done (%.1fs)", idx + 1, len(audio_rows), duration)
            else:
                logger.error("Chunk %d failed — skipping", idx)

            for p in (img_path, aud_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

        if not chunk_paths:
            return jsonify({"error": "No chunks rendered"}), 500

        merged_path = os.path.join(tmpdir, "final.mp4")
        if not _merge_with_xfade(ffmpeg_path, chunk_paths, chunk_durations, merged_path):
            return jsonify({"error": "xfade merge failed"}), 500

        dest_blob_name = f"final_videos/{video_id}.mp4"
        bucket.blob(dest_blob_name).upload_from_filename(merged_path)
        logger.info("Uploaded → gs://%s/%s", bucket_name, dest_blob_name)

        return jsonify({
            "success": True,
            "gcs_uri": f"gs://{bucket_name}/{dest_blob_name}",
            "chunks_processed": len(chunk_paths),
            "total_chunks": len(audio_rows),
            "processing_time_seconds": round(time.time() - start_time, 2),
        }), 200

    except Exception as exc:
        import traceback
        logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": str(exc)}), 500

    finally:
        try:
            shutil.rmtree(tmpdir)
        except OSError:
            pass
