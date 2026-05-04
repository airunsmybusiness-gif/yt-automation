# yt-automation HANDOFF — 2026-05-03 (Autonomy Layer Shipped)

## Status: FULLY AUTONOMOUS PIPELINE LIVE

### Tonight's two milestones
1. First end-to-end automated upload — video CDJHuUPcfUk on WiredDifferentYT
2. Autonomy layer added — daily discovery cron + email approval flow + 24h upload cap

## Daily flow (zero manual queueing)

1. **08:00 CST (14:00 UTC)** — Pipeline runs viral discovery on YouTube
   - Searches 8 active keywords
   - Filters by viral threshold: >=7000 views/48h OR >=4000 views/12h
   - Picks ONE highest-view candidate
   - Inserts to yt_viral_videos with status=queued, suitable=NULL
   - Sends email to lilibethsejera@gmail.com with subject "[YT Auto] Approve: ..."

2. **You reply YES or NO from your phone** (5 sec, on coffee)
   - poll_approvals job (60s cadence) reads Gmail, parses reply, sets suitable=true/false

3. **process_next job (every 2 min)** picks up the approved video
   - Hard cap: if any video uploaded in last 24h, skip this run entirely
   - Otherwise, runs full agent pipeline → render → YouTube upload (Private)
   - Total time: ~12-15 min from approval to private upload

4. **You review the private upload in YouTube Studio**, flip to Public if quality holds

## New files (autonomy layer)
- execution/viral_finder.py — daily YouTube discovery + insert + email send
- execution/email_sender.py — Gmail API send (uses GMAIL_*_JSON env vars)
- execution/email_approval_poller.py — polls Gmail every 60s for replies
- scripts/get_gmail_credentials.py — one-shot Gmail OAuth helper (re-mint anytime)

## New env vars on Railway
- GMAIL_CREDENTIALS_JSON — OAuth client config
- GMAIL_TOKEN_JSON — refresh token (auto-refreshes itself)
- GMAIL_SENDER_EMAIL — lilibethsejera@gmail.com
- GMAIL_APPROVAL_TO — lilibethsejera@gmail.com

## Three running APScheduler jobs
| Job | Cadence | Purpose |
|---|---|---|
| process_next | every 2 min | Pick up approved videos, run pipeline |
| discover_daily | 14:00 UTC daily | Find one viral candidate, email for approval |
| poll_approvals | every 60s | Read Gmail replies, update suitable flag |

## Hard 1/day upload cap
pipeline._uploaded_within_24h() — checks for any yt_viral_videos row with
status='done' and production_completed_at within last 24h. If yes, skip.
Means you can YES multiple emails without uploading multiple videos.

## Publishing schedule (Lily's plan)
Sunday → Tuesday → Friday → Sunday (4 videos/week max)
You manually choose which days to flip Private → Public in YouTube Studio.

## Cost (unchanged from earlier ship)
~$1.50/video, ~$23/month at 12 videos.

## Active channel
WiredDifferentYT (current YOUTUBE_REFRESH_TOKEN binding).
To switch to MindSeam: re-run scripts/get_youtube_refresh_token.py
and select MindSeam on the "Choose a channel" page during OAuth.

## Test commands

Manual smoke-test the email send (works locally with `railway run`):
```bash
railway run python3 -c "
from execution.email_sender import send_approval_email
print(send_approval_email({
    'id': 'test', 'title': 'TEST', 'channel_title': 'X',
    'views': 1, 'age_hours': 1, 'url': 'https://x.com',
}))
"
```

Manually trigger discovery without waiting for 14:00 UTC:
```bash
railway run python3 -c "
from execution.viral_finder import discover_and_email
print(f'Sent: {discover_and_email()}')
"
```

Manually reset a stuck row:
```sql
UPDATE yt_viral_videos
SET status = 'queued', suitable = true,
    production_started_at = NULL, production_completed_at = NULL,
    production_notes = NULL, transcript_status = 'completed',
    comments_status = 'completed'
WHERE id = '<uuid>';
```

## Parked for next session
1. Switch active channel to MindSeam (re-mint YouTube token, choose MindSeam)
2. Image quality — bump cap from 30 to 60 if Replicate credit allows
3. Processing-orphan recovery (auto-reset stuck rows older than 30 min)
4. Watch first 3 auto-discovered videos to validate pickup quality
5. Tune viral_threshold values if discovery returns nothing for several days

## Git tip on origin/main
01fc3ff autonomy layer: daily discovery cron + Gmail approval poller + 24h upload cap
b28f4a1 drop unused youtube.readonly scope
0450750 fix filename contract: NNNN.jpg
faacb6e fix Replicate image save: bytes + size validation
f36db8e fix KeyError: 'failure_count' -> 'failed'
9913fee Anthropic passthrough shim + claude-haiku-4-5
f27c8be swap Groq -> Anthropic

## What to do tomorrow
1. ☕ Check inbox at 08:00 CST for the first auto-discovery email
2. Reply YES if it looks good
3. Wait ~15 min, video appears as Private on WiredDifferentYT
4. Watch 30 sec, decide if good enough
5. Flip to Public if yes
6. Repeat Tuesday, Friday, Sunday
