from __future__ import annotations

import asyncio
import json
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
        asyncio.run(
            export_guild(
                config, DiscordGuildSource(config.token, config.request_policy)
            )
        )
    except (discord.DiscordException, ValueError) as error:
        print(f"Discord export failed: {error}", file=sys.stderr)
        return 1

    manifest = json.loads((config.export_root / "manifest.json").read_text())
    status = "completed" if manifest.get("status") == "complete" else "incomplete"
    print(f"Export {status} at {config.export_root}")
    return 0
