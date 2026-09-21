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

    async def fetch_channels(self, guild_id: int) -> list[dict[str, object]]:
        client = discord.Client(intents=discord.Intents.none())
        try:
            await client.login(self.token)
            guild = await client.fetch_guild(guild_id)
            if client.user is None:
                raise RuntimeError("Discord did not return the bot user")
            bot_member = await guild.fetch_member(client.user.id)
            channels: list[dict[str, object]] = []
            for channel in await guild.fetch_channels():
                if not _is_message_channel(channel):
                    continue
                record = _channel_record(channel, bot_member)
                channels.append(record)
                if record["accessible"]:
                    await _append_archived_threads(channel, channels, record)
            channels.extend(
                _thread_record(thread) for thread in await guild.active_threads()
            )
            return channels
        finally:
            await client.close()

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        client = discord.Client(intents=discord.Intents.none())
        try:
            await client.login(self.token)
            channel = await client.fetch_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                return []
            messages: list[dict[str, object]] = []
            async for message in channel.history(limit=None, oldest_first=True):
                messages.append(_message_record(message))
            return messages
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


def _is_message_channel(channel: discord.abc.GuildChannel) -> bool:
    return isinstance(
        channel,
        (
            discord.TextChannel,
            discord.VoiceChannel,
            discord.StageChannel,
            discord.ForumChannel,
        ),
    )


def _channel_record(
    channel: discord.abc.GuildChannel, bot_member: discord.Member
) -> dict[str, object]:
    return {
        "id": channel.id,
        "name": channel.name,
        "type": channel.type.name,
        "parent_id": channel.category_id,
        "position": channel.position,
        "topic": getattr(channel, "topic", None),
        "nsfw": getattr(channel, "nsfw", False),
        "permission_overwrites": [
            overwrite._asdict() for overwrite in channel._overwrites
        ],
        "accessible": (
            channel.permissions_for(bot_member).view_channel
            and channel.permissions_for(bot_member).read_message_history
        ),
    }


def _thread_record(thread: discord.Thread) -> dict[str, object]:
    return {
        "id": thread.id,
        "name": thread.name,
        "type": thread.type.name,
        "parent_id": thread.parent_id,
        "owner_id": thread.owner_id,
        "archived": thread.archived,
        "locked": thread.locked,
        "auto_archive_duration": thread.auto_archive_duration,
        "archive_timestamp": (
            thread.archive_timestamp.isoformat() if thread.archive_timestamp else None
        ),
        "accessible": True,
    }


def _message_record(message: discord.Message) -> dict[str, object]:
    return {
        "id": message.id,
        "channel_id": message.channel.id,
        "author_id": message.author.id,
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "content": message.content,
    }


async def _append_archived_threads(
    channel: discord.abc.GuildChannel,
    records: list[dict[str, object]],
    channel_record: dict[str, object],
) -> None:
    try:
        if isinstance(channel, discord.TextChannel):
            async for thread in channel.archived_threads(limit=None):
                records.append(_thread_record(thread))
            async for thread in channel.archived_threads(private=True, limit=None):
                records.append(_thread_record(thread))
        elif isinstance(channel, discord.ForumChannel):
            async for thread in channel.archived_threads(limit=None):
                records.append(_thread_record(thread))
    except (discord.Forbidden, discord.HTTPException) as error:
        channel_record["thread_discovery_error"] = type(error).__name__
