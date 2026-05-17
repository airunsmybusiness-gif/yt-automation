-- Reset a video for reprocessing
-- Run in Supabase SQL Editor
-- Replace VIDEO_ID with the actual video_id string

UPDATE yt_viral_videos
SET status = 'queued',
    suitable = true,
    production_started_at = NULL,
    production_completed_at = NULL,
    production_notes = NULL
WHERE video_id = 'REPLACE_WITH_VIDEO_ID';

-- Also clean up related data if re-running from scratch:
-- DELETE FROM yt_scripts WHERE viral_video_id = 'REPLACE_WITH_UUID';
-- DELETE FROM yt_audio_files WHERE viral_video_id = 'REPLACE_WITH_UUID';
-- DELETE FROM yt_viral_analyzer_results WHERE video_record_id = 'REPLACE_WITH_UUID';
-- DELETE FROM yt_strategist_results WHERE video_record_id = 'REPLACE_WITH_UUID';
