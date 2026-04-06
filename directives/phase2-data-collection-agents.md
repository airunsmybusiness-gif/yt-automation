# Phase 2: Data Collection + Agent Pipeline — Directive

## Objective
When a video is approved (suitable=true), run 3 parallel data collection jobs, then execute 4 sequential AI agents to produce a production-ready script.

## Inputs
- Approved yt_viral_videos record (suitable=true, status=queued)
- YouTube Data API (comments)
- Supadata API / Gemini fallback (transcript)
- Gemini Vision (thumbnail description)
- Agent prompts from yt_agent_prompts table

## Outputs
- yt_comments: all top-level + reply comments for the video
- yt_video_transcripts: source transcript (type='source')
- yt_viral_videos.thumbnail_description: text description of thumbnail
- yt_viral_analyzer_results: Agent 1 JSON output
- yt_strategist_results: Agent 2 JSON output
- yt_scripts: sentence-by-sentence script (150+ sentences, 1200-2250 words)
- yt_viral_videos.status updated to 'production_started'

## Flow
1. Poll for approved videos (suitable=true, status=queued)
2. Set status='production_started', production_started_at=now()
3. Launch 3 parallel jobs:
   a. Comment scraping (YouTube commentThreads API → yt_comments)
   b. Transcript fetching (Supadata → Gemini fallback → yt_video_transcripts)
   c. Thumbnail description (download thumbnail → Gemini Vision → yt_viral_videos.thumbnail_description)
4. Wait for all 3 to complete (or timeout after 5 minutes)
5. Run Agent 1 (Analyzer): video metadata + transcript + comments → analysis JSON
6. Run Agent 2 (Strategist): Agent 1 output → strategy JSON
7. Run Agent 3 (Script Writer): Agent 1 + Agent 2 → sentence array
8. Run Agent 4 (Optimizer): Agent 3 output → cleaned sentence array
9. Save final sentences to yt_scripts
10. Update yt_viral_videos.transcript_status = 'completed'

## Edge Cases
| Scenario | Handling |
|---|---|
| Supadata transcript fails | Fallback to Gemini (use video URL directly) |
| Gemini transcript also fails | Mark transcript_status='failed', alert, stop pipeline |
| Comments API quota exceeded | Save whatever was fetched, continue pipeline |
| No comments on video | Insert empty, continue (comments are optional context) |
| Thumbnail download fails | Set thumbnail_description to 'No thumbnail available' |
| Agent returns invalid JSON | Retry once with stricter prompt, then fail with alert |
| Agent output too short (<150 sentences) | Retry with explicit length instruction |
| Agent timeout (>120s) | Retry once, then fail |
| Concurrent pipeline runs | One video at a time enforced by status check |

## Self-Annealing Rules
- On Supadata failure: log ANNEALING, auto-fallback to Gemini
- On agent JSON parse error: log raw output, retry with "respond ONLY with valid JSON"
- On short script: log sentence count, retry with "minimum 150 sentences required"
- Weekly: check yt_viral_videos where status='production_started' for >24h → alert stale pipelines
