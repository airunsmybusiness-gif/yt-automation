# yt-automation HANDOFF — 2026-04-28

## Status: working pipeline, last quality fix in flight

### What works (DO NOT TOUCH)
- Railway cron polls Supabase every 2min for queued videos
- Edge TTS audio generation (free)
- Cloudflare Flux Schnell image generation (free, capped at 30/video)
- Custom title-card thumbnail via Cloudflare
- Strategist agent generates SEO title/description/tags via Sonnet
- YouTube upload as Private with metadata + thumbnail
- One Public video shipped: youtube.com/watch?v=kQoDi9vSZws

### Last commit pending push
`transform script sentences via Claude before image generation`
- Adds `_scene_from_sentences()` method (Sonnet call, ~$0.06/video)
- Both image-job creation paths now transform raw script → literal stick-figure scene description
- pipeline.py lines 232 + 261 call it
- image_generator Supabase prompt rewritten (version 2) to take {{sentence}} input

### Open question
Will Claude transformation + tightened Cloudflare style rails produce coherent stick figures? Last visual test showed a busy collage of cone-shaped figures (incoherent). Theory: prompts were raw script sentences, not visual descriptions. New code fixes that.

### Key files
- orchestration/pipeline.py — main DOE orchestrator
- execution/cloudflare_images.py — has STYLE_PREFIX / _wrap_prompt rails
- execution/video_render.py — thumbnail uses cloudflare_images too
- execution/openrouter_images.py — kept but UNUSED (account hit $10 cap)

### Test video
viral_video_id = 260372c9-e989-427a-88f9-5be63594f3e3
"Every stage of self improvement explained"

### Budget reality
- Cloudflare images: $0
- Sonnet calls (metadata + scene transform): ~$1/month for 12 videos
- OpenRouter: locked at $10 cap, unusable until topped up

### Re-queue SQL (run in Supabase SQL Editor)
```sql
DELETE FROM yt_image_generation_jobs WHERE viral_video_id = '260372c9-e989-427a-88f9-5be63594f3e3';
DELETE FROM yt_audio_files WHERE viral_video_id = '260372c9-e989-427a-88f9-5be63594f3e3';
DELETE FROM yt_scripts WHERE viral_video_id = '260372c9-e989-427a-88f9-5be63594f3e3';
UPDATE yt_viral_videos
SET status = 'queued', suitable = true, production_started_at = NULL,
    production_completed_at = NULL, production_notes = NULL,
    transcript_status = 'no_transcript', comments_status = 'no_comments'
WHERE id = '260372c9-e989-427a-88f9-5be63594f3e3';
```

### Watch progress
```bash
railway logs --tail 30 2>&1 | grep -E "260372c9|Cloudflare Flux|Audio|Capped|Thumbnail|Rendered|YouTube|FAILED|ERROR" | tail -15
```
