# yt-automation HANDOFF — 2026-05-03

## Status: SHIPPED. End-to-end automated YouTube upload working.

### First successful video
- video_id: CDJHuUPcfUk
- viral source: 260372c9-e989-427a-88f9-5be63594f3e3 ("Every stage of self improvement")
- runtime: 12:28
- channel: WiredDifferentYT (wrong channel — OAuth token authorized this one instead of MindSeam)
- pipeline runtime: ~8 min cron pickup → YouTube live

### Stack confirmed working
- Railway: FastAPI + APScheduler (2-min poll)
- Supabase: yt_viral_videos as job queue, RLS off on test rows
- Anthropic Claude: Opus 4.7 (heavy agent calls), Haiku 4.5 (cheap helpers)
- Edge TTS: Microsoft, free, ~23 chunks per 177-sentence script
- Replicate Flux Dev: 30 images per video, throttled to 6/min while account credit < $5
- FFmpeg in-process: 177 slides rendered with image fallback for sentences past 30
- Cloudflare Workers AI: thumbnail only
- YouTube Data API: in-process upload via google-api-python-client + refresh token

### Current cost per video: ~$1.50
- Anthropic: $0.40 (4 agents + scene transform)
- Replicate: $0.90 (30 images @ $0.03)
- Edge TTS, Cloudflare, Google Cloud: $0
- Railway prorated: $0.17

### Monthly fixed: ~$23 at 12 videos/month

### Critical environment variables on Railway (do not lose these)
- ANTHROPIC_API_KEY
- REPLICATE_API_TOKEN
- SUPABASE_URL = https://pohozvmvxlskqbklsosr.supabase.co
- SUPABASE_KEY (service role)
- YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET
- YOUTUBE_REFRESH_TOKEN (currently bound to WiredDifferentYT — re-mint to switch channels)
- CLAUDE_MODEL = claude-opus-4-7 (default for heavy agent calls)
- CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
- GROQ_API_KEY (legacy, can be removed)

### Channel routing (next session decision)
The pipeline currently has ONE refresh token, binds to ONE channel.
lilibethsejera@gmail.com owns 2 channels: WiredDifferentYT + MindSeam.
To switch channel: re-run scripts/get_youtube_refresh_token.py and click the
correct channel on Google's "Choose a channel" page during OAuth.
To run multiple channels in parallel: add target_channel column + per-channel
refresh tokens (~30 min code change).

### Parked for next session (priority order)
1. Image quality — 30 images for 177 sentences = each image holds ~25s. Either:
   (a) bump cap to 60 (cost ~$1.80/video), or
   (b) refine Replicate STYLE_PREFIX to match the cleaner first-video aesthetic, or
   (c) buy $5 Replicate credit to escape 6/min throttling.
2. Channel routing decision (single-channel rotation vs. multi-channel parallel).
3. Processing-orphan recovery: if Railway container restarts mid-run, the row
   stays at status='processing' forever and cron skips it. Add 30-min timeout
   that auto-resets stuck rows back to 'queued'.
4. Single-worker enforcement: APScheduler runs in-process. If Railway scales
   to >1 worker, two workers could process the same video. Add Postgres
   advisory lock or pin to 1 replica.

### Test command for next session
```sql
UPDATE yt_viral_videos
SET status = 'queued', suitable = true,
    production_started_at = NULL, production_completed_at = NULL,
    production_notes = NULL,
    transcript_status = 'completed', comments_status = 'completed'
WHERE id = '<test-video-uuid>';
```

### Key files
- orchestration/pipeline.py — DOE orchestrator (process_next, agent calls, render)
- execution/gemini_text.py — Anthropic SDK shim (named for legacy reasons)
- execution/imagen_images.py — Replicate Flux Dev wrapper
- execution/cloudflare_images.py — thumbnail only
- execution/video_render.py — FFmpeg slide assembly
- execution/youtube_upload.py — refresh-token-based upload
- scripts/get_youtube_refresh_token.py — re-mint token for channel switch

### Git tip on origin/main
b28f4a1 drop unused youtube.readonly scope
0450750 fix filename contract: NNNN.jpg
faacb6e fix Replicate image save: bytes + size validation
f36db8e fix KeyError: 'failure_count' -> 'failed'
9913fee Anthropic passthrough shim + claude-haiku-4-5
f27c8be swap Groq -> Anthropic
