# MindSeam Pipeline v2 — PLAN.md

## What this is
Complete rebuild of the pipeline that produced LOzrFoSHnGA, fixing every problem:
- Images were great (Vertex AI Imagen) → KEEP, but add hard budget cap
- 40-second slides with Parkinson's-zoom → FIX: 1 image per sentence, smooth crossfade
- Gemini TTS sounded robotic → FIX: Edge TTS (free, Microsoft neural voices)
- 21 minutes, boring, no hook → FIX: agent prompts rewritten, 8-10 min cap
- Vertex AI batch kept spinning ($124 bill) → FIX: hard timeout + job cancellation
- No thumbnail → FIX: scroll-stopping thumbnail via Flux Dev
- No SEO optimization → FIX: Strategist agent produces full metadata
- Upload was manual curl → FIX: automated in pipeline

## Budget: $2.00/video, nothing else
| Component | Cost | Provider |
|-----------|------|----------|
| 4 AI agents (script) | $0.40 | Anthropic Claude Haiku 4.5 |
| 50 images × $0.03 | $1.50 | Replicate Flux Dev |
| Voice narration | $0.00 | Edge TTS (Microsoft, free) |
| Thumbnail | $0.00 | Replicate (1 extra image) |
| FFmpeg render | $0.00 | In-process on Railway |
| YouTube upload | $0.00 | In-process on Railway |
| Railway compute | $0.10 | Prorated |
| **Total** | **$2.00** | |

NO Vertex AI. NO Cloud Functions. NO GCS buckets per video. NO batch jobs.
Everything runs in one Railway process. Images stored as local temp files, rendered, uploaded, deleted.

## Architecture: single Railway FastAPI process

```
APScheduler cron (14:00 UTC)
  → viral_finder.py discovers videos from yt_competitors + yt_search_keywords
  → email_sender.py sends approval email
  → poll_approvals (60s) reads Gmail replies
  → process_next (2 min) picks up approved video:

    Stage 1: Data Collection (parallel)
      - Supadata transcript (Gemini fallback)
      - YouTube comments scraper
      - Thumbnail description (Haiku)

    Stage 2: Agent Pipeline (sequential)
      - Agent 1: Viral Analyzer → yt_viral_analyzer_results
      - Agent 2: Strategist → yt_strategist_results (title, desc, tags, thumbnail brief)
      - Agent 3: Script Writer → 80-120 sentences, 8-10 min target
      - Agent 4: Optimizer → yt_scripts (final)

    Stage 3: Media Generation (sequential, controlled)
      - Edge TTS: 1 audio file per sentence → /tmp/{video_id}/audio/
      - Replicate Flux Dev: 1 image per 2 sentences → /tmp/{video_id}/images/
        - Scene transform via Haiku before each image
        - Max 50 images, hard cap
        - 1 thumbnail image from Strategist brief

    Stage 4: Render + Upload (sequential)
      - FFmpeg in-process:
        - Each sentence: image + audio → .ts segment
        - Crossfade transitions (0.3s) between segments
        - 1280×720, libx264, CRF 23, CFR 30fps
        - Concat → final MP4
      - YouTube upload via google-api-python-client
        - Strategist title, description, tags
        - Thumbnail set
        - Privacy: private (flip to public manually or on schedule)
      - Gmail notification with YouTube link

    Cleanup: delete /tmp/{video_id}/
```

## What's different from LOzrFoSHnGA pipeline

| LOzrFoSHnGA (April) | v2 (this rebuild) |
|----------------------|--------------------|
| 4 Cloud Functions | 0 Cloud Functions |
| Vertex AI Imagen batch | Replicate Flux Dev (per-image, controlled) |
| Gemini TTS batch | Edge TTS (per-sentence, free) |
| GCS bucket per video | Local /tmp, cleaned after upload |
| 197 images, 40 audio chunks | 1 image per 2 sentences, 1 audio per sentence |
| 40s per slide, Ken Burns shake | 3-8s per slide, crossfade transitions |
| No budget cap, $124 bill | Hard $2 cap, Replicate spend tracked |
| Manual curl upload | Automated in pipeline |
| No thumbnail | Scroll-stopping thumbnail from Strategist |
| 21 min, no hook | 8-10 min, 5-beat viral hook formula |
| OpenRouter agents | Anthropic SDK direct (Haiku 4.5) |

## File map

```
yt-pipeline-v2/
├── PLAN.md
├── CLAUDE.md
├── .env.example
├── requirements.txt
├── Dockerfile
├── railway.toml
├── directives/
│   ├── pipeline.md          # Full pipeline SOP
│   ├── agent-prompts.md     # All 6 agent prompts (portable)
│   └── video-quality.md     # Render spec, transitions, timing
├── orchestration/
│   └── pipeline.py          # Main orchestrator (process_video)
├── execution/
│   ├── api/
│   │   └── main.py          # FastAPI + APScheduler
│   ├── services/
│   │   ├── supabase_client.py
│   │   ├── youtube_api.py   # Discovery + key rotation
│   │   ├── gmail_service.py # Approval flow
│   │   ├── transcript.py    # Supadata + Gemini fallback
│   │   ├── comment_scraper.py
│   │   ├── thumbnail_describer.py
│   │   ├── tts_edge.py      # Edge TTS (free)
│   │   ├── image_replicate.py # Replicate Flux Dev ($0.03/img)
│   │   ├── video_render.py  # FFmpeg in-process
│   │   ├── youtube_upload.py # google-api-python-client
│   │   └── notification.py  # Gmail alerts
│   ├── agents/
│   │   └── agent_runner.py  # Sequential agent chain
│   ├── utils/
│   │   └── retry.py
│   └── tests/
├── scripts/
│   ├── get_youtube_refresh_token.py
│   └── reset_video.sql
└── config/
    └── settings.py          # Env validation, fail-fast
```

## Agent prompt requirements (stored in yt_agent_prompts)

### agent1_analyzer
- Extract viral patterns from transcript + comments
- Identify hook structure, retention tactics, emotional triggers
- Output: JSON with viral_elements, audience_profile, content_gaps

### agent2_strategist
- Generate 3 title options (ranked by CTR potential)
- SEO-optimized description with timestamps
- 15-20 tags (mix of high-volume + long-tail)
- Thumbnail brief: exact text overlay, color scheme, emotion
- Category: 27 (Education) for algorithm favor
- Output: JSON with title, description, tags, thumbnail_brief, category_id

### agent3_script_writer
- 5-beat viral hook in first 30 seconds
- 80-120 sentences, 8-10 minute target
- Pattern interrupt every 60-90 seconds
- Open loops ("but here's what most people miss...")
- End with strong CTA + tease next video
- Output: numbered sentences with section markers

### agent4_optimizer
- Pacing pass: no sentence > 25 words
- Remove filler, tighten transitions
- Verify hook lands in first 3 sentences
- Output: final numbered sentences

### image_generator (prompt template, not an agent)
- Cinematic 16:9 photoreal OR editorial illustration
- Consistent style anchor: "digital art, clean composition, cinematic lighting"
- No text in images, no hands, no faces looking at camera
- Scene description transformed from script by Haiku before generation

### thumbnail_style
- Bold text overlay (3-5 words max)
- High contrast, saturated colors
- Face or dramatic scene (no generic stock feel)
- Mobile-first: readable at 120px thumbnail size

## Render specification

### Timing
- Each sentence audio: 3-8 seconds (Edge TTS natural pacing)
- Image holds for duration of its paired sentences
- 0.3s crossfade between slides (xfade filter)
- No Ken Burns. Clean cuts with subtle fade.

### Quality
- 1280×720 (720p) — YouTube processes faster, good enough for monetization
- libx264, CRF 23, preset medium
- AAC 192kbps, 44100 Hz
- CFR 30fps
- Total: 8-10 minutes, 80-150 MB

### Transitions
- xfade filter with fade type between segments
- NOT zoompan (caused the Parkinson's shake)
- NOT static holds (caused the 40s bore)

## Deployment

1. `git init && git add . && git commit`
2. Connect to Railway project splendid-charisma
3. Set env vars from .env.example
4. Run scripts/get_youtube_refresh_token.py locally → select MindSeam channel
5. Push to Railway → auto-deploy
6. Verify: POST /health returns 200
7. Wait for 14:00 UTC discover_daily cron OR POST /api/trigger-discovery

## Success criteria
- [ ] Video renders in < 15 minutes
- [ ] Total cost < $2.00 per video
- [ ] 8-10 minute runtime
- [ ] Crossfade transitions, no static holds > 8s
- [ ] Scroll-stopping thumbnail uploaded
- [ ] Full SEO metadata (title, desc, tags, category 27)
- [ ] Uploaded to MindSeam (not WiredDifferentYT)
- [ ] Gmail notification with YouTube link
- [ ] No Vertex AI charges
- [ ] Pipeline runs unattended after approval email
