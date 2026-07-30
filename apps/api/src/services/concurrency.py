"""Shared concurrency controls for model calls."""

import asyncio
from collections.abc import Awaitable, Callable


class ModelCallLimiter:
    """Cap the number of simultaneous model calls."""

    def __init__(self, max_concurrent_calls: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_calls)

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run one model operation within the configured concurrency limit."""
        async with self._semaphore:
            return await operation()
