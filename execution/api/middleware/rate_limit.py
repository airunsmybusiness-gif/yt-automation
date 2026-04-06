"""Simple in-memory rate limiter for API endpoints."""

import logging
import time
from collections import defaultdict

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter.

    Not distributed — works for a single Railway instance.
    For multi-instance, swap to Redis-backed.
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Check if request is within rate limit.

        Args:
            key: Client identifier (IP or API key).

        Raises:
            HTTPException: If rate limit exceeded.
        """
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean expired entries
        self._requests[key] = [
            t for t in self._requests[key] if t > cutoff
        ]

        if len(self._requests[key]) >= self.max_requests:
            logger.warning("Rate limit exceeded for %s", key)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {self.max_requests} requests per {self.window_seconds}s.",
            )

        self._requests[key].append(now)


# Singleton instances for different tiers
api_limiter = RateLimiter(max_requests=30, window_seconds=60)
webhook_limiter = RateLimiter(max_requests=10, window_seconds=60)
