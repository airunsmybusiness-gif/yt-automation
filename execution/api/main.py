"""FastAPI application with APScheduler — main entry point for Railway."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from config.settings import Settings, load_settings
from execution.api.routes.webhooks import router as webhook_router
from execution.api.routes.pipeline import router as pipeline_router
from execution.api.routes.batch import router as batch_router
from execution.api.routes.status import router as status_router
from execution.api.middleware.auth import verify_api_key
from execution.api.middleware.rate_limit import api_limiter
from execution.services.gmail_service import (
    poll_approval_replies,
    send_approval_email,
    send_error_alert,
)
from execution.services.supabase_client import (
    create_supabase_client,
    get_queued_videos,
    reset_all_quotas,
    update_viral_video,
)
from execution.services.youtube_api import discover_viral_videos

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler instance
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def job_discover_videos() -> None:
    """Cron job: discover viral videos every 12 hours."""
    logger.info("=== DISCOVERY CRON START ===")
    try:
        from execution.api.main import _app_settings, _supabase

        new_videos = discover_viral_videos(_supabase, _app_settings)
        logger.info("Discovery found %d new videos", len(new_videos))

        # Send approval email for each new video
        for video in new_videos:
            try:
                thread_id = send_approval_email(_app_settings, video)
                update_viral_video(
                    _supabase, video["id"], {"thread_id": thread_id}
                )
            except Exception as e:
                logger.error(
                    "Failed to send approval for video %s: %s",
                    video.get("video_id"), e,
                )

    except Exception as e:
        logger.error("Discovery cron failed: %s", e, exc_info=True)
        try:
            send_error_alert(
                _app_settings, "Discovery Cron Failed", str(e)
            )
        except Exception:
            logger.error("Could not send error alert")


async def job_poll_emails() -> None:
    """Poll Gmail for approval replies every 60 seconds."""
    try:
        from execution.api.main import _app_settings, _supabase

        updated = poll_approval_replies(_supabase, _app_settings)
        if updated > 0:
            logger.info("Processed %d approval replies", updated)
    except Exception as e:
        logger.error("Email poll failed: %s", e)


async def job_reset_quotas() -> None:
    """Reset YouTube API quotas daily at 08:00 UTC."""
    logger.info("=== QUOTA RESET CRON ===")
    try:
        from execution.api.main import _supabase

        count = reset_all_quotas(_supabase)
        logger.info("Reset quotas for %d accounts", count)
    except Exception as e:
        logger.error("Quota reset failed: %s", e)


async def job_process_approved() -> None:
    """Check for approved videos and run the pipeline. Every 2 minutes."""
    try:
        from execution.api.main import _app_settings, _supabase
        from execution.services.supabase_client import get_approved_videos
        from orchestration.pipeline import run_pipeline_for_video

        approved = get_approved_videos(_supabase)
        if not approved:
            return

        # Process one video at a time
        video = approved[0]
        logger.info("Auto-triggering pipeline for approved video %s", video["video_id"])
        run_pipeline_for_video(_supabase, _app_settings, video)

    except Exception as e:
        logger.error("Auto-pipeline trigger failed: %s", e)


async def job_detect_stale() -> None:
    """Detect and alert on stale pipelines. Every 6 hours."""
    try:
        from datetime import timedelta
        from execution.api.main import _app_settings, _supabase

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()

        resp = (
            _supabase.table("yt_viral_videos")
            .select("id, video_id, title, production_started_at")
            .eq("status", "production_started")
            .lt("production_started_at", cutoff)
            .execute()
        )
        stale = resp.data or []
        if stale:
            logger.warning("ANNEALING: %d stale pipelines detected", len(stale))
            video_list = "\n".join(
                f"- {v.get('video_id')}: {v.get('title', '')[:50]} (started {v.get('production_started_at')})"
                for v in stale
            )
            send_error_alert(
                _app_settings,
                f"Stale Pipelines: {len(stale)} videos stuck",
                f"These videos have been in production_started for >24h:\n\n{video_list}",
            )
    except Exception as e:
        logger.error("Stale pipeline detection failed: %s", e)


# ---------------------------------------------------------------------------
# Module-level state (set during lifespan)
# ---------------------------------------------------------------------------
_app_settings: Settings = None  # type: ignore[assignment]
_supabase = None


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init services and scheduler on startup."""
    global _app_settings, _supabase

    logger.info("=== YT AUTOMATION PIPELINE STARTING ===")

    # Decode GCP service account JSON from base64 env var if present
    gcp_b64 = os.environ.get("GCP_SERVICE_ACCOUNT_JSON_BASE64")
    if gcp_b64:
        import base64
        from pathlib import Path
        sa_path = Path("/app/service-account.json")
        sa_path.write_bytes(base64.b64decode(gcp_b64))
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(sa_path)
        logger.info("Decoded GCP service account JSON to %s", sa_path)

    # Load settings (fails fast on missing env vars)
    _app_settings = load_settings()

    # Connect Supabase
    _supabase = create_supabase_client(_app_settings)

    # Inject into app state for route access
    app.state.settings = _app_settings
    app.state.supabase_client = _supabase

    # Schedule jobs
    scheduler.add_job(
        job_discover_videos,
        IntervalTrigger(hours=_app_settings.discovery_interval_hours),
        id="discover_videos",
        name="Viral Video Discovery",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_poll_emails,
        IntervalTrigger(seconds=_app_settings.email_poll_interval_seconds),
        id="poll_emails",
        name="Gmail Approval Polling",
        misfire_grace_time=30,
    )
    scheduler.add_job(
        job_reset_quotas,
        CronTrigger(hour=_app_settings.quota_reset_hour_utc, minute=0),
        id="reset_quotas",
        name="YouTube API Quota Reset",
    )
    scheduler.add_job(
        job_process_approved,
        IntervalTrigger(seconds=120),
        id="process_approved",
        name="Process Approved Videos",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        job_detect_stale,
        IntervalTrigger(hours=6),
        id="detect_stale",
        name="Stale Pipeline Detector",
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: discovery=%dh, email_poll=%ds, quota_reset=%02d:00 UTC",
        _app_settings.discovery_interval_hours,
        _app_settings.email_poll_interval_seconds,
        _app_settings.quota_reset_hour_utc,
    )

    # Run initial discovery on startup
    logger.info("Running initial discovery on startup...")
    await job_discover_videos()

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("=== YT AUTOMATION PIPELINE STOPPED ===")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="YT Automation Pipeline",
    description="Automated faceless YouTube channel pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)
app.include_router(pipeline_router)
app.include_router(batch_router)
app.include_router(status_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "execution.api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
