# Session Handoff — Railway Config & Next Steps

## Railway Deployment

- **Project:** gracious-achievement
- **Service:** luminous-endurance

## Working Environment Variables

| Variable | Value |
|---|---|
| `OPENAI_API_KEY` | (set in Railway — confirmed working) |
| `YOUTUBE_CLIENT_ID` | `90531216094-go2rs2befg77l36a7pb9ucnq6kmd4njm.apps.googleusercontent.com` |
| `YOUTUBE_CLIENT_SECRET` | (set in Railway — confirmed working) |
| `YOUTUBE_REFRESH_TOKEN` | (set in Railway — confirmed working) |
| `SUPABASE_URL` | `https://ksrqeuhwmjbptcfkotuz.supabase.co` |
| `API_SECRET` | `yt-auto-2026-secure-key` |

## Code Location

Local: `~/Desktop/nsf-fresh`

## Last Working Video

https://www.youtube.com/watch?v=IRyVtp4Bvo0

## Quality Improvements Needed (Next Session)

Stack: FastAPI + OpenAI TTS + ffmpeg + YouTube Data API

Three things to tune — everything is wired up, only quality changes needed:

1. **Script prompt rewrite** — current output is boring/flat; needs hooks, storytelling structure, narrative tension
2. **Ken Burns effect** — apply zoom/pan motion to every image frame via ffmpeg (currently static images)
3. **Voice energy** — faster, more energetic TTS (adjust speed/model params in OpenAI TTS call)

## Paste This Into the New Chat

> I have a working YouTube automation pipeline deployed on Railway (luminous-endurance, project gracious-achievement). It produces videos end-to-end but they're low quality — boring script, flat voice, static images. Code is at ~/Desktop/nsf-fresh. I need: (1) complete script prompt rewrite with hooks and storytelling, (2) Ken Burns zoom effect on every frame, (3) faster more energetic voice. The pipeline uses FastAPI + OpenAI TTS + ffmpeg + YouTube Data API.
