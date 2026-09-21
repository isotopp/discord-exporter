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

    async def fetch_members(self, guild_id: int) -> list[dict[str, object]]:
        intents = discord.Intents.none()
        intents.members = True
        client = discord.Client(intents=intents)
        try:
            await client.login(self.token)
            guild = await client.fetch_guild(guild_id)
            members: list[dict[str, object]] = []
            async for member in guild.fetch_members(limit=None):
                members.append(_member_record(member))
            return members
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


def _member_record(member: discord.Member) -> dict[str, object]:
    return {
        "id": member.id,
        "username": member.name,
        "global_name": member.global_name,
        "display_name": member.display_name,
        "discriminator": member.discriminator,
        "avatar": member.avatar.key if member.avatar else None,
        "bot": member.bot,
        "system": member.system,
        "nick": member.nick,
        "roles": [role.id for role in member.roles],
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "premium_since": (
            member.premium_since.isoformat() if member.premium_since else None
        ),
        "pending": member.pending,
    }
