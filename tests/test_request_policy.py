import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from discord_exporter.discord_source import DiscordGuildSource
from discord_exporter.request_policy import RequestPolicy


def test_request_policy_delays_within_configured_bounds_and_exposes_headers() -> None:
    observed_delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        observed_delays.append(delay)

    policy = RequestPolicy(
        delay_min_seconds=1.5,
        delay_max_seconds=3.5,
        user_agent="Firefox/test",
        sleep=record_sleep,
        uniform=lambda lower, upper: (lower + upper) / 2,
    )

    delay = asyncio.run(policy.before_request())

    assert delay == 2.5
    assert observed_delays == [2.5]
    assert policy.headers == {
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.5",
        "User-Agent": "Firefox/test",
    }


def test_discord_api_requests_keep_discord_py_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def request(*args: Any, **kwargs: Any) -> None:
        pass

    session = SimpleNamespace(headers={"User-Agent": "DiscordBot/test"})
    fake_client = SimpleNamespace(
        http=SimpleNamespace(
            user_agent="DiscordBot/test",
            request=request,
            _HTTPClient__session=session,
        )
    )
    monkeypatch.setattr(discord, "Client", lambda **kwargs: fake_client)
    policy = RequestPolicy(0, 0, "Firefox/test")
    client: Any = DiscordGuildSource("token", policy)._client(discord.Intents.none())

    asyncio.run(client.http.request())

    assert client.http.user_agent == "DiscordBot/test"
    assert client.http._HTTPClient__session.headers == {"User-Agent": "DiscordBot/test"}
