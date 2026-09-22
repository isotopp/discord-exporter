import asyncio
import io
import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import discord
import pytest

from discord_exporter.archive import export_guild
from discord_exporter.config import Config


class ProgressStream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _without_failure_timestamps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("expected a failure list")
    return [
        {key: item for key, item in failure.items() if key != "occurred_at"}
        for failure in value
        if isinstance(failure, Mapping)
    ]


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

    async def fetch_messages_after(
        self, channel_id: int, after_message_id: str
    ) -> list[dict[str, object]]:
        assert after_message_id
        return []

    async def iter_messages(
        self, channel_id: int, after_message_id: str | None
    ) -> AsyncIterator[dict[str, object]]:
        seen: set[str] = set()
        if after_message_id is None:
            records = await self.fetch_messages(channel_id)
            cursor = "0"
        else:
            records = []
            cursor = after_message_id

        while True:
            records.sort(
                key=lambda record: (str(record["created_at"]), str(record["id"]))
            )
            for record in records:
                message_id = str(record["id"])
                if message_id not in seen:
                    seen.add(message_id)
                    yield record
                    cursor = message_id

            records = await self.fetch_messages_after(channel_id, cursor)
            if not records:
                return

    async def download_media(self, url: str) -> bytes:
        assert url
        return b"media"


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


class AvatarMemberGuildSource(MemberGuildSource):
    def __init__(self) -> None:
        self.downloads = 0

    async def fetch_members(self, guild_id: int) -> list[dict[str, object]]:
        records = await super().fetch_members(guild_id)
        records[0]["avatar_url"] = (
            "https://cdn.example/avatars/avatar-alice.png?size=1024"
        )
        return records

    async def download_media(self, url: str) -> bytes:
        self.downloads += 1
        assert url.endswith(".png?size=1024")
        return b"avatar-bytes"


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


class ResumeGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.full_channels: list[int] = []
        self.after_calls: list[tuple[int, str]] = []
        self.has_new_message = False

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        self.full_channels.append(channel_id)
        if channel_id == 100:
            return [
                {
                    "id": 200,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "first",
                },
                {
                    "id": 201,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:01:00+00:00",
                    "content": "second",
                },
            ]
        return await super().fetch_messages(channel_id)

    async def fetch_messages_after(
        self, channel_id: int, after_message_id: str
    ) -> list[dict[str, object]]:
        self.after_calls.append((channel_id, after_message_id))
        if channel_id == 100 and after_message_id == "201" and self.has_new_message:
            return [
                {
                    "id": 202,
                    "channel_id": 100,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:02:00+00:00",
                    "content": "arrived later",
                }
            ]
        return []


class InterruptingStreamGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt_after_first = True
        self.cursors: list[tuple[int, str | None]] = []

    async def iter_messages(
        self, channel_id: int, after_message_id: str | None
    ) -> AsyncIterator[dict[str, object]]:
        self.cursors.append((channel_id, after_message_id))
        if channel_id != 100:
            return

        messages: list[dict[str, object]] = [
            {
                "id": 700,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:00:00+00:00",
                "content": "first",
            },
            {
                "id": 701,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:01:00+00:00",
                "content": "second",
            },
        ]
        start = 0
        if after_message_id is not None:
            start = next(
                index + 1
                for index, message in enumerate(messages)
                if str(message["id"]) == after_message_id
            )

        for message in messages[start:]:
            yield message
            if self.interrupt_after_first and message["id"] == 700:
                raise KeyboardInterrupt


class FailedMediaThenInterruptSource(ChannelGuildSource):
    async def iter_messages(
        self, channel_id: int, after_message_id: str | None
    ) -> AsyncIterator[dict[str, object]]:
        if channel_id != 100:
            return
        yield {
            "id": 710,
            "channel_id": 100,
            "author_id": 111,
            "created_at": "2021-01-01T00:00:00+00:00",
            "content": "failed file",
            "attachments": [
                {
                    "id": 711,
                    "filename": "bad.txt",
                    "url": "https://cdn.example/bad",
                }
            ],
        }
        raise KeyboardInterrupt

    async def download_media(self, url: str) -> bytes:
        raise OSError("media unavailable")


class InterruptingMediaSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt_media = True

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id != 100:
            return await super().fetch_messages(channel_id)
        return [
            {
                "id": 720,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:00:00+00:00",
                "content": "retry file",
                "attachments": [
                    {
                        "id": 721,
                        "filename": "retry.txt",
                        "url": "https://cdn.example/retry",
                    }
                ],
            }
        ]

    async def download_media(self, url: str) -> bytes:
        if self.interrupt_media:
            raise KeyboardInterrupt
        return b"retry bytes"


class ErrorAfterFirstMessageSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.fail_after_first = True
        self.cursors: list[tuple[int, str | None]] = []

    async def iter_messages(
        self, channel_id: int, after_message_id: str | None
    ) -> AsyncIterator[dict[str, object]]:
        self.cursors.append((channel_id, after_message_id))
        if channel_id != 100:
            return
        if after_message_id is None:
            yield {
                "id": 730,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:00:00+00:00",
                "content": "first",
            }
            if self.fail_after_first:
                raise OSError("stream interrupted")
            return
        if after_message_id == "730":
            yield {
                "id": 731,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:01:00+00:00",
                "content": "second",
            }


class MetadataProgressSource(ChannelGuildSource):
    async def fetch_channels(self, guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 123
        return [
            {
                "id": 100,
                "name": "general",
                "type": "text",
                "parent_id": None,
                "accessible": True,
            },
            {
                "id": 101,
                "name": "random",
                "type": "text",
                "parent_id": None,
                "accessible": True,
            },
            {
                "id": 102,
                "name": "general-thread",
                "type": "public_thread",
                "parent_id": 100,
                "accessible": True,
            },
            {
                "id": 103,
                "name": "random-thread",
                "type": "private_thread",
                "parent_id": 101,
                "accessible": True,
            },
            {
                "id": 104,
                "name": "another-thread",
                "type": "public_thread",
                "parent_id": 101,
                "accessible": True,
            },
        ]

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        return []

    async def fetch_messages_after(
        self, channel_id: int, after_message_id: str
    ) -> list[dict[str, object]]:
        return []


class MessageMediaGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.downloads: list[str] = []

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 700,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "first file",
                    "attachments": [
                        {
                            "id": 701,
                            "filename": "same.txt",
                            "url": "https://cdn.example/701",
                        }
                    ],
                },
                {
                    "id": 702,
                    "channel_id": 100,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:01:00+00:00",
                    "content": "second file",
                    "attachments": [
                        {
                            "id": 703,
                            "filename": "same.txt",
                            "url": "https://cdn.example/703",
                        }
                    ],
                    "embeds": [{"image": {"url": "https://cdn.example/embed.png"}}],
                    "stickers": [{"id": 704, "name": "keep-metadata-only"}],
                },
            ]
        return await super().fetch_messages(channel_id)

    async def download_media(self, url: str) -> bytes:
        self.downloads.append(url)
        return url.encode()


class FailedMediaGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.downloads: list[str] = []

    async def fetch_members(self, guild_id: int) -> list[dict[str, object]]:
        records = await super().fetch_members(guild_id)
        records[0]["avatar_url"] = "https://cdn.example/avatar-bad"
        return records

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 800,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "retain this message",
                    "attachments": [
                        {
                            "id": 801,
                            "filename": "good.txt",
                            "url": "https://cdn.example/good",
                        },
                        {
                            "id": 802,
                            "filename": "bad.txt",
                            "url": "https://cdn.example/message-bad",
                        },
                    ],
                }
            ]
        return await super().fetch_messages(channel_id)

    async def download_media(self, url: str) -> bytes:
        self.downloads.append(url)
        if url.endswith("-bad"):
            raise OSError("media unavailable")
        return url.encode()


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


class CatchUpMessageGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.catch_up_calls = 0

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return [
                {
                    "id": 400,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "historical",
                }
            ]
        return await super().fetch_messages(channel_id)

    async def fetch_messages_after(
        self, channel_id: int, after_message_id: str
    ) -> list[dict[str, object]]:
        if channel_id != 100:
            return []
        self.catch_up_calls += 1
        assert after_message_id == str(399 + self.catch_up_calls)
        if self.catch_up_calls == 1:
            return [
                {
                    "id": 401,
                    "channel_id": 100,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:01:00+00:00",
                    "content": "arrived during export",
                }
            ]
        return []


class MessageWindowGuildSource(ChannelGuildSource):
    def __init__(self, messages: list[Mapping[str, object]]) -> None:
        super().__init__()
        self.messages: list[dict[str, object]] = [dict(message) for message in messages]

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            return self.messages
        return await super().fetch_messages(channel_id)


class RateLimitedMessageGuildSource(ChannelGuildSource):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            self.calls += 1
            if self.calls == 1:
                raise discord.RateLimited(0)
            return [
                {
                    "id": 600,
                    "channel_id": 100,
                    "author_id": 111,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "after rate limit",
                }
            ]
        return await super().fetch_messages(channel_id)


class FailedChannelGuildSource(ChannelGuildSource):
    async def fetch_messages(self, channel_id: int) -> list[dict[str, object]]:
        if channel_id == 100:
            raise OSError("network unavailable")
        if channel_id == 101:
            return [
                {
                    "id": 601,
                    "channel_id": 101,
                    "author_id": 222,
                    "created_at": "2021-01-01T00:00:00+00:00",
                    "content": "other channel continues",
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
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["failures"] == []
    assert manifest["format_version"] == 1
    assert manifest["source_guild_id"] == "123"
    assert manifest["status"] == "complete"
    assert manifest["incomplete_channels"] == []
    assert isinstance(manifest["export_started_at"], str)
    assert isinstance(manifest["export_finished_at"], str)
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


def test_member_avatars_are_downloaded_once_and_referenced_relatively(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = AvatarMemberGuildSource()

    asyncio.run(export_guild(config, source))
    asyncio.run(export_guild(config, source))

    members = [
        json.loads(line)
        for line in (root / "members.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert members[0]["avatar_path"] == "media/avatars/111--avatar-alice.png"
    assert members[1]["avatar"] is None
    assert source.downloads == 1
    assert (
        root / "media" / "avatars" / "111--avatar-alice.png"
    ).read_bytes() == b"avatar-bytes"


def test_resume_appends_new_messages_after_the_durable_cursor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = ResumeGuildSource()

    asyncio.run(export_guild(config, source))
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    original_bytes = message_path.read_bytes()

    source.has_new_message = True
    asyncio.run(export_guild(config, source))

    assert source.full_channels.count(100) == 1
    assert (100, "201") in source.after_calls
    assert (100, "201") in source.after_calls[2:]
    assert (101, "0") in source.after_calls
    assert message_path.read_bytes().startswith(original_bytes)
    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == ["200", "201", "202"]
    assert json.loads((root / "state.json").read_text())["channels"]["100"] == {
        "complete": True,
        "last_message_id": "202",
        "last_message_timestamp": "2021-01-01T00:02:00+00:00",
    }


def test_interrupted_stream_leaves_a_durable_message_checkpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = InterruptingStreamGuildSource()
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(export_guild(config, source))

    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == ["700"]
    assert json.loads((root / "state.json").read_text())["channels"]["100"] == {
        "complete": False,
        "last_message_id": "700",
        "last_message_timestamp": "2021-01-01T00:00:00+00:00",
    }

    source.interrupt_after_first = False
    asyncio.run(export_guild(config, source))

    assert [cursor for channel_id, cursor in source.cursors if channel_id == 100] == [
        None,
        "700",
    ]
    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == [
        "700",
        "701",
    ]
    assert json.loads((root / "state.json").read_text())["channels"]["100"] == {
        "complete": True,
        "last_message_id": "701",
        "last_message_timestamp": "2021-01-01T00:01:00+00:00",
    }


def test_failed_media_is_in_manifest_before_message_append(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = FailedMediaThenInterruptSource()
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(export_guild(config, source))

    assert json.loads(message_path.read_text())["id"] == "710"
    manifest = json.loads((root / "manifest.json").read_text())
    assert _without_failure_timestamps(manifest["failures"]) == [
        {
            "channel_id": "100",
            "error": "OSError",
            "kind": "media",
            "message_id": "710",
            "media_id": "711",
            "operation": "attachment",
            "url": "https://cdn.example/bad",
        }
    ]


def test_interrupted_media_is_retried_before_message_checkpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = InterruptingMediaSource()
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(export_guild(config, source))

    assert not message_path.exists()
    assert not (root / "state.json").exists()

    source.interrupt_media = False
    asyncio.run(export_guild(config, source))

    message = json.loads(message_path.read_text())
    assert message["id"] == "720"
    media_path = (
        root / "channels" / "general--100" / "2021" / "media" / "720--721--retry.txt"
    )
    assert media_path.read_bytes() == b"retry bytes"
    assert json.loads((root / "state.json").read_text())["channels"]["100"] == {
        "complete": True,
        "last_message_id": "720",
        "last_message_timestamp": "2021-01-01T00:00:00+00:00",
    }


def test_channel_error_preserves_cursor_for_the_next_run(tmp_path: Path) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = ErrorAfterFirstMessageSource()

    asyncio.run(export_guild(config, source))

    state = json.loads((root / "state.json").read_text())["channels"]["100"]
    assert state == {
        "complete": False,
        "last_message_id": "730",
        "last_message_timestamp": "2021-01-01T00:00:00+00:00",
    }

    source.fail_after_first = False
    asyncio.run(export_guild(config, source))

    assert [cursor for channel_id, cursor in source.cursors if channel_id == 100] == [
        None,
        "730",
    ]
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == ["730", "731"]
    assert json.loads((root / "state.json").read_text())["channels"]["100"] == {
        "complete": True,
        "last_message_id": "731",
        "last_message_timestamp": "2021-01-01T00:01:00+00:00",
    }


def test_message_media_uses_stable_paths_and_reuses_existing_downloads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = MessageMediaGuildSource()

    asyncio.run(export_guild(config, source))
    asyncio.run(export_guild(config, source))

    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    messages = [json.loads(line) for line in message_path.read_text().splitlines()]

    assert messages[0]["attachments"][0]["local_path"] == (
        "channels/general--100/2021/media/700--701--same.txt"
    )
    assert messages[1]["attachments"][0]["local_path"] == (
        "channels/general--100/2021/media/702--703--same.txt"
    )
    embed_path = messages[1]["embeds"][0]["image"]["local_path"]
    assert embed_path.startswith("channels/general--100/2021/media/702--embed-")
    assert messages[1]["stickers"] == [{"id": "704", "name": "keep-metadata-only"}]
    assert source.downloads == [
        "https://cdn.example/701",
        "https://cdn.example/703",
        "https://cdn.example/embed.png",
    ]
    assert (root / messages[0]["attachments"][0]["local_path"]).read_bytes() == (
        b"https://cdn.example/701"
    )
    assert (root / embed_path).is_file()


def test_media_failures_are_durable_without_blocking_other_media_or_channels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = FailedMediaGuildSource()

    asyncio.run(export_guild(config, source))
    asyncio.run(export_guild(config, source))

    members = [
        json.loads(line) for line in (root / "members.jsonl").read_text().splitlines()
    ]
    messages = [
        json.loads(line)
        for line in (root / "channels" / "general--100" / "2021" / "messages.jsonl")
        .read_text()
        .splitlines()
    ]
    manifest = json.loads((root / "manifest.json").read_text())

    assert "avatar_download_error" in members[0]
    assert messages[0]["content"] == "retain this message"
    assert messages[0]["attachments"][0]["local_path"]
    assert "download_error" in messages[0]["attachments"][1]
    assert manifest["status"] == "complete"
    assert manifest["incomplete_channels"] == []
    assert _without_failure_timestamps(manifest["failures"]) == [
        {
            "kind": "media",
            "operation": "avatar",
            "user_id": "111",
            "url": "https://cdn.example/avatar-bad",
            "error": "OSError",
        },
        {
            "channel_id": "100",
            "error": "OSError",
            "kind": "media",
            "message_id": "800",
            "media_id": "802",
            "operation": "attachment",
            "url": "https://cdn.example/message-bad",
        },
    ]
    assert source.downloads.count("https://cdn.example/good") == 1
    assert source.downloads.count("https://cdn.example/avatar-bad") == 2
    assert source.downloads.count("https://cdn.example/message-bad") == 1


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
                "local_path": (
                    "channels/general--100/2021/media/300--301--evidence.txt"
                ),
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
    assert (
        root / "channels" / "general--100" / "2021" / "media" / "300--301--evidence.txt"
    ).read_bytes() == b"media"


def test_exporting_messages_performs_a_finite_catch_up_pass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = CatchUpMessageGuildSource()

    asyncio.run(export_guild(config, source))

    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == [
        "400",
        "401",
    ]
    assert source.catch_up_calls == 2


def test_exporting_messages_records_global_channel_progress(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, MessageGuildSource()))

    assert json.loads((root / "state.json").read_text()) == {
        "channels": {
            "100": {
                "complete": True,
                "last_message_id": "201",
                "last_message_timestamp": "2021-01-01T00:00:01+00:00",
            },
            "101": {
                "complete": True,
                "last_message_id": None,
                "last_message_timestamp": None,
            },
        },
        "version": 1,
    }


def test_exporting_messages_reports_replaceable_tty_channel_progress(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    progress = ProgressStream(tty=True)

    asyncio.run(export_guild(config, MessageGuildSource(), progress))

    output = progress.getvalue()
    assert output.count("\n") == 10
    assert output.count("\r\033[2K") == 15
    assert (
        "\r\033[2KChannel 1/2 (50%): general [100] — starting full history; 0 new messages"
        in output
    )
    assert (
        "\r\033[2KChannel 1/2 (50%): general [100] — exporting; 3 new messages"
        in output
    )
    assert (
        "\r\033[2KChannel 1/2 (50%): general [100] — complete; 3 new messages\n"
        in output
    )
    assert (
        "\r\033[2KChannel 2/2 (100%): discussion [101] — complete; 0 new messages\n"
        in output
    )


def test_exporting_messages_reports_plain_progress_without_terminal_controls(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    progress = ProgressStream(tty=False)

    asyncio.run(export_guild(config, MessageGuildSource(), progress))

    output = progress.getvalue()
    assert "\r" not in output
    assert "\033" not in output
    assert "Channel 1/2 (50%): general [100] — exporting; 3 new messages\n" in output
    assert "Channel 1/2 (50%): general [100] — complete; 3 new messages\n" in output
    assert "Channel 2/2 (100%): discussion [101] — complete; 0 new messages\n" in output


def test_progress_explains_archive_resume_and_durable_message_counts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = MessageWindowGuildSource(
        [
            {
                "id": 500,
                "channel_id": 100,
                "author_id": 111,
                "created_at": "2021-01-01T00:00:00+00:00",
                "content": "one message",
            }
        ]
    )
    first_progress = ProgressStream(tty=False)
    asyncio.run(export_guild(config, source, first_progress))

    first_output = first_progress.getvalue()
    assert "Starting new archive at " in first_output
    assert "Checkpoint summary: 0 complete, 0 partial, 2 missing" in first_output
    assert "starting full history; 0 new messages" in first_output
    assert "exporting; 1 new messages" in first_output
    assert "complete; 1 new messages" in first_output

    second_progress = ProgressStream(tty=False)
    asyncio.run(export_guild(config, source, second_progress))

    second_output = second_progress.getvalue()
    assert "Existing archive detected at " in second_output
    assert "Checkpoint summary: 2 complete, 0 partial, 0 missing" in second_output
    assert "checking after 500 at 2021-01-01T00:00:00+00:00; 0 new messages" in (
        second_output
    )
    assert "complete; 0 new messages" in second_output


def test_progress_reports_metadata_phases_and_thread_counts(tmp_path: Path) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    progress = ProgressStream(tty=False)

    asyncio.run(export_guild(config, MetadataProgressSource(), progress))

    output = progress.getvalue()
    phases = [
        "Starting new archive at ",
        "Fetching server metadata",
        "Fetching members and avatars",
        "Avatars: 0 downloaded, 0 reused, 0 failed",
        "Discovering channels, active threads, and archived threads",
        "Discovered 2 channels and 3 threads",
        "Writing metadata snapshots and manifest",
    ]
    positions = [output.index(phase) for phase in phases]
    assert positions == sorted(positions)
    assert "Channel 1/5 (20%): general [100]" in output
    assert "Channel 5/5 (100%): another-thread [104]" in output


def test_progress_reports_metadata_refresh_and_avatar_reuse(tmp_path: Path) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = AvatarMemberGuildSource()
    first_progress = ProgressStream(tty=False)
    asyncio.run(export_guild(config, source, first_progress))

    second_progress = ProgressStream(tty=False)
    asyncio.run(export_guild(config, source, second_progress))

    assert "Writing metadata snapshots and manifest" in first_progress.getvalue()
    assert "Avatars: 1 downloaded, 0 reused, 0 failed" in first_progress.getvalue()
    assert "Refreshing metadata snapshots and manifest" in second_progress.getvalue()
    assert "Avatars: 0 downloaded, 1 reused, 0 failed" in second_progress.getvalue()


def test_state_replacement_failure_keeps_previous_state_and_durable_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "export"
    root.mkdir()
    previous_state = {
        "channels": {
            "100": {
                "complete": False,
                "last_message_id": "198",
                "last_message_timestamp": "2020-12-31T23:59:00+00:00",
            }
        },
        "version": 1,
    }
    state_path = root / "state.json"
    state_path.write_text(json.dumps(previous_state), encoding="utf-8")
    config = Config(token="test-token", guild_id=123, export_root=root)

    original_replace = Path.replace

    def fail_state_replace(self: Path, target: Path) -> Path:
        if self.name == ".state.json.tmp":
            raise OSError("simulated interruption")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_state_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        asyncio.run(export_guild(config, MessageGuildSource()))

    assert json.loads(state_path.read_text()) == previous_state
    assert (root / "channels" / "general--100" / "2020" / "messages.jsonl").is_file()

    monkeypatch.undo()
    asyncio.run(export_guild(config, MessageGuildSource()))

    message_ids: list[str] = []
    for year in ("2020", "2021"):
        message_path = root / "channels" / "general--100" / year / "messages.jsonl"
        message_ids.extend(
            json.loads(line)["id"] for line in message_path.read_text().splitlines()
        )
    assert message_ids == ["199", "200", "201"]


def test_malformed_jsonl_is_reported_without_touching_the_channel(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    first_message: dict[str, object] = {
        "id": 500,
        "channel_id": 100,
        "author_id": 111,
        "created_at": "2021-01-01T00:00:00+00:00",
        "content": "already durable",
    }
    source = MessageWindowGuildSource([first_message])

    asyncio.run(export_guild(config, source))

    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    message_path.write_text(
        message_path.read_text() + '{"id":"truncated', encoding="utf-8"
    )
    malformed_bytes = message_path.read_bytes()

    asyncio.run(export_guild(config, source))

    assert message_path.read_bytes() == malformed_bytes
    state = json.loads((root / "state.json").read_text())["channels"]
    assert state["100"]["complete"] is False
    assert state["101"]["complete"] is True
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["incomplete_channels"] == ["100"]
    assert _without_failure_timestamps(manifest["failures"]) == [
        {
            "channel_id": "100",
            "error": "ArchiveFormatError",
            "operation": "messages",
            "path": str(message_path),
        }
    ]


def test_resume_uses_newest_valid_archive_message_when_state_is_behind(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = ResumeGuildSource()

    asyncio.run(export_guild(config, source))
    message_path = root / "channels" / "general--100" / "2021" / "messages.jsonl"
    message_path.write_text(
        message_path.read_text()
        + json.dumps(
            {
                "id": "202",
                "channel_id": "100",
                "author_id": "222",
                "created_at": "2021-01-01T00:02:00+00:00",
                "content": "already durable",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(export_guild(config, source))

    assert source.full_channels.count(100) == 1
    assert (100, "202") in source.after_calls[2:]
    assert [
        json.loads(line)["id"] for line in message_path.read_text().splitlines()
    ] == ["200", "201", "202"]


def test_resume_falls_back_to_full_history_when_state_cursor_is_absent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = ResumeGuildSource()

    asyncio.run(export_guild(config, source))
    state_path = root / "state.json"
    state = json.loads(state_path.read_text())
    state["channels"]["100"]["last_message_id"] = "999"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    asyncio.run(export_guild(config, source))

    assert source.full_channels.count(100) == 2


def test_rate_limit_is_retried_before_message_is_written(tmp_path: Path) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    source = RateLimitedMessageGuildSource()

    asyncio.run(export_guild(config, source))

    assert source.calls == 2
    assert (root / "channels" / "general--100" / "2021" / "messages.jsonl").is_file()
    assert (
        json.loads((root / "state.json").read_text())["channels"]["100"]["complete"]
        is True
    )


def test_permanent_channel_failure_is_recorded_without_stopping_other_channels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, FailedChannelGuildSource()))

    assert _without_failure_timestamps(
        json.loads((root / "manifest.json").read_text())["failures"]
    ) == [{"channel_id": "100", "error": "OSError", "operation": "messages"}]
    state = json.loads((root / "state.json").read_text())["channels"]
    assert state["100"]["complete"] is False
    assert state["101"]["complete"] is True
    assert (root / "channels" / "discussion--101" / "2021" / "messages.jsonl").is_file()


def test_failed_progress_and_manifest_include_current_error_details(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)
    progress = ProgressStream(tty=False)

    asyncio.run(export_guild(config, FailedChannelGuildSource(), progress))

    assert (
        "failed; 0 new messages (OSError: network unavailable)" in progress.getvalue()
    )
    failure = json.loads((root / "manifest.json").read_text())["failures"][0]
    assert failure["error"] == "OSError"
    assert datetime.fromisoformat(failure["occurred_at"]).tzinfo == UTC


def test_completed_manifest_keeps_archive_paths_portable_after_relocation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    config = Config(token="test-token", guild_id=123, export_root=root)

    asyncio.run(export_guild(config, MessageGuildSource()))
    relocated = tmp_path / "relocated"
    root.rename(relocated)

    manifest = json.loads((relocated / "manifest.json").read_text())

    assert manifest["status"] == "complete"
    assert manifest["incomplete_channels"] == []
    assert manifest["failures"] == []
    assert manifest["channels"] == [
        {
            "archive_path": "channels/general--100",
            "complete": True,
            "id": "100",
        },
        {
            "archive_path": "channels/discussion--101",
            "complete": True,
            "id": "101",
        },
    ]
    assert all(
        not Path(channel["archive_path"]).is_absolute()
        for channel in manifest["channels"]
    )
    assert (
        relocated / manifest["channels"][0]["archive_path"] / "channel.json"
    ).is_file()
