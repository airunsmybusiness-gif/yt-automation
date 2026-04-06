"""Tests for configuration loading and validation."""

import os
import pytest
from unittest.mock import patch

from config.settings import load_settings, REQUIRED_ENV_VARS


MOCK_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "YOUTUBE_API_KEYS": "key1,key2,key3",
    "GMAIL_CREDENTIALS_JSON": "dGVzdA==",
    "GMAIL_TOKEN_JSON": "dGVzdA==",
    "GMAIL_SENDER_EMAIL": "test@gmail.com",
    "GMAIL_APPROVAL_TO": "owner@gmail.com",
    "GCS_BUCKET_NAME": "test-bucket",
    "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/creds.json",
}


class TestLoadSettings:
    @patch.dict(os.environ, MOCK_ENV, clear=False)
    def test_happy_path(self) -> None:
        settings = load_settings()
        assert settings.supabase_url == "https://test.supabase.co"
        assert len(settings.youtube_api_keys) == 3
        assert settings.gmail_sender_email == "test@gmail.com"
        assert settings.viral_min_views_48h == 7000  # default

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_env_vars_exits(self) -> None:
        with pytest.raises(SystemExit, match="Missing env vars"):
            load_settings()

    @patch.dict(os.environ, {**MOCK_ENV, "YOUTUBE_API_KEYS": " , , "}, clear=False)
    def test_empty_api_keys_exits(self) -> None:
        with pytest.raises(SystemExit, match="YOUTUBE_API_KEYS is empty"):
            load_settings()

    @patch.dict(os.environ, {**MOCK_ENV, "YOUTUBE_API_KEYS": "  key1 , key2  "}, clear=False)
    def test_api_keys_trimmed(self) -> None:
        settings = load_settings()
        assert settings.youtube_api_keys == ["key1", "key2"]

    @patch.dict(os.environ, {**MOCK_ENV, "VIRAL_MIN_VIEWS_48H": "15000"}, clear=False)
    def test_custom_threshold(self) -> None:
        settings = load_settings()
        assert settings.viral_min_views_48h == 15000
