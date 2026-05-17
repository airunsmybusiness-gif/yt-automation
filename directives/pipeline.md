# Pipeline Directive — MindSeam Automation v2

## Objective
Fully automated faceless YouTube channel. Discovers viral psychology videos,
rewrites them as original content, renders publishable video, uploads to YouTube.
Only human interaction: reply "yes" to approval email.

## Inputs
- yt_competitors table (5 channels: TopThink, BetterThanYesterday, etc.)
- yt_search_keywords table (psychology/self-improvement terms)
- yt_agent_prompts table (6 prompts: analyzer, strategist, script_writer, optimizer, image_generator, thumbnail_style)
- yt_workflow_settings (viral thresholds)

## Outputs
- Published YouTube video on MindSeam channel
- Gmail notification with video URL
- Updated yt_viral_videos record (status=done)

## Constraints
- $2.00 per video MAX
- 8-10 minute video length
- 1 upload per 24 hours
- No Vertex AI (billing risk)
- No Cloud Functions (single-process simplicity)
- All secrets via Railway env vars

## Pipeline Stages

### Stage 0: Discovery (cron, 14:00 UTC)
1. Fetch non-exhausted API key from yt_api_accounts
2. For each active competitor: get uploads playlist, fetch recent videos
3. Apply viral threshold (minViews, earlyViews, earlyHours, maxAgeHours)
4. Filter: 1-30 min duration, not a Short, not already in DB
5. Dedup against yt_viral_videos
6. Insert new viral videos as status=queued
7. Send approval email with video title, views, link
8. Save thread_id to yt_viral_videos
9. If no channel results: fall back to keyword search

### Stage 0b: Approval (poll, every 60s)
1. For each queued video with thread_id: check Gmail for reply
2. If reply contains "yes" → set suitable=true
3. If reply contains "no" → set suitable=false, skip

### Stage 1: Data Collection (triggered by process_next, every 2 min)
1. Pick oldest suitable=true, status=queued video
2. Set status=production_started
3. Parallel:
   a. Fetch transcript (Supadata API → Gemini fallback if unavailable)
   b. Scrape top 50 comments (YouTube Data API)
   c. Describe thumbnail (Claude Haiku on thumbnail URL)
4. Save to yt_video_transcripts, yt_comments, yt_viral_videos.thumbnail_description
5. If transcript unavailable after both attempts: mark failed, email alert, skip

### Stage 2: Agent Pipeline (sequential)
1. Load all active prompts from yt_agent_prompts
2. Agent 1 (Analyzer): transcript + comments + thumbnail → viral pattern JSON
3. Agent 2 (Strategist): analyzer output → title, desc, tags, thumbnail brief, category
4. Agent 3 (Script Writer): analyzer + strategist → 80-120 numbered sentences
5. Agent 4 (Optimizer): raw script → pacing pass → final sentences
6. Save: yt_viral_analyzer_results, yt_strategist_results, yt_scripts
7. Gate: if < 80 or > 120 sentences, log warning (don't block)

### Stage 3: Media Generation (sequential, cost-controlled)
1. Group sentences into pairs (1-2, 3-4, 5-6, ...)
2. For each sentence: generate Edge TTS audio → /tmp/{vid}/audio/sent_{N:04d}.mp3
3. For each pair: Claude Haiku scene transform → image prompt
4. For each pair: Replicate Flux Dev → /tmp/{vid}/images/img_{N:04d}.jpg
   - Track cost: if cumulative > $2, stop and use last image as fallback
   - Max 50 images hard cap
5. Generate thumbnail: Replicate Flux Dev from Strategist thumbnail_brief
6. Add text overlay to thumbnail via PIL
7. Save metadata to yt_audio_files (for audit trail)

### Stage 4: Render (in-process FFmpeg)
1. For each image pair:
   a. Concat paired sentence audios → pair audio
   b. Create segment: loop image + pair audio → .ts
2. Crossfade-concat all segments → merged.mp4
3. Quality gate: duration must be 7-11 minutes
4. Save to /tmp/{vid}/final.mp4

### Stage 5: Upload (in-process)
1. YouTube Data API: resumable upload of final.mp4
   - Title, description, tags from Strategist
   - Category 27 (Education)
   - Privacy: private
2. Set custom thumbnail
3. Save YouTube video_id to yt_results
4. Gmail notification: "New video uploaded: {title} — {url}"
5. Update yt_viral_videos: status=done, production_completed_at=now()

### Stage 6: Cleanup
1. Delete /tmp/{vid}/ entirely
2. Log total cost, duration, sentence count

## Edge Cases

| Scenario | Handling |
|----------|----------|
| All API keys exhausted | Mark keys, email alert, wait for quota reset |
| Transcript unavailable | Gemini fallback; if both fail, skip video |
| Replicate timeout | Retry 3x with backoff; if all fail, use fallback image |
| FFmpeg render fails | Log error with stderr, email alert |
| YouTube upload 403 | Token expired: log, email, skip (needs manual re-mint) |
| Video > 11 min | Log warning, upload anyway (11 min is soft cap) |
| Cost > $2 | Halt image generation, render with available images |
| Duplicate video in DB | Skip silently (dedup at discovery) |

## Self-Annealing Rules
- On Replicate 429: increase delay between requests, log new delay
- On FFmpeg error: capture stderr, add to edge-cases.md
- On agent producing < 80 sentences: log prompt version, flag for review
- Weekly: check logs for ANNEALING tags, update this directive

## Definition of Done
- [ ] Video uploaded to MindSeam with thumbnail
- [ ] 8-10 minutes, crossfade transitions
- [ ] Full SEO metadata (title, desc, tags, category 27)
- [ ] Cost < $2.00
- [ ] Gmail notification sent
- [ ] yt_viral_videos.status = done
- [ ] /tmp cleaned up
- [ ] Total pipeline time < 20 minutes
