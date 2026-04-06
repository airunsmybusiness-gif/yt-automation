"""Batch status webhook + stale pipeline detector."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["batch"])


class BatchStatusPayload(BaseModel):
    """Payload from Vertex AI batch job completion webhook."""
    batch_job_name: str
    status: str  # "completed" or "failed"
    images_generated: int = 0
    images_failed: int = 0


@router.post("/status")
async def batch_status_webhook(payload: BatchStatusPayload, request: Request) -> dict[str, str]:
    """Receive batch job completion notification from Vertex AI or polling.

    Updates yt_batch_jobs status and triggers downstream pipeline steps.
    """
    try:
        supabase = request.app.state.supabase_client
    except AttributeError:
        return {"error": "App state not initialized"}

    logger.info(
        "Batch status update: %s → %s (gen=%d, fail=%d)",
        payload.batch_job_name, payload.status,
        payload.images_generated, payload.images_failed,
    )

    # Update batch job record
    update_data: dict[str, Any] = {
        "status": payload.status,
        "images_generated": payload.images_generated,
        "images_failed": payload.images_failed,
    }
    if payload.status in ("completed", "failed"):
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

    supabase.table("yt_batch_jobs").update(
        update_data
    ).eq("batch_job_name", payload.batch_job_name).execute()

    return {"status": "ok", "batch_job_name": payload.batch_job_name}


@router.get("/stale")
async def detect_stale_pipelines(request: Request) -> dict[str, Any]:
    """Detect pipelines stuck in production_started for >24 hours.

    Returns list of stale video records for manual review.
    """
    try:
        supabase = request.app.state.supabase_client
    except AttributeError:
        return {"error": "App state not initialized"}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    resp = (
        supabase.table("yt_viral_videos")
        .select("id, video_id, title, status, production_started_at, production_notes")
        .eq("status", "production_started")
        .lt("production_started_at", cutoff)
        .execute()
    )

    stale = resp.data or []
    if stale:
        logger.warning("Found %d stale pipelines (>24h in production_started)", len(stale))

    return {
        "stale_count": len(stale),
        "stale_videos": [
            {
                "id": v["id"],
                "video_id": v["video_id"],
                "title": v.get("title", "")[:60],
                "started_at": v.get("production_started_at"),
                "notes": v.get("production_notes", "")[:200],
            }
            for v in stale
        ],
    }
