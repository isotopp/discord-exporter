from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .request_policy import RequestPolicy


class ConfigurationError(ValueError):
    """Raised when the exporter configuration is missing or invalid."""


DEFAULT_DELAY_MIN_SECONDS = 1.0
DEFAULT_DELAY_MAX_SECONDS = 3.0


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    export_root: Path
    delay_min_seconds: float = DEFAULT_DELAY_MIN_SECONDS
    delay_max_seconds: float = DEFAULT_DELAY_MAX_SECONDS
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0"

    @property
    def request_policy(self) -> RequestPolicy:
        return RequestPolicy(
            delay_min_seconds=self.delay_min_seconds,
            delay_max_seconds=self.delay_max_seconds,
            user_agent=self.user_agent,
        )


def load_env(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Config:
    current_directory = cwd or Path.cwd()
    home_directory = home or Path.home()
    local_file = current_directory / ".env"
    environment_file = (
        local_file if local_file.is_file() else home_directory / ".discord-export.env"
    )

    original_environment = dict(os.environ) if environ is not None else None
    if environ is not None:
        os.environ.clear()
        os.environ.update(environ)
    try:
        if environment_file.is_file():
            load_dotenv(environment_file, override=False)
        values = dict(os.environ)
    finally:
        if original_environment is not None:
            os.environ.clear()
            os.environ.update(original_environment)

    token = values.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("missing required setting: DISCORD_TOKEN")

    guild_id = values.get("DISCORD_GUILD_ID", "").strip()
    if not guild_id.isdigit() or int(guild_id) <= 0:
        raise ConfigurationError("DISCORD_GUILD_ID must be a positive integer")

    export_root_value = values.get("DISCORD_EXPORT_ROOT", "").strip()
    if not export_root_value:
        raise ConfigurationError("missing required setting: DISCORD_EXPORT_ROOT")

    export_root = Path(export_root_value).expanduser()
    if not export_root.is_absolute():
        export_root = current_directory / export_root

    delay_min_seconds = _delay_setting(
        values, "DISCORD_DELAY_MIN_SECONDS", DEFAULT_DELAY_MIN_SECONDS
    )
    delay_max_seconds = _delay_setting(
        values, "DISCORD_DELAY_MAX_SECONDS", DEFAULT_DELAY_MAX_SECONDS
    )
    if delay_min_seconds > delay_max_seconds:
        raise ConfigurationError(
            "DISCORD_DELAY_MIN_SECONDS must not exceed DISCORD_DELAY_MAX_SECONDS"
        )

    user_agent = values.get("DISCORD_USER_AGENT", "").strip()
    if not user_agent:
        raise ConfigurationError("missing required setting: DISCORD_USER_AGENT")

    return Config(
        token=token,
        guild_id=int(guild_id),
        export_root=export_root,
        delay_min_seconds=delay_min_seconds,
        delay_max_seconds=delay_max_seconds,
        user_agent=user_agent,
    )


def _delay_setting(values: Mapping[str, str], name: str, default: float) -> float:
    raw_value = values.get(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"{name} must be a finite non-negative number"
        ) from error
    if not math.isfinite(value) or value < 0:
        raise ConfigurationError(f"{name} must be a finite non-negative number")
    return value
