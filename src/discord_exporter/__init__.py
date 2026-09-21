from __future__ import annotations

import asyncio
import sys

import discord

from .archive import export_guild
from .config import ConfigurationError, load_env
from .discord_source import DiscordGuildSource


def main() -> int:
    try:
        config = load_env()
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    try:
        asyncio.run(export_guild(config, DiscordGuildSource(config.token)))
    except (discord.DiscordException, ValueError) as error:
        print(f"Discord export failed: {error}", file=sys.stderr)
        return 1

    print(f"Export initialized at {config.export_root}")
    return 0
