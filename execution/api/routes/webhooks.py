"""Webhook routes — manual video URL submission and health checks."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from execution.api.middleware.auth import verify_api_key
from execution.api.middleware.rate_limit import webhook_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["webhooks"])


class SubmitURLRequest(BaseModel):
    """Request body for manual video URL submission."""
    url: str = Field(..., description="YouTube video URL or video ID")


class SubmitURLResponse(BaseModel):
    """Response for video submission."""
    status: str
    video_id: str | None = None
    message: str


# These get injected at app startup via app.state
def _get_deps(request: Any) -> tuple[Any, Any]:
    """Extract supabase client and settings from app state."""
    app = request.app
    return app.state.supabase_client, app.state.settings


@router.get("/version")
async def version():
    return {"commit": "6e591e1"}


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for Railway."""
    return {"status": "ok", "service": "yt-automation"}


@router.post("/submit-url", response_model=SubmitURLResponse)
async def submit_url(
    body: SubmitURLRequest,
    request: Request,
    _auth: str = Depends(verify_api_key),
) -> SubmitURLResponse:
    """Manually submit a YouTube video URL for processing.

    Fetches video metadata, checks viral threshold, inserts to DB,
    and sends approval email.
    """
    # Rate limit by client IP
    client_ip = request.client.host if request.client else "unknown"
    webhook_limiter.check(client_ip)

    from execution.services.youtube_api import fetch_single_video
    from execution.services.gmail_service import send_approval_email
    from execution.services.supabase_client import update_viral_video
    try:
        app_state = request.app.state
        supabase_client = app_state.supabase_client
        settings = app_state.settings
    except AttributeError:
        raise HTTPException(status_code=500, detail="App state not initialized")

    try:
        record = fetch_single_video(supabase_client, settings, body.url)
    except Exception as e:
        logger.error("Failed to fetch video %s: %s", body.url, e)
        raise HTTPException(status_code=400, detail=str(e))

    if record is None:
        return SubmitURLResponse(
            status="skipped",
            message="Video already exists or does not meet criteria",
        )

    # Send approval email
    try:
        thread_id = send_approval_email(settings, record)
        update_viral_video(
            supabase_client,
            record["id"],
            {"thread_id": thread_id},
        )
    except Exception as e:
        logger.error("Failed to send approval email: %s", e)
        # Video is inserted, email just failed — non-fatal

    return SubmitURLResponse(
        status="queued",
        video_id=record.get("video_id"),
        message="Video queued for approval",
    )
