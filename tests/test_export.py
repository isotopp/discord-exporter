import asyncio
import json
from pathlib import Path

from discord_exporter.archive import export_guild
from discord_exporter.config import Config


class EmptyGuildSource:
    async def fetch_guild(self, guild_id: int) -> dict[str, object]:
        assert guild_id == 123
        return {
            "id": 123,
            "name": "Source Guild",
            "owner_id": 456,
            "roles": [{"id": 789, "name": "Archive"}],
            "emojis": [{"id": 987, "name": "wave"}],
        }

    async def fetch_members(self, guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 123
        return []

    async def fetch_channels(self, guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 123
        return []

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        assert channel_id == 0
        return []


class MemberGuildSource(EmptyGuildSource):
    async def fetch_members(self, guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 123
        return [
            {
                "id": 111,
                "username": "alice",
                "display_name": "Shared name",
                "avatar": "avatar-alice",
                "roles": [789],
                "joined_at": "2020-01-02T03:04:05+00:00",
            },
            {
                "id": 222,
                "username": "bob",
                "display_name": "Shared name",
                "avatar": None,
                "roles": [789],
                "joined_at": "2021-02-03T04:05:06+00:00",
            },
        ]


class ChannelGuildSource(MemberGuildSource):
    def __init__(self, channel_name: str = "general") -> None:
        self.channel_name = channel_name

    async def fetch_channels(self, guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 123
        return [
            {
                "id": 100,
                "name": self.channel_name,
                "type": "text",
                "topic": "Archive source",
                "parent_id": None,
                "position": 1,
                "accessible": True,
            },
            {
                "id": 101,
                "name": "discussion",
                "type": "public_thread",
                "parent_id": 100,
                "owner_id": 222,
                "archived": False,
                "locked": False,
                "accessible": True,
            },
        ]

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        assert channel_id in {100, 101}
        return []


class MessageGuildSource(ChannelGuildSource):
    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 200,
                    "channel_id": 100,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "new year",
                },
                {
                    "id": 201,
                    "channel_id": 100,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:00:01+00:00",
                    "content": "one second later",
                },
                {
                    "id": 199,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2020-12-31T23:59:00+00:00",
                    "content": "old year",
                },
            ]
        return await super().fetch_messages(channel_id)


class OffsetMessageGuildSource(MessageGuildSource):
    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 198,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:30:00+01:00",
                    "content": "still old UTC year",
                }
            ]
        return await super().fetch_messages(channel_id)


class RelationshipMessageGuildSource(ChannelGuildSource):
    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 300,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "edited_at": "2021-01-01T00:01:00+00:00",
                    "message_type": "default",
                    "content": "Replying to <@333>",
                    "reference": {
                        "message_id": 299,
                        "channel_id": 100,
                        "guild_id": 123,
                    },
                    "mentions": [{"id": 333, "username": "mentioned"}],
                    "mention_roles": [789],
                    "embeds": [{"title": "An embed"}],
                    "reactions": [{"emoji": {"id": 987, "name": "wave"}, "count": 2}],
                    "attachments": [
                        {
                            "id": 301,
                            "filename": "evidence.txt",
                            "url": "https://cdn.example/evidence.txt",
                        }
                    ],
                }
            ]
        return await super().fetch_messages(channel_id)


class InaccessibleChannelGuildSource(ChannelGuildSource):
    async def fetch_channels(self, guild_id: int) -> list[dict[str, object]]:
        records = await super().fetch_channels(guild_id)
        records.append(
            {
                "id": 102,
                "name": "private",
                "type": "text",
                "parent_id": None,
                "accessible": False,
                "access_error": "Forbidden",
            }
        )
        return records


def test_exporting_an_empty_guild_creates_the_initial_archive(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "channels").mkdir(parents=True)
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, EmptyGuildSource()))

    assert json.loads((root / "server.json").read_text()) == {
        "emojis": [{"id": "987", "name": "wave"}],
        "id": "123",
        "name": "Source Guild",
        "owner_id": "456",
        "roles": [{"id": "789", "name": "Archive"}],
    }
    assert json.loads((root / "manifest.json").read_text()) == {
        "format_version": 1,
        "source_guild_id": "123",
        "status": "in_progress",
    }
    assert json.loads((root / "state.json").read_text()) == {
        "version": 1,
        "channels": {},
    }
    assert (root / "channels").is_dir()
    assert (root / "media" / "avatars").is_dir()


