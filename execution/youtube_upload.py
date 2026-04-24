"""
Direct YouTube upload from Railway.
Replaces the Google Cloud Function upload-video.
Uses YouTube Data API v3 with OAuth refresh token.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)

YT_CLIENT_ID: str = os.environ["YOUTUBE_CLIENT_ID"]
YT_CLIENT_SECRET: str = os.environ["YOUTUBE_CLIENT_SECRET"]
YT_REFRESH_TOKEN: str = os.environ["YOUTUBE_REFRESH_TOKEN"]
YT_TOKEN_URI: str = "https://oauth2.googleapis.com/token"
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubeUploadError(RuntimeError):
    """Raised when YouTube upload fails."""


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri=YT_TOKEN_URI,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    thumbnail_path: Path | None = None,
    privacy_status: str = "private",
    category_id: str = "27",
) -> dict:
    if not video_path.exists():
        raise YouTubeUploadError(f"Video file missing: {video_path}")

    service = _get_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:15],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True,
    )

    try:
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info(f"YouTube upload: {int(status.progress() * 100)}%")
    except HttpError as exc:
        raise YouTubeUploadError(f"YouTube upload failed: {exc}") from exc

    video_id = response.get("id")
    log.info(f"YouTube upload complete: video_id={video_id}")

    thumbnail_ok = False
    if thumbnail_path and thumbnail_path.exists():
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            thumbnail_ok = True
            log.info(f"Thumbnail uploaded for {video_id}")
        except HttpError as exc:
            log.warning(f"Thumbnail upload failed: {exc}")

    return {
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "thumbnail_uploaded": thumbnail_ok,
    }
