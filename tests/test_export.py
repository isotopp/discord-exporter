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
