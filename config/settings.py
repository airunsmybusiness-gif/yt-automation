"""Application settings — all secrets via env vars, fail at startup if missing."""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS: list[str] = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "YOUTUBE_API_KEYS",          # comma-separated
    "GMAIL_CREDENTIALS_JSON",    # base64-encoded OAuth2 credentials
    "GMAIL_TOKEN_JSON",          # base64-encoded OAuth2 token
    "GMAIL_SENDER_EMAIL",
    "GMAIL_APPROVAL_TO",         # email to send approvals to
    "GCS_BUCKET_NAME",
    "GOOGLE_APPLICATION_CREDENTIALS",
]


@dataclass(frozen=True)
class Settings:
    """Immutable application configuration loaded from environment."""

    supabase_url: str
    supabase_key: str
    youtube_api_keys: list[str]
    gmail_credentials_json: str
    gmail_token_json: str
    gmail_sender_email: str
    gmail_approval_to: str
    gcs_bucket_name: str
    google_application_credentials: str

    # Tunable defaults
    discovery_interval_hours: int = 12
    email_poll_interval_seconds: int = 60
    quota_reset_hour_utc: int = 8
    viral_min_views_48h: int = 7000
    viral_min_views_12h: int = 4000
    max_video_age_hours: int = 48
    log_level: str = "INFO"

    # Derived
    _active_key_index: int = field(default=0, repr=False)


def load_settings() -> Settings:
    """Load and validate all settings from environment.

    Raises:
        SystemExit: If any required env var is missing.
    """
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        logger.critical("Missing required environment variables: %s", missing)
        raise SystemExit(f"Missing env vars: {missing}")

    keys_raw = os.environ["YOUTUBE_API_KEYS"]
    youtube_keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not youtube_keys:
        raise SystemExit("YOUTUBE_API_KEYS is empty after parsing")

    settings = Settings(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_KEY"],
        youtube_api_keys=youtube_keys,
        gmail_credentials_json=os.environ["GMAIL_CREDENTIALS_JSON"],
        gmail_token_json=os.environ["GMAIL_TOKEN_JSON"],
        gmail_sender_email=os.environ["GMAIL_SENDER_EMAIL"],
        gmail_approval_to=os.environ["GMAIL_APPROVAL_TO"],
        gcs_bucket_name=os.environ["GCS_BUCKET_NAME"],
        google_application_credentials=os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        viral_min_views_48h=int(os.environ.get("VIRAL_MIN_VIEWS_48H", "7000")),
        viral_min_views_12h=int(os.environ.get("VIRAL_MIN_VIEWS_12H", "4000")),
        max_video_age_hours=int(os.environ.get("MAX_VIDEO_AGE_HOURS", "48")),
    )

    logger.info(
        "Settings loaded: %d YouTube API keys, Supabase=%s, sender=%s",
        len(settings.youtube_api_keys),
        settings.supabase_url[:30] + "...",
        settings.gmail_sender_email,
    )
    return settings
