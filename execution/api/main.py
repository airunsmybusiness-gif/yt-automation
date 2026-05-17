"""FastAPI app with APScheduler — the single process that runs everything."""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from supabase import create_client

from config.settings import load_settings

# Configure logging before anything else
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Validate settings at startup (crash if missing)
settings = load_settings()
logger.info("Settings loaded. Supabase: %s", settings.supabase_url)

# Supabase client
supabase = create_client(settings.supabase_url, settings.supabase_service_key)

# FastAPI app
app = FastAPI(title="MindSeam Pipeline v2")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def _uploaded_within_24h() -> bool:
    """Check if we've already uploaded a video in the last 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    resp = (
        supabase.table("yt_viral_videos")
        .select("id")
        .eq("status", "done")
        .gte("production_completed_at", cutoff)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def process_next() -> None:
    """Pick up the next approved video and run the full pipeline."""
    if _uploaded_within_24h():
        return

    # Find oldest approved, unprocessed video
    resp = (
        supabase.table("yt_viral_videos")
        .select("*")
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if not resp.data:
        return

    video = resp.data[0]
    logger.info("process_next: picked up %s", video["video_id"])

    from orchestration.pipeline import process_video
    process_video(supabase, settings, video)


def discover_daily() -> None:
    """Discover viral videos from competitors + keywords."""
    logger.info("discover_daily: starting")
    try:
        from execution.services.viral_finder import discover_viral_videos
        discover_viral_videos(supabase, settings)
    except Exception as e:
        logger.error("Discovery failed: %s", e, exc_info=True)


def poll_approvals() -> None:
    """Check Gmail for approval replies on pending videos."""
    try:
        from execution.services.email_approval_poller import poll_approval_emails
        poll_approval_emails(supabase, settings)
    except Exception as e:
        logger.error("Approval polling failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler()
scheduler.add_job(process_next, "interval", minutes=2, id="process_next")
scheduler.add_job(discover_daily, "cron", hour=14, minute=0, id="discover_daily")
scheduler.add_job(poll_approvals, "interval", seconds=60, id="poll_approvals")


@app.on_event("startup")
def startup() -> None:
    scheduler.start()
    logger.info("Scheduler started: process_next(2m), discover_daily(14:00), poll_approvals(60s)")


@app.on_event("shutdown")
def shutdown() -> None:
    scheduler.shutdown()
    logger.info("Scheduler stopped")


# ---------------------------------------------------------------------------
# Manual trigger endpoints
# ---------------------------------------------------------------------------

@app.post("/api/trigger-discovery")
def trigger_discovery() -> dict:
    """Manually trigger viral video discovery."""
    discover_daily()
    return {"status": "discovery triggered"}


@app.post("/api/trigger-pipeline/{video_id}")
def trigger_pipeline(video_id: str) -> dict:
    """Manually trigger pipeline for a specific video."""
    resp = (
        supabase.table("yt_viral_videos")
        .select("*")
        .eq("video_id", video_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return {"error": f"Video {video_id} not found"}

    try:
        from orchestration.pipeline import process_video
        logger.info("TRIGGER: starting pipeline for %s", video_id)
        success = process_video(supabase, settings, resp.data[0])
        logger.info("TRIGGER: pipeline result=%s", success)
        return {"status": "complete" if success else "failed", "video_id": video_id}
    except Exception as e:
        logger.error("TRIGGER CRASH: %s", e, exc_info=True)
        return {"status": "crashed", "error": str(e), "video_id": video_id}
