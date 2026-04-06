# YT Automation Pipeline — CLAUDE.md

## What This Is
Fully automated faceless YouTube channel pipeline. Discovers viral videos → approval email → 4 AI agents → TTS → images → FFmpeg → YouTube upload. Zero manual steps after approval.

## Architecture: DOE
- `directives/` — plain-English SOPs (no code)
- `orchestration/` — Claude SDK routing (never computes)
- `execution/` — deterministic FastAPI endpoints, services, cron jobs

## Key Files
- `execution/api/main.py` — FastAPI app entry point with APScheduler
- `execution/services/youtube_api.py` — viral discovery + key rotation
- `execution/services/gmail_service.py` — approval flow
- `execution/services/supabase_client.py` — typed DB wrapper
- `config/settings.py` — env validation, fail-fast

## Database
All tables prefixed `yt_`. Key table: `yt_viral_videos` (lifecycle: queued → production_started → done). Schema in `/mnt/project/schema.sql`.

## Running
```bash
# Local
cp .env.example .env  # fill in values
pip install -r requirements.txt
python -m execution.api.main

# Tests
pytest
```

## Standards
- Type hints all signatures
- `logging` not `print`
- `pathlib.Path` always
- `os.environ[]` for secrets, fail at startup
- Max 50-line functions
- Specific exceptions, never bare except
- Self-anneal: error → patch → update SOP → re-test
