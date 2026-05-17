"""YouTube upload — in-process, resumable, with thumbnail."""

import logging
from pathlib import Path
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
import io

logger = logging.getLogger(__name__)


def _get_credentials(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Credentials:
    """Build and refresh YouTube OAuth credentials."""
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    logger.info("YouTube OAuth token refreshed")
    return creds


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    thumbnail_path: Optional[Path] = None,
    privacy_status: str = "private",
) -> dict[str, Any]:
    """Upload video to YouTube with metadata and thumbnail.

    Args:
        video_path: Path to MP4 file.
        title: Video title (50-70 chars).
        description: SEO-optimized description.
        tags: 15-20 tags.
        category_id: "27" for Education.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        refresh_token: OAuth refresh token (for MindSeam channel).
        thumbnail_path: Optional path to thumbnail JPG.
        privacy_status: "private" or "public".

    Returns:
        Dict with video_id, video_url, thumbnail_uploaded.
    """
    credentials = _get_credentials(client_id, client_secret, refresh_token)
    youtube = build("youtube", "v3", credentials=credentials)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:20],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "embeddable": True,
            "license": "youtube",
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=1024 * 1024,
        resumable=True,
    )

    logger.info("Starting YouTube upload: %s", title[:60])
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %d%%", int(status.progress() * 100))

    yt_video_id = response["id"]
    logger.info("Upload complete: %s", yt_video_id)

    # Set thumbnail
    thumb_ok = False
    if thumbnail_path and thumbnail_path.exists():
        try:
            thumb_data = thumbnail_path.read_bytes()
            mime = "image/jpeg"
            if thumb_data[:4] == b"\x89PNG":
                mime = "image/png"

            thumb_media = MediaIoBaseUpload(
                io.BytesIO(thumb_data), mimetype=mime, resumable=True,
            )
            youtube.thumbnails().set(
                videoId=yt_video_id, media_body=thumb_media,
            ).execute()
            thumb_ok = True
            logger.info("Thumbnail set for %s", yt_video_id)
        except HttpError as e:
            logger.error("Thumbnail upload failed: %s", e)
        except Exception as e:
            logger.error("Thumbnail error: %s", e)

    return {
        "video_id": yt_video_id,
        "video_url": f"https://www.youtube.com/watch?v={yt_video_id}",
        "title": response["snippet"]["title"],
        "thumbnail_uploaded": thumb_ok,
    }
