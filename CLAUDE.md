# MindSeam Pipeline v2 — CLAUDE.md

## What this is
Fully automated faceless YouTube channel for psychology/self-improvement.
Discovers viral videos → approval email → 4 AI agents → Edge TTS → Replicate images →
FFmpeg render → YouTube upload. One Railway process, no Cloud Functions.

## Budget: $2/video, nothing else
- Anthropic agents: ~$0.40
- Replicate images: ~$1.50 (50 × $0.03)
- Edge TTS: free
- Railway: ~$0.10
- Everything else: $0

## Architecture: DOE
- `directives/` — SOPs, quality spec, agent prompt docs
- `orchestration/pipeline.py` — main process_video() function
- `execution/` — all services, agents, API

## Key files
- `execution/api/main.py` — FastAPI + APScheduler entry point
- `orchestration/pipeline.py` — full pipeline orchestrator
- `execution/agents/agent_runner.py` — 4-agent chain
- `execution/services/tts_edge.py` — Edge TTS (1 audio per sentence)
- `execution/services/image_replicate.py` — Replicate Flux Dev (cost-capped)
- `execution/services/video_render.py` — FFmpeg render (crossfade)
- `execution/services/youtube_upload.py` — YouTube Data API upload
- `config/settings.py` — env validation

## Database
Supabase project: pohozvmvxlskqbklsosr
Key table: yt_viral_videos (lifecycle: queued → production_started → done)
Agent prompts: yt_agent_prompts (agent1_analyzer through agent4_optimizer)
Scripts: yt_scripts (sentence_number is universal sync key)

## What NOT to do
- NO Vertex AI (caused $124 bill from runaway batch jobs)
- NO Cloud Functions (single process is simpler to debug)
- NO Ken Burns zoompan (caused Parkinson's shake on LOzrFoSHnGA)
- NO GCS buckets per video (use local /tmp, clean after upload)
- NO n8n (failed 5-day detour, produced zero videos)
- NO Gemini TTS (robotic voice)
- NO `--set-env-vars` in gcloud (silently wipes existing vars)

## Debugging protocol
1. Read actual logs before proposing fixes
2. One change per deploy
3. Verify success before moving on
4. Never theorize when logs are available
