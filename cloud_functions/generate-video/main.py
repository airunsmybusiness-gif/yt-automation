import os
import json
import base64
import subprocess
import tempfile
import shutil
import functions_framework
from flask import jsonify
from google.cloud import storage
import imageio_ffmpeg
import time
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def get_ai_image_b64(item):
    try:
        resp = item.get('response', {})
        parts = resp.get('candidates', [{}])[0].get('content', {}).get('parts', [])
        for part in parts:
            data = part.get('inlineData', {}).get('data') or part.get('inline_data', {}).get('data')
            if data:
                return data
    except Exception:
        return None

def calculate_mapping_offset(image_keys, sentence_numbers):
    if not image_keys or not sentence_numbers:
        return None
    return min(image_keys) - min(sentence_numbers)

@functions_framework.http
def generate_video(request):
    start_time = time.time()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    data = request.get_json(silent=True) or {}
    viral_video_id = data.get('viral_video_id')  # UUID for DB
    video_id = data.get('video_id')              # YouTube string for bucket
    bg_music_name = data.get('bg_music_name', 'audio1.mp3')
    bg_vol = data.get('bg_volume', 0.15)
    if not viral_video_id or not video_id:
        return jsonify({"error": "viral_video_id (UUID) and video_id (YouTube) both required"}), 400
    tmpdir = tempfile.mkdtemp()
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        storage_client = storage.Client()
        bucket_name = f"yt-{video_id.lower()}"
        bucket = storage_client.bucket(bucket_name)
        blobs = list(storage_client.list_blobs(bucket_name, prefix="images/"))
        jsonl_blobs = [b for b in blobs if b.name.endswith(".jsonl") and "prediction" in b.name]
        if not jsonl_blobs:
            return jsonify({"error": "No prediction JSONL files found"}), 404
        image_map = {}
        for idx, jsonl_blob in enumerate(jsonl_blobs):
            local_jsonl = os.path.join(tmpdir, f"shard_{idx}.jsonl")
            jsonl_blob.download_to_filename(local_jsonl)
            with open(local_jsonl, 'r') as f:
                for line in f:
                    item = json.loads(line)
                    key = item.get('key')
                    b64_data = get_ai_image_b64(item)
                    if key is not None and b64_data:
                        try:
                            image_map[int(key)] = b64_data
                        except (ValueError, TypeError):
                            pass
            os.remove(local_jsonl)
        if not image_map:
            return jsonify({"error": "No valid images found"}), 500
        available_keys = sorted(image_map.keys())
        audio_res = supabase.table("yt_audio_files").select("*").eq("viral_video_id", viral_video_id).order("start_sentence_number").execute()
        if not audio_res.data:
            return jsonify({"error": "No audio files found"}), 404
        sentence_numbers = [rec['start_sentence_number'] for rec in audio_res.data]
        offset = calculate_mapping_offset(available_keys, sentence_numbers)
        expected_keys = set(s + offset for s in sentence_numbers)
        missing_keys = sorted(expected_keys - set(available_keys))
        if missing_keys:
            return jsonify({"error": f"Missing {len(missing_keys)} images", "missing_image_keys": missing_keys[:20]}), 400
        chunks = []
        for idx, rec in enumerate(audio_res.data):
            sentence_num = rec.get('start_sentence_number')
            aud_path = os.path.join(tmpdir, f"a_{idx}.mp3")
            try:
                bucket.blob(rec['file_path']).download_to_filename(aud_path)
            except Exception as e:
                continue
            image_key = sentence_num + offset
            img_path = os.path.join(tmpdir, f"i_{idx}.png")
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(image_map[image_key]))
            chunk_out = os.path.join(tmpdir, f"c_{idx:04d}.ts")
            try:
                result = subprocess.run([ffmpeg_path, "-y", "-loop", "1", "-framerate", "30", "-i", img_path, "-i", aud_path, "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-r", "30", "-c:a", "aac", "-ar", "44100", "-b:a", "128k", "-shortest", "-avoid_negative_ts", "make_zero", "-fflags", "+genpts", "-movflags", "+faststart", "-f", "mpegts", chunk_out], capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    continue
            except subprocess.TimeoutExpired:
                continue
            chunks.append(chunk_out)
            try:
                os.remove(img_path)
                os.remove(aud_path)
            except Exception:
                pass
        if not chunks:
            return jsonify({"error": "No chunks created"}), 500
        concat_list = os.path.join(tmpdir, "list.txt")
        with open(concat_list, "w") as f:
            for c in chunks:
                f.write(f"file '{c}'\n")
        merged_video = os.path.join(tmpdir, "merged.mp4")
        subprocess.run([ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-fflags", "+genpts", "-avoid_negative_ts", "make_zero", "-vsync", "cfr", merged_video], capture_output=True, text=True, timeout=300)
        bg_local = os.path.join(tmpdir, "bg.mp3")
        try:
            storage_client.bucket("yt-auto-bg-audio-ls").blob(bg_music_name).download_to_filename(bg_local)
        except Exception:
            bg_local = None
        final_video = os.path.join(tmpdir, "final.mp4")
        if bg_local and os.path.exists(bg_local):
            bg_result = subprocess.run([ffmpeg_path, "-y", "-i", merged_video, "-stream_loop", "-1", "-i", bg_local, "-filter_complex", f"[1:a]volume={bg_vol}[bg];[0:a][bg]amix=inputs=2:duration=first[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", final_video], capture_output=True, text=True, timeout=120)
            if bg_result.returncode != 0:
                shutil.copy(merged_video, final_video)
        else:
            shutil.copy(merged_video, final_video)
        dest_blob = bucket.blob(f"final_videos/{video_id}.mp4")
        dest_blob.upload_from_filename(final_video)
        total_time = time.time() - start_time
        return jsonify({"success": True, "url": dest_blob.public_url, "chunks_processed": len(chunks), "processing_time_seconds": round(total_time, 2)}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass
