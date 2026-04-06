"""API key authentication middleware for production endpoints."""

import logging
import os
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(API_KEY_HEADER),
) -> str:
    """Verify the X-API-Key header against the configured secret.

    Skips auth for health check endpoint and if API_SECRET is not configured
    (development mode).

    Returns:
        The validated API key string.

    Raises:
        HTTPException: If key is missing or invalid.
    """
    # Always allow health check
    if request.url.path == "/api/health":
        return "health-check"

    expected = os.environ.get("API_SECRET")
    if not expected:
        # No secret configured = development mode, skip auth
        return "dev-mode"

    if not api_key:
        logger.warning("Missing API key from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if api_key != expected:
        logger.warning("Invalid API key attempt from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key
