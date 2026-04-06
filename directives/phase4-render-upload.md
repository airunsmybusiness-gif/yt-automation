# Phase 4: Video Render + YouTube Upload — Directive

## Objective
Render the final MP4 from per-sentence images + audio, generate a thumbnail, upload to YouTube as private, and send notification email.

## Inputs
- GCS bucket with images (prediction JSONL shards) and audio (WAV files)
- yt_audio_files records (ordered by start_sentence_number)
- yt_strategist_results (title, description, tags)
- yt_agent_prompts: thumbnail_style prompt
- Background music from background-audio GCS bucket

## Outputs
- Final MP4 in GCS: final_videos/{viral_video_id}.mp4
- yt_results: GCS URL + thumbnail link
- YouTube upload (private)
- Gmail notification with YouTube URL
- yt_viral_videos.status = 'done'

## Flow
1. Call generate_video Cloud Function (FFmpeg rendering)
2. Save GCS URL to yt_results
3. Generate thumbnail via Gemini (thumbnail_style prompt)
4. Save thumbnail to GCS, update yt_results.thumbnail_link
5. Call upload_video Cloud Function (YouTube OAuth2)
6. Send notification email with YouTube URL
7. Update yt_viral_videos.status = 'done'

## Edge Cases
| Scenario | Handling |
|---|---|
| Missing images for some sentences | Cloud Function returns error with missing keys |
| FFmpeg timeout | Cloud Function has 540s timeout |
| Background music missing | Continue without BG music |
| YouTube upload quota exceeded | Alert, retry next day |
| Thumbnail generation fails | Upload video without thumbnail |
