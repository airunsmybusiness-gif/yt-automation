"""
cloud_functions/generate-video/main.py  v3
Strategy: one image per SENTENCE (not per audio chunk).
Each audio chunk covers N sentences -> split into N sub-clips.
Smooth zoom 0.0003 speed. Burned captions. Hard cuts.
"""
from __future__ import annotations
import base64, json, logging, os, shutil, subprocess, sys, tempfile, time
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
FPS: int = 30
VIDEO_W: int = 1280
VIDEO_H: int = 720
ZOOM_SPEED: float = 0.0003
MIN_SLIDE_DURATION: float = 1.5
CAPTION_FONT_SIZE: int = 52

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
    result = subprocess.run([ffmpeg_path, "-i", path, "-f", "null", "-"],
                            capture_output=True, text=True, timeout=30)
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            raw = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = raw.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 5.0

def _split_audio_by_proportion(ffmpeg_path, src_path, total_duration,
                                sentence_texts, out_dir, chunk_idx):
    if len(sentence_texts) == 1:
        return [(src_path, total_duration)]
    total_chars = sum(len(t) for t in sentence_texts)
    props = [len(t) / total_chars if total_chars else 1.0 / len(sentence_texts)
             for t in sentence_texts]
    durations = [max(p * total_duration, MIN_SLIDE_DURATION) for p in props]
    dur_sum = sum(durations)
    if dur_sum > total_duration:
        durations = [d * total_duration / dur_sum for d in durations]
    clips, offset = [], 0.0
    for i, dur in enumerate(durations):
        out_path = os.path.join(out_dir, f"sub_{chunk_idx:04d}_{i:03d}.wav")
        cmd = [ffmpeg_path, "-y", "-ss", f"{offset:.4f}", "-t", f"{dur:.4f}",
               "-i", src_path, "-c", "copy", out_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        clips.append((out_path if result.returncode == 0 else src_path, dur))
        offset += dur
    return clips

def _escape_caption(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("'", "\u2019")
                .replace(":", "\\:").replace("%", "\\%")
                .replace("[", "\\[").replace("]", "\\]").replace(",", "\\,"))

def _render_slide(ffmpeg_path, img_path, aud_path, out_path,
                  duration, caption, slide_idx):
    total_frames = max(int(duration * FPS), 1)
    if slide_idx % 2 == 0:
        zoom_expr = f"min(zoom+{ZOOM_SPEED},1.15)"
    else:
        zoom_expr = f"max(zoom-{ZOOM_SPEED},1.0)"
    zoompan = (f"scale={VIDEO_W*2}:{VIDEO_H*2},"
               f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
               f":d={total_frames}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},format=yuv420p")
    safe = _escape_caption(caption[:140])
    drawtext = (f"drawtext=text='{safe}':fontsize={CAPTION_FONT_SIZE}:"
                f"fontcolor=white:bordercolor=black:borderw=3:"
                f"x=(w-text_w)/2:y={VIDEO_H-130}:line_spacing=8:fix_bounds=true")
    cmd = [ffmpeg_path, "-y",
           "-loop", "1", "-framerate", str(FPS), "-i", img_path,
           "-i", aud_path,
           "-vf", zoompan,
           "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-r", str(FPS), "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
           "-t", str(duration), "-avoid_negative_ts", "make_zero",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("Slide %d failed: %s", slide_idx, result.stderr[-400:])
        return False
    return True

def _concat_slides(ffmpeg_path, slide_paths, output_path):
    list_file = output_path + "_list.txt"
    with open(list_file, "w") as f:
        for p in slide_paths:
            f.write(f"file '{p}'\n")
    cmd = [ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
           "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
           "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
           "-vsync", "cfr", "-movflags", "+faststart", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    try:
        os.remove(list_file)
    except OSError:
        pass
    if result.returncode != 0:
        logger.error("Concat failed: %s", result.stderr[-600:])
        return False
    logger.info("Concat complete -> %s", output_path)
    return True

@functions_framework.http
def generate_video(request):
    start_time = time.time()
    logger.info("STARTING VIDEO GENERATION v3 (per-sentence slides + captions)")
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
        bucket_name = "yt-" + data.get("video_id", video_id).lower()
        bucket = storage_client.bucket(bucket_name)
        jsonl_blobs = [b for b in storage_client.list_blobs(bucket_name, prefix="images/")
                       if b.name.endswith(".jsonl") and "prediction" in b.name]
        if not jsonl_blobs:
            return jsonify({"error": "No prediction JSONL files found"}), 404
        image_map: dict[int, str] = {}
        for idx, blob in enumerate(jsonl_blobs):
            local = os.path.join(tmpdir, f"shard_{idx}.jsonl")
            blob.download_to_filename(local)
            with open(local) as f:
                for line in f:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = item.get("key")
                    b64 = _get_image_b64(item)
                    if key is not None and b64:
                        try:
                            image_map[int(key)] = b64
                        except (ValueError, TypeError):
                            pass
            os.remove(local)
        if not image_map:
            return jsonify({"error": "No valid images found"}), 500
        logger.info("Images loaded: %d", len(image_map))
        available_keys = sorted(image_map.keys())
        script_rows = (sb.table("yt_scripts").select("sentence_number,sentence_text")
                       .eq("viral_video_id", video_id).order("sentence_number")
                       .execute().data or [])
        sentence_map: dict[int, str] = {r["sentence_number"]: (r.get("sentence_text") or "")
                                         for r in script_rows}
        logger.info("Script sentences: %d", len(sentence_map))
        audio_rows = _dedupe_audio(
            sb.table("yt_audio_files").select("*").eq("viral_video_id", video_id)
            .order("start_sentence_number").execute().data or [])
        if not audio_rows:
            return jsonify({"error": "No audio files found"}), 404
        logger.info("Audio chunks: %d", len(audio_rows))
        all_sent_nums = sorted(sentence_map.keys()) if sentence_map else [
            r["start_sentence_number"] for r in audio_rows]
        offset = min(available_keys) - min(all_sent_nums)
        logger.info("Image key offset: %d", offset)
        slide_paths: list[str] = []
        slide_idx = 0
        for chunk_idx, rec in enumerate(audio_rows):
            start_sent: int = rec["start_sentence_number"]
            end_sent: int = rec.get("end_sentence_number") or start_sent
            aud_chunk_path = os.path.join(tmpdir, f"chunk_{chunk_idx:04d}.wav")
            try:
                bucket.blob(rec["file_path"]).download_to_filename(aud_chunk_path)
            except Exception as exc:
                logger.error("Audio download failed chunk=%d: %s", chunk_idx, exc)
                continue
            chunk_duration = _probe_duration(ffmpeg_path, aud_chunk_path)
            logger.info("Chunk %d/%d sents %d->%d %.2fs",
                        chunk_idx+1, len(audio_rows), start_sent, end_sent, chunk_duration)
            sent_range = list(range(start_sent, end_sent + 1))
            sentence_texts = [sentence_map.get(s, "") for s in sent_range]
            sub_clips = _split_audio_by_proportion(ffmpeg_path, aud_chunk_path,
                                                    chunk_duration, sentence_texts,
                                                    tmpdir, chunk_idx)
            for sent_num, (sub_aud_path, sub_dur) in zip(sent_range, sub_clips):
                image_key = sent_num + offset
                if image_key not in image_map:
                    image_key = min(available_keys, key=lambda k: abs(k - image_key))
                img_path = os.path.join(tmpdir, f"img_{slide_idx:04d}.png")
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(image_map[image_key]))
                slide_out = os.path.join(tmpdir, f"slide_{slide_idx:04d}.mp4")
                ok = _render_slide(ffmpeg_path, img_path, sub_aud_path,
                                   slide_out, sub_dur,
                                   sentence_map.get(sent_num, ""), slide_idx)
                if ok:
                    slide_paths.append(slide_out)
                    logger.info("  Slide %d ok sent=%d %.2fs", slide_idx, sent_num, sub_dur)
                else:
                    logger.error("  Slide %d FAILED sent=%d", slide_idx, sent_num)
                try:
                    os.remove(img_path)
                except OSError:
                    pass
                if sub_aud_path != aud_chunk_path:
                    try:
                        os.remove(sub_aud_path)
                    except OSError:
                        pass
                slide_idx += 1
            try:
                os.remove(aud_chunk_path)
            except OSError:
                pass
        if not slide_paths:
            return jsonify({"error": "No slides rendered"}), 500
        logger.info("Slides rendered: %d", len(slide_paths))
        merged_path = os.path.join(tmpdir, "final.mp4")
        if not _concat_slides(ffmpeg_path, slide_paths, merged_path):
            return jsonify({"error": "Concat failed"}), 500
        dest_blob_name = f"final_videos/{video_id}.mp4"
        bucket.blob(dest_blob_name).upload_from_filename(merged_path)
        total_time = time.time() - start_time
        logger.info("Uploaded -> gs://%s/%s (%.1fs)", bucket_name, dest_blob_name, total_time)
        return jsonify({"success": True, "gcs_uri": f"gs://{bucket_name}/{dest_blob_name}",
                        "slides_rendered": len(slide_paths),
                        "processing_time_seconds": round(total_time, 2)}), 200
    except Exception as exc:
        import traceback
        logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": str(exc)}), 500
    finally:
        try:
            shutil.rmtree(tmpdir)
        except OSError:
            pass