def test_exporting_members_preserves_distinct_ids_and_does_not_duplicate_on_rerun(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, MemberGuildSource()))
    asyncio.run(export_guild(config, MemberGuildSource()))

    members = [
        json.loads(line)
        for line in (root / "members.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert members == [
        {
            "avatar": "avatar-alice",
            "display_name": "Shared name",
            "id": "111",
            "joined_at": "2020-01-02T03:04:05+00:00",
            "roles": ["789"],
            "username": "alice",
        },
        {
            "avatar": None,
            "display_name": "Shared name",
            "id": "222",
            "joined_at": "2021-02-03T04:05:06+00:00",
            "roles": ["789"],
            "username": "bob",
        },
    ]


def test_exporting_channels_keeps_ids_stable_when_a_channel_is_renamed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, ChannelGuildSource("general")))
    asyncio.run(export_guild(config, ChannelGuildSource("renamed")))

    channels = [
        json.loads(line)
        for line in (root / "channels.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert channels == [
        {
            "accessible": True,
            "archive_path": "channels/general--100",
            "id": "100",
            "name": "renamed",
            "parent_id": None,
            "position": 1,
            "topic": "Archive source",
            "type": "text",
        },
        {
            "accessible": True,
            "archive_path": "channels/discussion--101",
            "archived": False,
            "id": "101",
            "locked": False,
            "name": "discussion",
            "owner_id": "222",
            "parent_id": "100",
            "type": "public_thread",
        },
    ]
    assert (root / "channels" / "general--100" / "channel.json").is_file()
    assert not (root / "channels" / "renamed--100").exists()


def test_exporting_channels_records_inaccessible_locations_without_stopping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, InaccessibleChannelGuildSource()))

    channels = [
        json.loads(line)
        for line in (root / "channels.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert channels[-1] == {
        "accessible": False,
        "access_error": "Forbidden",
        "archive_path": "channels/private--102",
        "id": "102",
        "name": "private",
        "parent_id": None,
        "type": "text",
    }
    assert (root / "channels" / "general--100" / "channel.json").is_file()
    assert (root / "channels" / "private--102" / "channel.json").is_file()


def test_exporting_messages_uses_utc_years_and_chronological_jsonl(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, MessageGuildSource()))

    old_year = root / "channels" / "general--100" / "2020" / "messages.jsonl"
    new_year = root / "channels" / "general--100" / "2021" / "messages.jsonl"

    assert [json.loads(line) for line in old_year.read_text().splitlines()] == [
        {
            "author_id": "111",
            "channel_id": "100",
            "content": "old year",
            "created_at": "2020-12-31T23:59:00+00:00",
            "id": "199",
        }
    ]
    assert [json.loads(line) for line in new_year.read_text().splitlines()] == [
        {
            "author_id": "222",
            "channel_id": "100",
            "content": "new year",
            "created_at": "2021-01-01T00:00:00+00:00",
            "id": "200",
        },
        {
            "author_id": "222",
            "channel_id": "100",
            "content": "one second later",
            "created_at": "2021-01-01T00:00:01+00:00",
            "id": "201",
        },
    ]
    assert not (root / "channels" / "general--100" / "2019").exists()


def test_reexporting_messages_does_not_duplicate_and_uses_utc_year(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, OffsetMessageGuildSource()))
    asyncio.run(export_guild(config, OffsetMessageGuildSource()))

    old_year = root / "channels" / "general--100" / "2020" / "messages.jsonl"
    assert [json.loads(line) for line in old_year.read_text().splitlines()] == [
        {
            "author_id": "111",
            "channel_id": "100",
            "content": "still old UTC year",
            "created_at": "2021-01-01T00:30:00+01:00",
            "id": "198",
        }
    ]
    assert not (root / "channels" / "general--100" / "2021").exists()


def test_exporting_messages_preserves_relationships_and_string_ids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, RelationshipMessageGuildSource()))

    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    message = json.loads(message_path.read_text().splitlines()[0])

    assert message == {
        "attachments": [
            {
                "filename": "evidence.txt",
                "id": "301",
                "url": "https://cdn.example/evidence.txt",
            }
        ],
        "author_id": "111",
        "channel_id": "100",
        "content": "Replying to <@333>",
        "created_at": "2021-01-01T00:00:00+00:00",
        "edited_at": "2021-01-01T00:01:00+00:00",
        "embeds": [{"title": "An embed"}],
        "id": "300",
        "mention_roles": ["789"],
        "mentions": [{"id": "333", "username": "mentioned"}],
        "message_type": "default",
        "reactions": [{"count": 2, "emoji": {"id": "987", "name": "wave"}}],
        "reference": {
            "channel_id": "100",
            "guild_id": "123",
            "message_id": "299",
        },
    }
