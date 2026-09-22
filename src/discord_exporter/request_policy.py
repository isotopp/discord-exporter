from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]


@dataclass(frozen=True)
class RequestPolicy:
    delay_min_seconds: float
    delay_max_seconds: float
    user_agent: str
    sleep: Sleep = asyncio.sleep
    uniform: Uniform = random.uniform

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.delay_min_seconds)
            or not math.isfinite(self.delay_max_seconds)
            or self.delay_min_seconds < 0
            or self.delay_min_seconds > self.delay_max_seconds
        ):
            raise ValueError("request delay range is invalid")
        if not self.user_agent.strip():
            raise ValueError("request user agent is required")

    async def before_request(self) -> float:
        delay = self.uniform(self.delay_min_seconds, self.delay_max_seconds)
        await self.sleep(delay)
        return delay

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.5",
        }
