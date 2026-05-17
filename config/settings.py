"""Environment configuration — validates all secrets at startup."""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """All environment variables required by the pipeline."""

    # Supabase
    supabase_url: str
    supabase_service_key: str

    # Anthropic (agents)
    anthropic_api_key: str

    # Replicate (images)
    replicate_api_token: str

    # YouTube OAuth
    youtube_client_id: str
    youtube_client_secret: str
    youtube_refresh_token: str

    # Gmail OAuth
    gmail_credentials_json: str
    gmail_token_json: str

    # Optional
    log_level: str = "INFO"
    max_cost_per_video: float = 2.00
    max_images_per_video: int = 50
    target_video_minutes: int = 9
    edge_tts_voice: str = "en-US-GuyNeural"


def load_settings() -> Settings:
    """Load and validate all settings from environment. Crash if any missing."""
    required = {
        "SUPABASE_URL": "supabase_url",
        "SUPABASE_SERVICE_KEY": "supabase_service_key",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "REPLICATE_API_TOKEN": "replicate_api_token",
        "YOUTUBE_CLIENT_ID": "youtube_client_id",
        "YOUTUBE_CLIENT_SECRET": "youtube_client_secret",
        "YOUTUBE_REFRESH_TOKEN": "youtube_refresh_token",
        "GMAIL_CREDENTIALS_JSON": "gmail_credentials_json",
        "GMAIL_TOKEN_JSON": "gmail_token_json",
    }

    missing = [k for k in required if k not in os.environ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Settings(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        replicate_api_token=os.environ["REPLICATE_API_TOKEN"],
        youtube_client_id=os.environ["YOUTUBE_CLIENT_ID"],
        youtube_client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        youtube_refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        gmail_credentials_json=os.environ["GMAIL_CREDENTIALS_JSON"],
        gmail_token_json=os.environ["GMAIL_TOKEN_JSON"],
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        max_cost_per_video=float(os.environ.get("MAX_COST_PER_VIDEO", "2.00")),
        max_images_per_video=int(os.environ.get("MAX_IMAGES_PER_VIDEO", "50")),
        target_video_minutes=int(os.environ.get("TARGET_VIDEO_MINUTES", "9")),
        edge_tts_voice=os.environ.get("EDGE_TTS_VOICE", "en-US-GuyNeural"),
    )
