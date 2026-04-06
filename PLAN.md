# YouTube Automation Pipeline — PLAN.md

## Brief
Fully automated faceless YouTube channel pipeline. Discovers viral videos, runs 4-agent AI analysis, generates TTS audio + AI images per sentence, renders MP4 via FFmpeg, uploads to YouTube. Zero manual steps after approval email.

## Architecture: DOE
- **Directives**: `directives/` — plain-English SOPs per pipeline stage
- **Orchestration**: `orchestration/` — Claude SDK routing (never computes)
- **Execution**: `execution/` — deterministic FastAPI endpoints, services, Cloud Function callers

## Stack
| Layer | Tool |
|---|---|
| API | FastAPI on Railway |
| Scheduling | APScheduler (in-process) |
| Database | Supabase (existing schema) |
| AI Agents | Claude API (claude-opus-4-6) |
| TTS | Gemini TTS batch |
| Images | Vertex AI Imagen batch |
| Video Render | FFmpeg via Cloud Function |
| Storage | Google Cloud Storage |
| YouTube Upload | Cloud Function (OAuth2) |
| Email | Gmail API |

---

## Phase 1 — Infrastructure + Discovery + Approval ✦ CURRENT
**Goal**: Railway service boots, connects to Supabase, discovers viral videos on cron, sends approval emails, processes replies.

### Tasks
1. `config/settings.py` — env validation, all secrets, Supabase client factory
2. `execution/services/supabase_client.py` — typed Supabase wrapper (CRUD for all key tables)
3. `execution/services/youtube_api.py` — viral video discovery (competitor channels + keyword search)
4. `execution/services/gmail_service.py` — send approval email, poll replies, match thread_id
5. `execution/api/main.py` — FastAPI app with APScheduler (12h discovery cron, 60s email poll, 08:00 UTC quota reset)
6. `execution/api/routes/webhooks.py` — manual URL submission endpoint
7. `.env.example`, `requirements.txt`, `Dockerfile`, `railway.toml`

### Definition of Done
- [ ] `python -m execution.api.main` boots, logs config, connects to Supabase
- [ ] Cron fires discovery → new rows in yt_viral_videos
- [ ] Approval email sent with video link
- [ ] "yes"/"no" reply updates suitable flag
- [ ] Manual POST /api/submit-url queues a video
- [ ] All tests pass

---

## Phase 2 — Data Collection + Agent Pipeline
**Goal**: On approval, scrape comments/transcript/thumbnail in parallel, then run 4 sequential agents.

### Tasks
1. `execution/services/comment_scraper.py` — YouTube API → yt_comments
2. `execution/services/transcript_service.py` — Supadata → Gemini fallback → yt_video_transcripts
3. `execution/services/thumbnail_describer.py` — OpenRouter → Gemini fallback
4. `execution/agents/agent_runner.py` — sequential Agent 1→2→3→4 with prompt loading from yt_agent_prompts
5. `orchestration/pipeline.py` — parallel data collection trigger → agent chain trigger
6. `execution/api/routes/pipeline.py` — POST /api/trigger-pipeline/{video_id}

### Definition of Done
- [ ] 3 parallel jobs complete and save to correct tables
- [ ] Agent 1→4 chain runs, saves to yt_scripts
- [ ] Pipeline handles API failures with retry + email alert

---

## Phase 3 — TTS + Image Generation
**Goal**: Convert scripts to audio (Gemini TTS batch) and images (Vertex AI Imagen batch).

### Tasks
1. `execution/services/tts_service.py` — JSONL generation, GCS upload, batch job submit, poll
2. `execution/services/image_service.py` — image prompt generation, JSONL, Vertex batch, poll
3. `execution/services/gcs_client.py` — GCS upload/download/list wrapper
4. Cloud Function: `extract_audio` — batch output → individual WAV files

### Definition of Done
- [ ] TTS batch completes, WAV files in GCS per sentence
- [ ] Image batch completes, images in GCS keyed by sentence_number
- [ ] Batch job status tracked in yt_batch_jobs

---

## Phase 4 — Video Render + YouTube Upload
**Goal**: FFmpeg renders final MP4, thumbnail generated, uploaded to YouTube as private.

### Tasks
1. Cloud Function: `generate_video` — image+audio per sentence → concat → background music → MP4
2. `execution/services/thumbnail_generator.py` — Gemini thumbnail generation
3. Cloud Function: `upload_video` — YouTube OAuth2 upload + set thumbnail
4. `execution/services/notification_service.py` — Gmail notification with YouTube URL
5. Status lifecycle: queued → production_started → done

### Definition of Done
- [ ] End-to-end: viral video → private YouTube upload in one automated run
- [ ] Email notification with YouTube URL
- [ ] yt_viral_videos.status = done
