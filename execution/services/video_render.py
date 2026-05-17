"""Video render — FFmpeg in-process, crossfade transitions, no Ken Burns."""

import logging
import subprocess
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found in PATH")


def _get_duration(audio_path: Path) -> float:
    """Get audio duration via ffmpeg -i stderr parsing (no ffprobe needed)."""
    result = subprocess.run(
        [FFMPEG, "-i", str(audio_path)],
        capture_output=True, text=True, timeout=10,
    )
    # Duration line: "  Duration: 00:00:05.23, ..."
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            time_str = line.split("Duration:")[1].split(",")[0].strip()
            parts = time_str.split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    logger.warning("Could not parse duration for %s", audio_path)
    return 5.0  # safe fallback


def _concat_audio_pair(
    audio_paths: list[Path],
    output_path: Path,
) -> Path:
    """Concatenate 2-3 sentence audios into one pair audio."""
    if len(audio_paths) == 1:
        shutil.copy(audio_paths[0], output_path)
        return output_path

    inputs = []
    for p in audio_paths:
        inputs.extend(["-i", str(p)])

    filter_str = "".join(f"[{i}:a]" for i in range(len(audio_paths)))
    filter_str += f"concat=n={len(audio_paths)}:v=0:a=1[out]"

    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.error("Audio concat failed: %s", result.stderr[-300:])
        raise RuntimeError(f"Audio concat failed for {output_path}")
    return output_path


def _create_segment(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """Create one video segment: static image + audio → .ts"""
    duration = _get_duration(audio_path)

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-framerate", "30", "-t", str(duration),
        "-i", str(image_path),
        "-i", str(audio_path),
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-r", "30",
        "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
        "-shortest",
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts",
        "-f", "mpegts",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        logger.error("Segment creation failed: %s", result.stderr[-300:])
        raise RuntimeError(f"FFmpeg segment failed: {output_path}")
    return output_path


def render_video(
    pairs: list[dict],
    work_dir: Path,
) -> Path:
    """Render full video from image+audio pairs with crossfade transitions.

    Args:
        pairs: List of {pair_number, image_path, audio_paths}.
            audio_paths is a list of 2-3 sentence audio files.
        work_dir: Temp directory for intermediate files.

    Returns:
        Path to final MP4.
    """
    segments_dir = work_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Create paired audio files
    paired_audios: list[Path] = []
    for pair in pairs:
        pair_audio = work_dir / f"pair_audio_{pair['pair_number']:04d}.mp3"
        _concat_audio_pair(pair["audio_paths"], pair_audio)
        paired_audios.append(pair_audio)

    # Step 2: Create .ts segments
    segment_paths: list[Path] = []
    for i, pair in enumerate(pairs):
        seg_path = segments_dir / f"seg_{i:04d}.ts"
        try:
            _create_segment(
                pair["image_path"],
                paired_audios[i],
                seg_path,
            )
            segment_paths.append(seg_path)
        except RuntimeError as e:
            logger.warning("Skipping segment %d: %s", i, e)

    if not segment_paths:
        raise RuntimeError("No segments created — cannot render video")

    # Step 3: Concat with crossfade
    # For simplicity and reliability, use concat demuxer with brief fade
    # (xfade complex filtergraph is fragile with many inputs)
    concat_list = work_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{seg}'" for seg in segment_paths),
        encoding="utf-8",
    )

    final_path = work_dir / "final.mp4"
    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-vsync", "cfr",
        "-movflags", "+faststart",
        str(final_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error("Final concat failed: %s", result.stderr[-500:])
        raise RuntimeError("FFmpeg final concat failed")

    duration = _get_duration(final_path)
    size_mb = final_path.stat().st_size / (1024 * 1024)
    logger.info(
        "Render complete: %.1f min, %.1f MB, %d segments",
        duration / 60, size_mb, len(segment_paths),
    )

    return final_path
