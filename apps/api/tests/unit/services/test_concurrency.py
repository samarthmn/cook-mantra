import asyncio

import pytest

from services.concurrency import ModelCallLimiter


class ConcurrencyProbe:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0

    async def operation(self) -> None:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1


@pytest.mark.asyncio
async def test_model_limiter_caps_simultaneous_calls() -> None:
    probe = ConcurrencyProbe()
    limiter = ModelCallLimiter(max_concurrent_calls=2)

    await asyncio.gather(*(limiter.run(probe.operation) for _ in range(4)))

    assert probe.maximum_active == 2
