from __future__ import annotations

import discord


class DiscordGuildSource:
    def __init__(self, token: str) -> None:
        self.token = token

    async def fetch_guild(self, guild_id: int) -> dict[str, object]:
        client = discord.Client(intents=discord.Intents.none())
        try:
            await client.login(self.token)
            guild = await client.fetch_guild(guild_id)
            roles = await guild.fetch_roles()
            emojis = await guild.fetch_emojis()
            return {
                "id": guild.id,
                "name": guild.name,
                "description": guild.description,
                "icon": guild.icon.key if guild.icon else None,
                "owner_id": guild.owner_id,
                "features": list(guild.features),
                "roles": [_role_record(role) for role in roles],
                "emojis": [_emoji_record(emoji) for emoji in emojis],
            }
        finally:
            await client.close()


def _role_record(role: discord.Role) -> dict[str, object]:
    return {
        "id": role.id,
        "name": role.name,
        "position": role.position,
        "permissions": role.permissions.value,
        "color": role.colour.value,
        "hoist": role.hoist,
        "managed": role.managed,
        "mentionable": role.mentionable,
    }


def _emoji_record(emoji: discord.Emoji) -> dict[str, object]:
    return {
        "id": emoji.id,
        "name": emoji.name,
        "roles": [role.id for role in emoji.roles],
        "require_colons": emoji.require_colons,
        "managed": emoji.managed,
        "animated": emoji.animated,
    }
