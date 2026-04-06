# Phase 1: Infrastructure + Discovery + Approval — Directive

## Objective
Stand up the Railway service with FastAPI, connect to existing Supabase schema, discover viral videos on a 12-hour cron, send approval emails via Gmail API, and process yes/no replies to gate the pipeline.

## Inputs
- Supabase: yt_competitors, yt_search_keywords, yt_api_accounts, yt_workflow_settings
- YouTube Data API v3 (multiple API keys with quota tracking)
- Gmail API (OAuth2 credentials)

## Outputs
- New rows in yt_viral_videos (status: queued, suitable: null)
- Approval emails sent with video link + stats
- On "yes" reply: suitable=true, ready for Phase 2 pipeline
- On "no" reply: suitable=false, pipeline stops

## Constraints
- YouTube API: 10,000 units/day per key. Rotate keys on exhaustion.
- Gmail polling: every 60 seconds. Match by thread_id, not subject line.
- Viral threshold: views > 7000 within 48h OR > 4000 within 12h.
- Never re-queue a video that already exists in yt_viral_videos (deduplicate by video_id).
- Quota reset cron at 08:00 UTC daily resets all yt_api_accounts.quota_exhausted = false.

## Steps (High-Level)
1. Boot FastAPI app → validate all env vars → connect Supabase → start APScheduler
2. Every 12h: load competitors + keywords → query YouTube API → filter by viral threshold → deduplicate → insert to yt_viral_videos → send approval email per video
3. Every 60s: poll Gmail for replies to approval threads → match thread_id → update suitable flag
4. On manual POST /api/submit-url: fetch video metadata → apply threshold → insert + send email

## Edge Cases
| Scenario | Handling |
|---|---|
| All API keys exhausted | Log warning, skip discovery cycle, email alert |
| Gmail API rate limit | Backoff 30s, retry 3x |
| Duplicate video_id | Skip silently, log at DEBUG |
| Video deleted/private | Log warning, mark suitable=false |
| Empty competitor list | Fall through to keyword search only |
| No keywords configured | Skip keyword search, competitors only |
| Thread_id mismatch | Ignore reply, log at DEBUG |
| Reply is not "yes"/"no" | Ignore, log body at WARNING |

## Definition of Done
- [ ] Service boots in <5s, logs all config (redacted secrets)
- [ ] 12h cron fires, discovers videos, inserts to DB
- [ ] Approval email arrives with video title, link, views, age
- [ ] "yes" reply → suitable=true within 60s
- [ ] "no" reply → suitable=false within 60s
- [ ] Manual URL submission works via POST
- [ ] API key rotation works when quota exhausted
- [ ] All edge cases handled without crashes

## Self-Annealing Rules
- On YouTube API 403: mark key exhausted, rotate, log ANNEALING
- On Gmail send failure: retry 3x, then email alert via fallback SMTP
- On Supabase connection error: retry with backoff, crash if persistent (Railway auto-restarts)
- Weekly: grep logs for ANNEALING, update this directive with new edge cases
