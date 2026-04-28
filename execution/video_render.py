"""
Local FFmpeg video rendering on Railway disk.
Replaces Google Cloud Function generate-video.
Psych2Go style: 1 image per sentence, clean hard cuts, no zoom.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from execution import openrouter_images

log = logging.getLogger(__name__)

OUT_W: int = 1920
OUT_H: int = 1080
FPS: int = 25
CRF: int = 20


class RenderError(RuntimeError):
    """Raised when video rendering fails."""


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [_ffmpeg(), "-i", str(audio_path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    for line in result.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            ts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def _split_audio_per_sentence(
    audio_path: Path, total_dur: float, num_sentences: int, tmp_dir: Path, chunk_idx: int,
) -> list[tuple[Path, float]]:
    if num_sentences <= 1:
        return [(audio_path, total_dur)]
    seg_dur = total_dur / num_sentences
    segments: list[tuple[Path, float]] = []
    for i in range(num_sentences):
        start = i * seg_dur
        out_path = tmp_dir / f"seg_{chunk_idx:04d}_{i}.mp3"
        result = subprocess.run(
            [_ffmpeg(), "-y", "-i", str(audio_path), "-ss", str(start),
             "-t", str(seg_dur), "-c", "copy", str(out_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and out_path.exists():
            actual = _get_audio_duration(out_path)
            if actual > 0:
                segments.append((out_path, actual))
    return segments if segments else [(audio_path, total_dur)]


def _render_slide(img_path: Path, audio_path: Path, out_path: Path) -> bool:
    vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:white,fps={FPS},format=yuv420p"
    )
    proc = subprocess.run(
        [
            _ffmpeg(), "-y",
            "-loop", "1", "-i", str(img_path),
            "-i", str(audio_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        log.error(f"Slide render failed: {proc.stderr[-400:]}")
    return proc.returncode == 0 and out_path.exists()


def _generate_thumbnail(title: str, first_image: Path, out_path: Path) -> bool:
    """Generate YouTube thumbnail via OpenRouter. Falls back to first frame on failure."""
    prompt = (
        f"YouTube thumbnail, 1280x720 16:9 landscape. "
        f"Hand-drawn folk-art stick figure illustration in dark navy blue ink on cream paper. "
        f"Bold, expressive stick figure character with large emotive eyes, central composition. "
        f"Bold uppercase text overlay in chunky black sans-serif font reading: \"{title.upper()}\". "
        f"Text takes up roughly 40% of the image, positioned top or bottom for high contrast. "
        f"Naive outsider-art aesthetic, visible pencil strokes, minimal shading, "
        f"emotionally resonant, psychology-themed. Empty whitespace around the figure. "
        f"Style of channel: MindSeam — psychology and self-improvement."
    )
    job = [{"sentence_number": 9999, "formatted_prompt": prompt}]
    try:
        result = openrouter_images.generate_batch(job, out_path.parent)
        # Generator names file 9999.jpg in out_path.parent
        generated = out_path.parent / "9999.jpg"
        if result["success_count"] >= 1 and generated.exists():
            generated.rename(out_path)
            return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(f"Thumbnail OpenRouter failed: {exc}")
    # Fallback: resize first slide
    proc = subprocess.run(
        [_ffmpeg(), "-y", "-i", str(first_image), "-vf", "scale=1280:720",
         "-q:v", "2", str(out_path)],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0 and out_path.exists()


def render_video(
    audio_chunks: list[dict],
    images_dir: Path,
    work_dir: Path,
    output_path: Path,
    title: str = "",
) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    slide_paths: list[Path] = []
    slide_num = 0

    available_images = sorted(
        int(p.stem) for p in images_dir.glob("*.jpg") if p.stem.isdigit()
    )
    if not available_images:
        raise RenderError(f"No images found in {images_dir}")

    for idx, chunk in enumerate(audio_chunks, start=1):
        audio_path = Path(chunk["local_audio_path"])
        if not audio_path.exists():
            log.warning(f"Audio missing for chunk {idx}: {audio_path}")
            continue
        total_dur = _get_audio_duration(audio_path)
        if total_dur <= 0:
            continue

        start_sentence = int(chunk["start_sentence"])
        num_sentences = int(chunk["num_sentences"])
        segments = _split_audio_per_sentence(
            audio_path, total_dur, num_sentences, work_dir, idx
        )

        for seg_i, (seg_aud, seg_dur) in enumerate(segments):
            sentence_num = start_sentence + seg_i
            img_path = images_dir / f"{sentence_num:04d}.jpg"
            if not img_path.exists():
                closest = min(available_images, key=lambda k: abs(k - sentence_num))
                img_path = images_dir / f"{closest:04d}.jpg"
                log.warning(f"Sentence {sentence_num}: fallback to image {closest}")

            slide_num += 1
            slide_out = work_dir / f"slide_{slide_num:04d}.ts"
            if _render_slide(img_path, seg_aud, slide_out):
                slide_paths.append(slide_out)

    if not slide_paths:
        raise RenderError("No slides rendered")

    concat_list = work_dir / "concat.txt"
    with concat_list.open("w") as f:
        for p in slide_paths:
            f.write(f"file '{p}'\n")

    merge_proc = subprocess.run(
        [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", "-fflags", "+genpts", str(output_path)],
        capture_output=True, text=True, timeout=600,
    )
    if merge_proc.returncode != 0:
        raise RenderError(f"Final merge failed: {merge_proc.stderr[-400:]}")

    thumb_path = output_path.parent / f"{output_path.stem}_thumb.jpg"
    first_img = images_dir / f"{available_images[0]:04d}.jpg"
    thumb_ok = _generate_thumbnail(title, first_img, thumb_path)

    log.info(f"Rendered {slide_num} slides -> {output_path}")
    return {
        "slides_total": slide_num,
        "video_path": str(output_path),
        "thumbnail_path": str(thumb_path) if thumb_ok else None,
    }
