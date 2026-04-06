"""Tests for retry utility."""

import pytest
from unittest.mock import MagicMock

from execution.utils.retry import retry_sync


class TestRetrySync:
    def test_succeeds_first_try(self) -> None:
        func = MagicMock(return_value="ok")
        result = retry_sync(func, max_attempts=3, backoff=0.01)
        assert result == "ok"
        assert func.call_count == 1

    def test_retries_on_transient_error(self) -> None:
        func = MagicMock(side_effect=[ConnectionError("fail"), "ok"])
        result = retry_sync(func, max_attempts=3, backoff=0.01)
        assert result == "ok"
        assert func.call_count == 2

    def test_exhausts_retries(self) -> None:
        func = MagicMock(side_effect=ConnectionError("persistent"))
        with pytest.raises(ConnectionError, match="persistent"):
            retry_sync(func, max_attempts=3, backoff=0.01)
        assert func.call_count == 3

    def test_non_retryable_raises_immediately(self) -> None:
        func = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError, match="bad input"):
            retry_sync(func, max_attempts=3, backoff=0.01)
        assert func.call_count == 1
