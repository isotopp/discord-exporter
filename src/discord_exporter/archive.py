from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from .config import Config


class GuildSource(Protocol):
    async def fetch_guild(self, guild_id: int) -> Mapping[str, object]: ...

    async def fetch_members(self, guild_id: int) -> Sequence[Mapping[str, object]]: ...


async def export_guild(config: Config, source: GuildSource) -> None:
    normalized_guild = _stringify_ids(await source.fetch_guild(config.guild_id))
    if not isinstance(normalized_guild, Mapping):
        raise TypeError("Discord returned an invalid guild record")
    if normalized_guild.get("id") != str(config.guild_id):
        raise ValueError("Discord returned a different guild than requested")

    config.export_root.mkdir(parents=True, exist_ok=True)
    (config.export_root / "channels").mkdir(exist_ok=True)
    (config.export_root / "media" / "avatars").mkdir(parents=True, exist_ok=True)

    members = _member_records(await source.fetch_members(config.guild_id))

    _write_json(config.export_root / "server.json", normalized_guild)
    _write_jsonl(config.export_root / "members.jsonl", members)
    _write_json(
        config.export_root / "manifest.json",
        {
            "format_version": 1,
            "source_guild_id": str(config.guild_id),
            "status": "in_progress",
        },
    )
    _write_json(
        config.export_root / "state.json",
        {
            "version": 1,
            "channels": {},
        },
    )


def _stringify_ids(value: object, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {name: _stringify_ids(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_stringify_ids(item, key) for item in value]
    if isinstance(value, int) and _is_id_key(key):
        return str(value)
    return value


def _is_id_key(key: str | None) -> bool:
    return bool(key and (key == "id" or key == "roles" or key.endswith("_id")))


def _member_records(
    records: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for record in records:
        normalized = _stringify_ids(record)
        if not isinstance(normalized, Mapping) or not isinstance(
            normalized.get("id"), str
        ):
            raise TypeError("Discord returned an invalid member record")
        by_id[normalized["id"]] = normalized
    return [by_id[member_id] for member_id in sorted(by_id)]


def _write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _write_jsonl(path: Path, values: Sequence[object]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for value in values:
            json.dump(value, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
