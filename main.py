import logging
import os
import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from orchestration.pipeline import Pipeline
from orchestration.scheduler import register_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main")

# ── Fail-fast on missing secrets ────────────────────────────────────────────
REQUIRED = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "GEMINI_API_KEY",
    "GCP_PROJECT_ID",
    "ANTHROPIC_API_KEY",
    "API_SECRET",
    "IMAGE_CF_URL",
    "GENERATE_VIDEO_CF_URL",
]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    log.error(f"Missing required env vars: {missing}")
    sys.exit(1)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline = Pipeline()
    register_jobs(scheduler, pipeline)
    scheduler.start()
    log.info("Scheduler started")
    yield
    scheduler.shutdown()
    log.info("Scheduler stopped")


app = FastAPI(title="YT Automation", lifespan=lifespan)

from api.routes import router  # noqa: E402 — after app created
app.include_router(router)





@app.get("/_debug/v1/status")
def _debug_v1_status() -> dict:
    import importlib.util, os
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    rows = sb.table("yt_viral_videos").select(
        "video_id,title,channel_username,status,suitable,production_notes"
    ).order("scraped_at", desc=True).limit(5).execute().data
    return {
        "anthropic": importlib.util.find_spec("anthropic") is not None,
        "ffmpeg": os.path.exists("/usr/bin/ffmpeg") or os.path.exists("/usr/local/bin/ffmpeg"),
        "recent_videos": rows,
    }


@app.post("/_debug/v1/discover")
def _debug_v1_discover() -> dict:
    from execution.viral_finder import discover_and_email
    return {"emails_sent": discover_and_email()}
