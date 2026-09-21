from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when the exporter configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    export_root: Path


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

    return Config(token=token, guild_id=int(guild_id), export_root=export_root)
