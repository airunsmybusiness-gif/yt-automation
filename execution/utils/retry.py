"""Retry utility with exponential backoff for transient failures."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _func_name(func: Callable) -> str:
    """Safely get function name, compatible with MagicMock."""
    return getattr(func, "__name__", repr(func))


def retry_sync(
    func: Callable[..., T],
    *args: object,
    max_attempts: int = 3,
    backoff: float = 1.0,
    retryable: tuple[type[Exception], ...] = (ConnectionError, TimeoutError),
    **kwargs: object,
) -> T:
    """Retry a synchronous function with exponential backoff.

    Args:
        func: Function to call.
        *args: Positional arguments for func.
        max_attempts: Maximum number of attempts.
        backoff: Base backoff in seconds.
        retryable: Exception types that trigger a retry.
        **kwargs: Keyword arguments for func.

    Returns:
        The return value of func.

    Raises:
        The last exception if all attempts fail.
    """
    name = _func_name(func)
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except retryable as e:
            if attempt == max_attempts:
                logger.error("All %d attempts failed for %s: %s", max_attempts, name, e)
                raise
            wait = backoff * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, max_attempts, name, e, wait,
            )
            time.sleep(wait)
    raise RuntimeError("Unreachable")  # pragma: no cover


async def retry_async(
    func: Callable[..., T],
    *args: object,
    max_attempts: int = 3,
    backoff: float = 1.0,
    retryable: tuple[type[Exception], ...] = (ConnectionError, TimeoutError),
    **kwargs: object,
) -> T:
    """Async version of retry with exponential backoff."""
    name = _func_name(func)
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except retryable as e:
            if attempt == max_attempts:
                logger.error("All %d attempts failed for %s: %s", max_attempts, name, e)
                raise
            wait = backoff * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, max_attempts, name, e, wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("Unreachable")  # pragma: no cover
