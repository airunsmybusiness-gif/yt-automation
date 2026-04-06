"""Tests for API key auth middleware."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from execution.api.middleware.auth import verify_api_key


class TestVerifyApiKey:
    @pytest.mark.asyncio
    async def test_health_check_bypasses_auth(self) -> None:
        request = MagicMock()
        request.url.path = "/api/health"
        result = await verify_api_key(request, api_key=None)
        assert result == "health-check"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {}, clear=False)
    async def test_no_secret_configured_dev_mode(self) -> None:
        # Remove API_SECRET if present
        os.environ.pop("API_SECRET", None)
        request = MagicMock()
        request.url.path = "/api/submit-url"
        result = await verify_api_key(request, api_key=None)
        assert result == "dev-mode"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"API_SECRET": "test-secret-123"}, clear=False)
    async def test_valid_key_passes(self) -> None:
        request = MagicMock()
        request.url.path = "/api/submit-url"
        result = await verify_api_key(request, api_key="test-secret-123")
        assert result == "test-secret-123"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"API_SECRET": "test-secret-123"}, clear=False)
    async def test_missing_key_rejected(self) -> None:
        request = MagicMock()
        request.url.path = "/api/submit-url"
        request.client.host = "127.0.0.1"
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(request, api_key=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"API_SECRET": "test-secret-123"}, clear=False)
    async def test_wrong_key_rejected(self) -> None:
        request = MagicMock()
        request.url.path = "/api/submit-url"
        request.client.host = "127.0.0.1"
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(request, api_key="wrong-key")
        assert exc_info.value.status_code == 403
