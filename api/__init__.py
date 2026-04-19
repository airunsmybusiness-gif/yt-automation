import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from supabase import create_client

log = logging.getLogger("api")
router = APIRouter()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
API_SECRET = os.environ["API_SECRET"]


def _sb():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _auth(x_api_key: str = Header(...)):
    if x_api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/api/health")
async def health():
    return {"status": "ok", "service": "yt-automation"}


@router.get("/api/version")
async def version():
    return {"commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "local")[:7]}


@router.get("/api/status/dashboard", dependencies=[Depends(_auth)])
async def dashboard():
    sb = _sb()
    videos = sb.table("yt_viral_videos").select("status").execute()
    status_counts: dict[str, int] = {}
    for row in videos.data:
        s = row["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    batch_jobs = (
        sb.table("yt_batch_jobs")
        .select("name,status,type,created_at")
        .neq("status", "completed")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    return {
        "video_status": status_counts,
        "active_batch_jobs": batch_jobs.data,
        "timestamp": "now",
    }


@router.get("/api/status/video/{video_id}", dependencies=[Depends(_auth)])
async def video_status(video_id: str):
    sb = _sb()
    row = (
        sb.table("yt_viral_videos")
        .select("*")
        .eq("video_id", video_id)
        .single()
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Video not found")

    vid_id = row.data["id"]
    scripts = sb.table("yt_scripts").select("count").eq("viral_video_id", vid_id).execute()
    audio = sb.table("yt_audio_files").select("start_sentence_number").eq("viral_video_id", vid_id).execute()
    result = sb.table("yt_results").select("*").eq("viral_video_id", vid_id).limit(1).execute()

    return {
        "video": row.data,
        "pipeline_progress": {
            "script_sentences": scripts.data[0]["count"] if scripts.data else 0,
            "audio_files": len(audio.data) if audio.data else 0,
            "final_video": result.data[0] if result.data else {},
        },
    }


class TriggerBody(BaseModel):
    pass


@router.post("/api/pipeline/trigger/{viral_video_id}", dependencies=[Depends(_auth)])
async def trigger_video(viral_video_id: str):
    sb = _sb()
    row = (
        sb.table("yt_viral_videos")
        .select("status,suitable,video_id")
        .eq("id", viral_video_id)
        .single()
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Video not found")
    if not row.data.get("suitable"):
        raise HTTPException(status_code=400, detail="Video not approved (suitable != true)")
    if row.data["status"] != "queued":
        raise HTTPException(
            status_code=400,
            detail=f"Video already in status '{row.data['status']}', must be 'queued'",
        )

    sb.table("yt_viral_videos").update({"suitable": True}).eq("id", viral_video_id).execute()

    from orchestration.pipeline import Pipeline
    pipeline = Pipeline()
    try:
        pipeline.process_next()
        return {"status": "triggered", "video_id": row.data["video_id"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
