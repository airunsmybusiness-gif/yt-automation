"""Tests for rate limiter middleware."""

import pytest
from fastapi import HTTPException

from execution.api.middleware.rate_limit import RateLimiter


class TestRateLimiter:
    def test_allows_within_limit(self) -> None:
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            limiter.check("test-client")  # Should not raise

    def test_blocks_over_limit(self) -> None:
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("test-client")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("test-client")
        assert exc_info.value.status_code == 429

    def test_separate_keys_independent(self) -> None:
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.check("client-a")
        limiter.check("client-a")
        # client-a is now at limit, but client-b should be fine
        limiter.check("client-b")  # Should not raise

    def test_expired_entries_cleaned(self) -> None:
        limiter = RateLimiter(max_requests=2, window_seconds=0)
        # With window=0, all entries expire immediately
        limiter.check("test")
        limiter.check("test")
        # This should pass because previous entries are expired
        limiter.check("test")
