"""Status dashboard — pipeline health overview at a glance."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from execution.api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/status",
    tags=["status"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/dashboard")
async def dashboard(request: Request) -> dict[str, Any]:
    """Full pipeline health dashboard.

    Returns counts by status, recent activity, API key health,
    and active batch jobs.
    """
    supabase = request.app.state.supabase_client

    # Video status breakdown
    videos = supabase.table("yt_viral_videos").select("status, suitable").execute()
    status_counts: dict[str, int] = {}
    suitable_counts = {"approved": 0, "rejected": 0, "pending": 0}
    for v in videos.data or []:
        s = v.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
        if v.get("suitable") is True:
            suitable_counts["approved"] += 1
        elif v.get("suitable") is False:
            suitable_counts["rejected"] += 1
        else:
            suitable_counts["pending"] += 1

    # API key health
    keys = supabase.table("yt_api_accounts").select(
        "id, account_name, quota_exhausted, last_used"
    ).execute()
    key_health = [
        {
            "id": str(k["id"]),
            "name": k.get("account_name", ""),
            "exhausted": k.get("quota_exhausted", False),
            "last_used": k.get("last_used"),
        }
        for k in (keys.data or [])
    ]

    # Active batch jobs
    batches = (
        supabase.table("yt_batch_jobs")
        .select("batch_job_name, status, media_type, created_at")
        .neq("status", "completed")
        .execute()
    )
    active_batches = [
        {
            "name": b["batch_job_name"][:40],
            "status": b.get("status"),
            "type": b.get("media_type"),
            "created": b.get("created_at"),
        }
        for b in (batches.data or [])
    ]

    # Recent completions (last 48h)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    recent = (
        supabase.table("yt_viral_videos")
        .select("video_id, title, status, production_completed_at")
        .eq("status", "done")
        .gte("production_completed_at", cutoff)
        .execute()
    )
    recent_done = [
        {
            "video_id": r["video_id"],
            "title": (r.get("title") or "")[:60],
            "completed": r.get("production_completed_at"),
        }
        for r in (recent.data or [])
    ]

    # Stale pipelines
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    stale = (
        supabase.table("yt_viral_videos")
        .select("video_id, title, production_started_at")
        .eq("status", "production_started")
        .lt("production_started_at", stale_cutoff)
        .execute()
    )

    return {
        "video_status": status_counts,
        "approval_status": suitable_counts,
        "total_videos": len(videos.data or []),
        "api_keys": key_health,
        "active_batch_jobs": active_batches,
        "recent_completions_48h": recent_done,
        "stale_pipelines": len(stale.data or []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/video/{video_id}")
async def video_status(video_id: str, request: Request) -> dict[str, Any]:
    """Detailed status for a single video across all pipeline tables."""
    supabase = request.app.state.supabase_client

    # Main record
    video = (
        supabase.table("yt_viral_videos")
        .select("*")
        .eq("video_id", video_id)
        .limit(1)
        .execute()
    )
    if not video.data:
        return {"error": "Video not found", "video_id": video_id}

    record = video.data[0]
    record_id = record["id"]

    # Related data counts
    comments = supabase.table("yt_comments").select(
        "id", count="exact"
    ).eq("video_record_id", record_id).execute()

    transcripts = supabase.table("yt_video_transcripts").select(
        "id, type, provider"
    ).eq("video_record_id", record_id).execute()

    scripts = supabase.table("yt_scripts").select(
        "id", count="exact"
    ).eq("viral_video_id", record_id).execute()

    audio = supabase.table("yt_audio_files").select(
        "id", count="exact"
    ).eq("viral_video_id", record_id).execute()

    batches = supabase.table("yt_batch_jobs").select(
        "batch_job_name, status, media_type"
    ).eq("viral_video_id", record_id).execute()

    analyzer = supabase.table("yt_viral_analyzer_results").select(
        "id"
    ).eq("video_record_id", record_id).execute()

    strategist = supabase.table("yt_strategist_results").select(
        "id"
    ).eq("video_record_id", record_id).execute()

    results = supabase.table("yt_results").select(
        "gcs_video_url, thumbnail_link"
    ).eq("video_id", video_id).execute()

    return {
        "video": {
            "video_id": record["video_id"],
            "title": record.get("title"),
            "status": record.get("status"),
            "suitable": record.get("suitable"),
            "transcript_status": record.get("transcript_status"),
            "comments_status": record.get("comments_status"),
            "production_started_at": record.get("production_started_at"),
            "production_completed_at": record.get("production_completed_at"),
            "production_notes": record.get("production_notes"),
        },
        "pipeline_progress": {
            "comments": comments.count or 0,
            "transcripts": [
                {"type": t.get("type"), "provider": t.get("provider")}
                for t in (transcripts.data or [])
            ],
            "analyzer_complete": len(analyzer.data or []) > 0,
            "strategist_complete": len(strategist.data or []) > 0,
            "script_sentences": scripts.count or 0,
            "audio_files": audio.count or 0,
            "batch_jobs": [
                {"name": b["batch_job_name"][:40], "status": b["status"], "type": b.get("media_type")}
                for b in (batches.data or [])
            ],
            "final_video": (results.data[0] if results.data else None),
        },
    }
