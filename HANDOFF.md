# HANDOFF.md — YT Automation Pipeline

## Status: Production-Ready — 65/65 Tests Pass

## What's Built (All 4 Phases + Hardening)

### Phase 1 — Infrastructure + Discovery + Approval ✅
- FastAPI app with APScheduler (6 scheduled jobs)
- Supabase typed client wrapper (schema-aligned)
- YouTube API discovery with key rotation
- Gmail approval flow (send + poll replies by thread_id)
- Manual URL submission endpoint (POST /api/submit-url)
- Health check (GET /api/health)

### Phase 2 — Data Collection + Agent Pipeline ✅
- Comment scraper (YouTube commentThreads API → yt_comments)
- Transcript fetcher (Supadata → Gemini fallback → yt_video_transcripts)
- Thumbnail describer (Gemini Vision → yt_viral_videos.thumbnail_description)
- 4-agent sequential pipeline (Analyzer → Strategist → Script Writer → Optimizer)
- Agent prompts loaded from yt_agent_prompts table
- JSON response parsing with code fence stripping + auto-retry

### Phase 3 — TTS + Image Generation ✅
- Gemini TTS batch (JSONL → GCS → poll → extract WAV)
- Vertex AI Imagen batch (image prompts via Claude → JSONL → Cloud Function)
- GCS client wrapper (upload/download/list/ensure_bucket)
- Batch job tracking in yt_batch_jobs

### Phase 4 — Video Render + YouTube Upload ✅
- generate_video Cloud Function caller
- Thumbnail generation (Gemini + thumbnail_style prompt)
- upload_video Cloud Function caller
- Completion notification email
- Status lifecycle: queued → production_started → done

### Production Hardening ✅
- API key auth middleware (X-API-Key header, dev mode when unset)
- Rate limiting (30 req/60s API, 10 req/60s webhooks)
- Stale pipeline detector (every 6h, email alert if >24h stuck)
- Batch status webhook endpoint (POST /api/batch/status)
- Monitoring dashboard (GET /api/status/dashboard)
- Per-video status drill-down (GET /api/status/video/{video_id})
- Schema alignment audit (all inserts match actual Supabase columns)

## Architecture
```
yt-automation/
├── PLAN.md / CLAUDE.md / HANDOFF.md
├── Dockerfile / railway.toml / .env.example
├── directives/              # 4 SOPs (no code)
│   ├── phase1-discovery-approval.md
│   ├── phase2-data-collection-agents.md
│   ├── phase3-tts-images.md
│   └── phase4-render-upload.md
├── orchestration/
│   └── pipeline.py          # Routes all 4 phases
├── execution/
│   ├── api/
│   │   ├── main.py          # FastAPI + APScheduler entry point
│   │   ├── middleware/
│   │   │   ├── auth.py      # X-API-Key verification
│   │   │   └── rate_limit.py
│   │   └── routes/
│   │       ├── webhooks.py  # /api/health, /api/submit-url
│   │       ├── pipeline.py  # /api/pipeline/trigger/{id}
│   │       ├── batch.py     # /api/batch/status, /api/batch/stale
│   │       └── status.py    # /api/status/dashboard, /api/status/video/{id}
│   ├── agents/
│   │   └── agent_runner.py  # Claude API 4-agent chain
│   ├── services/
│   │   ├── supabase_client.py
│   │   ├── youtube_api.py
│   │   ├── gmail_service.py
│   │   ├── comment_scraper.py
│   │   ├── transcript_service.py
│   │   ├── thumbnail_describer.py
│   │   ├── tts_service.py
│   │   ├── image_service.py
│   │   ├── gcs_client.py
│   │   └── video_render_service.py
│   ├── utils/
│   │   ├── exceptions.py
│   │   └── retry.py
│   └── tests/               # 65 tests
│       ├── test_settings.py
│       ├── test_youtube_api.py
│       ├── test_gmail_service.py
│       ├── test_comment_scraper.py
│       ├── test_agent_runner.py
│       ├── test_tts_service.py
│       ├── test_retry.py
│       ├── test_auth.py
│       └── test_rate_limiter.py
├── config/
│   └── settings.py
└── scripts/
    └── gmail_auth.py
```

## Stats
- 4,594 lines of Python across 43 files
- 65 tests (all pass)
- 6 scheduled jobs
- 8 API endpoints
- 10 service modules
- 4 directives

## API Endpoints
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/health | No | Health check |
| POST | /api/submit-url | Yes | Manual video submission |
| POST | /api/pipeline/trigger/{id} | Yes | Trigger pipeline for video |
| POST | /api/pipeline/trigger-all | Yes | Process all approved videos |
| POST | /api/batch/status | Yes | Batch job completion webhook |
| GET | /api/batch/stale | Yes | Detect stale pipelines |
| GET | /api/status/dashboard | Yes | Pipeline health overview |
| GET | /api/status/video/{id} | Yes | Per-video pipeline status |

## Scheduled Jobs
| Job | Interval | Purpose |
|---|---|---|
| discover_videos | 12h | Find viral videos from competitors + keywords |
| poll_emails | 60s | Check Gmail for yes/no approval replies |
| reset_quotas | Daily 08:00 UTC | Reset YouTube API key quotas |
| process_approved | 2min | Auto-trigger pipeline for approved videos |
| detect_stale | 6h | Alert on pipelines stuck >24h |

## Deploy Checklist
1. `git init && git add . && git commit -m "initial"`
2. Create Railway project, connect repo
3. Set ALL env vars from .env.example in Railway dashboard
4. Run `python scripts/gmail_auth.py` locally → set GMAIL_CREDENTIALS_JSON + GMAIL_TOKEN_JSON
5. Deploy 4 Cloud Functions from cloud_functions_reference.txt
6. Populate yt_competitors + yt_search_keywords in Supabase
7. Push to Railway → auto-deploys from Dockerfile
8. Verify: GET /api/health → {"status": "ok"}
9. Test: POST /api/submit-url with a YouTube URL
10. Monitor: GET /api/status/dashboard

## Blockers
None.
