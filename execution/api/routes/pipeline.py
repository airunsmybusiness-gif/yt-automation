"""Pipeline routes — trigger data collection + agent pipeline."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from execution.api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/pipeline",
    tags=["pipeline"],
    dependencies=[Depends(verify_api_key)],
)


class TriggerResponse(BaseModel):
    """Response for pipeline trigger."""
    status: str
    video_id: str | None = None
    message: str


@router.post("/trigger/{video_record_id}", response_model=TriggerResponse)
async def trigger_pipeline(video_record_id: str, request: Request) -> TriggerResponse:
    """Manually trigger the pipeline for a specific approved video.

    This runs synchronously (blocking). For production, consider
    offloading to a background task queue.
    """
    from orchestration.pipeline import run_pipeline_for_video

    try:
        supabase_client = request.app.state.supabase_client
        settings = request.app.state.settings
    except AttributeError:
        raise HTTPException(status_code=500, detail="App state not initialized")

    # Fetch the video
    resp = (
        supabase_client.table("yt_viral_videos")
        .select("*")
        .eq("id", video_record_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Video record not found")

    video = resp.data[0]

    if not video.get("suitable"):
        raise HTTPException(
            status_code=400,
            detail="Video not approved (suitable != true)",
        )

    if video.get("status") != "queued":
        raise HTTPException(
            status_code=400,
            detail=f"Video already in status '{video.get('status')}', must be 'queued'",
        )

    success = run_pipeline_for_video(supabase_client, settings, video)

    if success:
        return TriggerResponse(
            status="completed",
            video_id=video.get("video_id"),
            message="Pipeline completed successfully",
        )
    else:
        return TriggerResponse(
            status="failed",
            video_id=video.get("video_id"),
            message="Pipeline failed — check logs and production_notes",
        )


@router.post("/trigger-all", response_model=list[TriggerResponse])
async def trigger_all_approved(request: Request) -> list[TriggerResponse]:
    """Trigger the pipeline for all approved + queued videos.

    Processes one video at a time to avoid resource contention.
    """
    from execution.services.supabase_client import get_approved_videos
    from orchestration.pipeline import run_pipeline_for_video

    try:
        supabase_client = request.app.state.supabase_client
        settings = request.app.state.settings
    except AttributeError:
        raise HTTPException(status_code=500, detail="App state not initialized")

    approved = get_approved_videos(supabase_client)
    if not approved:
        return [TriggerResponse(status="skipped", message="No approved videos pending")]

    results: list[TriggerResponse] = []
    for video in approved:
        success = run_pipeline_for_video(supabase_client, settings, video)
        results.append(TriggerResponse(
            status="completed" if success else "failed",
            video_id=video.get("video_id"),
            message="OK" if success else "Pipeline failed",
        ))

    return results
