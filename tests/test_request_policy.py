import asyncio

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
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.5",
        "User-Agent": "Firefox/test",
    }
