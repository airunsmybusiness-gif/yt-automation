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



@app.post("/_debug/v1/mark_unsuitable")
def _debug_v1_mark_unsuitable(video_id: str) -> dict:
    """Mark a YouTube video_id unsuitable and clear any stored transcript."""
    import os
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    row = sb.table("yt_viral_videos").select("id").eq("video_id", video_id).execute().data
    if not row:
        return {"error": f"video_id {video_id} not found"}
    vid_uuid = row[0]["id"]
    sb.table("yt_video_transcripts").delete().eq("video_record_id", vid_uuid).execute()
    sb.table("yt_viral_videos").update({
        "status": "failed",
        "suitable": False,
        "transcript_status": "no_transcript",
        "production_notes": "No public captions; skipped",
    }).eq("id", vid_uuid).execute()
    return {"cleaned": video_id, "uuid": vid_uuid}



@app.post("/_debug/v1/seed_channels")
def _debug_v1_seed_channels() -> dict:
    """Insert mid-tier psych/self-improvement channels known to leave captions on."""
    import os
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    channels = [
        ("@Psych2go", "UCkJEpR7JmS36tajD34Gp4VA"),
        ("@TherapyinaNutshell", "UCpuqYFKLkcEryEieomiAv3Q"),
        ("@HealthyGamerGG", "UClHVl2N3jPEbkNJVx-ItQIQ"),
        ("@KatiMorton", "UCzBYOHyEEzlkRdDOSobbpvw"),
        ("@DrTraceyMarks", "UCKGie8VsdNaqX5p-W-tk04Q"),
        ("@TheMindfulMovement", "UCh9G-_BCmLYsq5W3pQAaung"),
        ("@MedCircle", "UCEJoRvFCdkPGiv3IZyhDM2g"),
        ("@PsychologyAndYou", "UCDkx66MZD2Z5y8gnrrvOG_g"),
        ("@DoctorMike", "UC0QHWhjbe5fGJEPz3sVb6nw"),
        ("@TheSchoolofLife", "UC7IcJI8PUf5Z3zKxnZvTBog"),
    ]
    rows = [{
        "channel_username": u,
        "channel_id": cid,
        "is_active": True,
    } for u, cid in channels]
    sb.table("yt_competitors").upsert(rows, on_conflict="channel_username").execute()
    return {"seeded": len(rows), "channels": [u for u, _ in channels]}
