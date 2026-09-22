from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO
from urllib.parse import urlparse

import discord

from .config import Config


class GuildSource(Protocol):
    async def fetch_guild(self, guild_id: int) -> Mapping[str, object]: ...

    async def fetch_members(self, guild_id: int) -> Sequence[Mapping[str, object]]: ...

    async def fetch_channels(self, guild_id: int) -> Sequence[Mapping[str, object]]: ...

    def iter_messages(
        self, channel_id: int, after_message_id: str | None
    ) -> AsyncIterator[Mapping[str, object]]: ...

    async def download_media(self, url: str) -> bytes: ...


class ArchiveFormatError(ValueError):
    def __init__(self, path: Path, line_number: int) -> None:
        self.path = path
        super().__init__(f"invalid JSONL at {path}:{line_number}")


class _Progress:
    def __init__(self, stream: TextIO, total: int) -> None:
        self.stream = stream
        self.total = total
        self.interactive = stream.isatty()

    def set_total(self, total: int) -> None:
        self.total = total

    def phase(self, message: str) -> None:
        if self.interactive:
            self.stream.write(f"\r\033[2K{message}\n")
        else:
            self.stream.write(f"{message}\n")
        self.stream.flush()

    def update(self, position: int, channel: Mapping[str, object], status: str) -> None:
        line = _progress_line(position, self.total, channel, status)
        if self.interactive:
            self.stream.write(f"\r\033[2K{line}")
        else:
            self.stream.write(f"{line}\n")
        self.stream.flush()

    def finish(self, position: int, channel: Mapping[str, object], status: str) -> None:
        self.update(position, channel, status)
        if self.interactive:
            self.stream.write("\n")
            self.stream.flush()

    def abort(self) -> None:
        if self.interactive:
            self.stream.write("\n")
            self.stream.flush()


def _progress_line(
    position: int, total: int, channel: Mapping[str, object], status: str
) -> str:
    name = _display_channel_name(channel.get("name"))
    return (
        f"Channel {position}/{total} ({position * 100 // total}%): "
        f"{name} [{channel['id']}] — {status}"
    )


def _display_channel_name(value: object) -> str:
    name = str(value or "channel")
    return "".join(character if character.isprintable() else "?" for character in name)


async def export_guild(
    config: Config, source: GuildSource, progress_stream: TextIO | None = None
) -> None:
    progress = _Progress(
        sys.stderr if progress_stream is None else progress_stream,
        0,
    )
    state_path = config.export_root / "state.json"
    if state_path.is_file():
        progress.phase(
            f"Existing archive detected at {config.export_root}; loading state.json"
        )
    elif config.export_root.exists():
        progress.phase(
            f"Archive directory found at {config.export_root}; no state.json checkpoint"
        )
    else:
        progress.phase(f"Starting new archive at {config.export_root}")

    config.export_root.mkdir(parents=True, exist_ok=True)
    (config.export_root / "channels").mkdir(exist_ok=True)
    (config.export_root / "media" / "avatars").mkdir(parents=True, exist_ok=True)
    state = _load_state(state_path)
    channel_states = state["channels"]
    if not isinstance(channel_states, dict):
        raise TypeError("Archive state has invalid channel entries")

    request_policy = config.request_policy
    progress.phase("Fetching server metadata")
    normalized_guild = _stringify_ids(
        await _with_retries(
            lambda: source.fetch_guild(config.guild_id), request_policy.sleep
        )
    )
    if not isinstance(normalized_guild, Mapping):
        raise TypeError("Discord returned an invalid guild record")
    if normalized_guild.get("id") != str(config.guild_id):
        raise ValueError("Discord returned a different guild than requested")

    media_failures = _load_previous_failures(config.export_root / "manifest.json")
    progress.phase("Fetching members and avatars")
    members, avatar_counts = await _download_member_avatars(
        _member_records(
            await _with_retries(
                lambda: source.fetch_members(config.guild_id), request_policy.sleep
            )
        ),
        source,
        config.export_root,
        request_policy.sleep,
        media_failures,
    )
    progress.phase(
        "Avatars: "
        f"{avatar_counts['downloaded']} downloaded, "
        f"{avatar_counts['reused']} reused, "
        f"{avatar_counts['failed']} failed"
    )
    progress.phase("Discovering channels, active threads, and archived threads")
    channels = _channel_records(
        await _with_retries(
            lambda: source.fetch_channels(config.guild_id), request_policy.sleep
        ),
        config.export_root,
    )
    ordinary_channels = sum(
        "thread" not in str(channel.get("type", "")) for channel in channels
    )
    threads = len(channels) - ordinary_channels
    progress.phase(f"Discovered {ordinary_channels} channels and {threads} threads")
    manifest: dict[str, object] = {
        "channels": _manifest_channels(channels, channel_states),
        "export_finished_at": None,
        "export_started_at": datetime.now(UTC).isoformat(),
        "failures": media_failures,
        "format_version": 1,
        "incomplete_channels": _incomplete_channel_ids(channels, channel_states),
        "source_guild_id": str(config.guild_id),
        "status": "in_progress",
    }
    failures = manifest["failures"]
    if not isinstance(failures, list):
        raise TypeError("Archive manifest has invalid failures")
    progress.set_total(len(channels))
    metadata_paths = [
        config.export_root / "server.json",
        config.export_root / "members.jsonl",
        config.export_root / "channels.jsonl",
        config.export_root / "manifest.json",
    ]
    metadata_action = (
        "Refreshing" if any(path.exists() for path in metadata_paths) else "Writing"
    )
    progress.phase(f"{metadata_action} metadata snapshots and manifest")
    complete, partial, missing = _checkpoint_summary(channels, channel_states)
    progress.phase(
        f"Checkpoint summary: {complete} complete, {partial} partial, {missing} missing"
    )
    _write_json(config.export_root / "server.json", normalized_guild)
    _write_jsonl(config.export_root / "members.jsonl", members)
    _write_jsonl(config.export_root / "channels.jsonl", channels)
    _write_json(config.export_root / "manifest.json", manifest)

    for position, channel in enumerate(channels, 1):
        channel_id = str(channel["id"])
        new_count = 0
        try:
            if channel.get("accessible", True) is False:
                progress.update(
                    position, channel, "starting full history; 0 new messages"
                )
                channel_states[channel_id] = _incomplete_channel_state()
                if channel.get("access_error"):
                    failures.append(
                        _failure_record("channel", channel_id, channel["access_error"])
                    )
                progress.finish(position, channel, "skipped")
            else:
                channel_path = config.export_root / str(channel["archive_path"])
                existing_records = _read_existing_messages(
                    sorted(channel_path.glob("*/messages.jsonl"))
                )
                resume_cursor, resume_status = _resume_decision(
                    existing_records, channel_states.get(channel_id)
                )
                progress.update(position, channel, f"{resume_status}; 0 new messages")
                existing_ids = {_record_id(record) for record in existing_records}
                archived_records = list(existing_records)
                async for raw_message in _iter_channel_messages(
                    source,
                    int(channel_id),
                    request_policy.sleep,
                    resume_cursor,
                ):
                    messages = _message_records([raw_message], channel_id)
                    for year, records in messages.items():
                        for record in records:
                            message_id = _record_id(record)
                            if message_id in existing_ids:
                                continue
                            previous_failure_count = len(media_failures)
                            enriched_messages = await _download_message_media(
                                {year: [record]},
                                channel_path,
                                config.export_root,
                                source,
                                request_policy.sleep,
                                media_failures,
                                channel_id,
                            )
                            if len(media_failures) > previous_failure_count:
                                media_failures[:] = _unique_failures(media_failures)
                                _write_json(
                                    config.export_root / "manifest.json", manifest
                                )
                            enriched_record = enriched_messages[year][0]
                            year_path = channel_path / str(year)
                            year_path.mkdir(parents=True, exist_ok=True)
                            _append_jsonl(
                                year_path / "messages.jsonl", [enriched_record]
                            )
                            existing_ids.add(message_id)
                            archived_records.append(enriched_record)
                            channel_states[channel_id] = _message_channel_state(
                                enriched_record, complete=False
                            )
                            _write_state_atomic(state_path, state)
                            new_count += 1
                            progress.update(
                                position,
                                channel,
                                f"exporting; {new_count} new messages",
                            )
                archived_messages = _message_records(archived_records, channel_id)
                channel_states[channel_id] = _complete_channel_state(archived_messages)
                progress.finish(
                    position, channel, f"complete; {new_count} new messages"
                )
        except (discord.DiscordException, OSError, TypeError, ValueError) as error:
            failure = _failure_record("messages", channel_id, error)
            if isinstance(error, ArchiveFormatError):
                failure["path"] = str(error.path)
            failures.append(failure)
            channel_states[channel_id] = _incomplete_channel_state(
                channel_states.get(channel_id)
            )
            progress.finish(
                position,
                channel,
                f"failed; {new_count} new messages ({_progress_error(error)})",
            )
        except KeyboardInterrupt:
            progress.abort()
            raise
        manifest["channels"] = _manifest_channels(channels, channel_states)
        manifest["incomplete_channels"] = _incomplete_channel_ids(
            channels, channel_states
        )
        _write_state_atomic(state_path, state)
        _write_json(config.export_root / "manifest.json", manifest)
    media_failures[:] = _unique_failures(media_failures)
    incomplete_channels = _incomplete_channel_ids(channels, channel_states)
    manifest["incomplete_channels"] = incomplete_channels
    manifest["export_finished_at"] = datetime.now(UTC).isoformat()
    manifest["status"] = "complete" if not incomplete_channels else "incomplete"
    _write_json(config.export_root / "manifest.json", manifest)
    _write_state_atomic(state_path, state)


async def _with_retries[T](
    operation: Callable[[], Awaitable[T]],
    sleep: Callable[[float], Awaitable[None]],
    attempts: int = 3,
) -> T:
    for attempt in range(attempts):
        try:
            return await operation()
        except discord.RateLimited as error:
            if attempt == attempts - 1:
                raise
            await sleep(error.retry_after)
        except discord.DiscordServerError:
            if attempt == attempts - 1:
                raise
            await sleep(float(2**attempt))
    raise RuntimeError("retry loop exhausted")


def _failure_record(operation: str, object_id: str, error: object) -> dict[str, str]:
    return _timestamped_failure(
        {
            "operation": operation,
            "channel_id": object_id,
            "error": type(error).__name__,
        }
    )


def _progress_error(error: object) -> str:
    details = str(error).replace("\r", " ").replace("\n", " ").strip()
    if len(details) > 200:
        details = f"{details[:197]}..."
    if not details:
        return type(error).__name__
    return f"{type(error).__name__}: {details}"


def _timestamped_failure(failure: Mapping[str, str]) -> dict[str, str]:
    return {
        **failure,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _load_previous_failures(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or not isinstance(value.get("failures"), list):
        return []
    failures: list[dict[str, str]] = []
    for failure in value["failures"]:
        if isinstance(failure, Mapping) and all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in failure.items()
        ):
            failures.append(dict(failure))
    return failures


def _unique_failures(
    failures: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    indexes: dict[tuple[tuple[str, str], ...], int] = {}
    for failure in failures:
        key = tuple(
            sorted(
                (name, value)
                for name, value in failure.items()
                if name != "occurred_at"
            )
        )
        if key not in indexes:
            indexes[key] = len(unique)
            unique.append(failure)
        elif "occurred_at" in failure:
            unique[indexes[key]] = failure
    return unique


async def _download_member_avatars(
    members: Sequence[Mapping[str, object]],
    source: GuildSource,
    export_root: Path,
    sleep: Callable[[float], Awaitable[None]],
    failures: list[dict[str, str]],
) -> tuple[list[Mapping[str, object]], dict[str, int]]:
    avatars_root = export_root / "media" / "avatars"
    enriched_members: list[Mapping[str, object]] = []
    counts = {"downloaded": 0, "reused": 0, "failed": 0}
    for member in members:
        enriched = dict(member)
        avatar_url = member.get("avatar_url")
        if isinstance(avatar_url, str) and avatar_url:
            member_id = str(member["id"])
            avatar_id = str(member.get("avatar") or "avatar")
            suffix = Path(urlparse(avatar_url).path).suffix or ".bin"
            avatar_path = avatars_root / f"{member_id}--{avatar_id}{suffix}"
            try:
                if avatar_path.is_file():
                    counts["reused"] += 1
                else:
                    avatar_path.write_bytes(
                        await _with_retries(
                            lambda avatar_url=avatar_url: source.download_media(
                                avatar_url
                            ),
                            sleep,
                        )
                    )
                    counts["downloaded"] += 1
                enriched["avatar_path"] = avatar_path.relative_to(
                    export_root
                ).as_posix()
            except (discord.DiscordException, OSError) as error:
                enriched["avatar_download_error"] = {
                    "url": avatar_url,
                    "error": type(error).__name__,
                }
                failures.append(
                    _timestamped_failure(
                        {
                            "kind": "media",
                            "operation": "avatar",
                            "user_id": member_id,
                            "url": avatar_url,
                            "error": type(error).__name__,
                        }
                    )
                )
                counts["failed"] += 1
        enriched_members.append(enriched)
    return enriched_members, counts


async def _download_message_media(
    messages: Mapping[int, Sequence[Mapping[str, object]]],
    channel_path: Path,
    export_root: Path,
    source: GuildSource,
    sleep: Callable[[float], Awaitable[None]],
    failures: list[dict[str, str]],
    channel_id: str,
) -> dict[int, list[Mapping[str, object]]]:
    enriched_messages: dict[int, list[Mapping[str, object]]] = {}
    for year, records in messages.items():
        media_root = channel_path / str(year) / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        enriched_year: list[Mapping[str, object]] = []
        for message in records:
            message_id = str(message["id"])
            enriched = dict(message)
            attachments = message.get("attachments")
            if isinstance(attachments, list):
                enriched["attachments"] = [
                    await _download_attachment(
                        attachment,
                        message_id,
                        media_root,
                        export_root,
                        source,
                        sleep,
                        failures,
                        channel_id,
                        index,
                    )
                    for index, attachment in enumerate(attachments)
                ]
            embeds = message.get("embeds")
            if isinstance(embeds, list):
                enriched["embeds"] = [
                    await _download_embed_images(
                        embed,
                        message_id,
                        media_root,
                        export_root,
                        source,
                        sleep,
                        failures,
                        channel_id,
                    )
                    for embed in embeds
                ]
            enriched_year.append(enriched)
        enriched_messages[year] = enriched_year
    return enriched_messages


async def _download_attachment(
    attachment: object,
    message_id: str,
    media_root: Path,
    export_root: Path,
    source: GuildSource,
    sleep: Callable[[float], Awaitable[None]],
    failures: list[dict[str, str]],
    channel_id: str,
    index: int,
) -> object:
    if not isinstance(attachment, Mapping):
        return attachment
    enriched = dict(attachment)
    url = attachment.get("url")
    if not isinstance(url, str) or not url:
        return enriched
    attachment_id = str(attachment.get("id") or f"attachment-{index}")
    filename = _safe_media_name(
        Path(str(attachment.get("filename") or "attachment")).name
    )
    path = media_root / f"{message_id}--{attachment_id}--{filename}"
    try:
        if not path.is_file():
            path.write_bytes(
                await _with_retries(lambda url=url: source.download_media(url), sleep)
            )
        enriched["local_path"] = path.relative_to(export_root).as_posix()
    except (discord.DiscordException, OSError) as error:
        enriched["download_error"] = {
            "url": url,
            "error": type(error).__name__,
        }
        failures.append(
            _timestamped_failure(
                {
                    "channel_id": channel_id,
                    "error": type(error).__name__,
                    "kind": "media",
                    "message_id": message_id,
                    "media_id": attachment_id,
                    "operation": "attachment",
                    "url": url,
                }
            )
        )
    return enriched


async def _download_embed_images(
    embed: object,
    message_id: str,
    media_root: Path,
    export_root: Path,
    source: GuildSource,
    sleep: Callable[[float], Awaitable[None]],
    failures: list[dict[str, str]],
    channel_id: str,
) -> object:
    if not isinstance(embed, Mapping):
        return embed
    enriched = dict(embed)
    for key in ("image", "thumbnail"):
        image = embed.get(key)
        if not isinstance(image, Mapping):
            continue
        url = image.get("url")
        if not isinstance(url, str) or not url:
            continue
        asset_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        suffix = Path(urlparse(url).path).suffix or ".bin"
        path = media_root / f"{message_id}--embed-{asset_id}{suffix}"
        enriched_image = dict(image)
        try:
            if not path.is_file():
                path.write_bytes(
                    await _with_retries(
                        lambda url=url: source.download_media(url), sleep
                    )
                )
            enriched_image["local_path"] = path.relative_to(export_root).as_posix()
        except (discord.DiscordException, OSError) as error:
            enriched_image["download_error"] = {
                "url": url,
                "error": type(error).__name__,
            }
            failures.append(
                _timestamped_failure(
                    {
                        "channel_id": channel_id,
                        "error": type(error).__name__,
                        "kind": "media",
                        "message_id": message_id,
                        "media_id": asset_id,
                        "operation": "embed_image",
                        "url": url,
                    }
                )
            )
        enriched[key] = enriched_image
    return enriched


def _safe_media_name(name: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in ".-_" else "-"
        for character in name
    ).strip(".-")
    return safe or "attachment"


def _manifest_channels(
    channels: Sequence[Mapping[str, object]],
    channel_states: Mapping[str, object],
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for channel in channels:
        channel_id = str(channel["id"])
        state = channel_states.get(channel_id)
        complete = isinstance(state, Mapping) and state.get("complete") is True
        summaries.append(
            {
                "archive_path": str(channel["archive_path"]),
                "complete": complete,
                "id": channel_id,
            }
        )
    return summaries


def _incomplete_channel_ids(
    channels: Sequence[Mapping[str, object]],
    channel_states: Mapping[str, object],
) -> list[str]:
    incomplete: list[str] = []
    for channel in channels:
        state = channel_states.get(str(channel["id"]))
        if not isinstance(state, Mapping) or state.get("complete") is not True:
            incomplete.append(str(channel["id"]))
    return incomplete


def _stringify_ids(value: object, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {name: _stringify_ids(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_stringify_ids(item, key) for item in value]
    if isinstance(value, int) and _is_id_key(key):
        return str(value)
    return value


def _is_id_key(key: str | None) -> bool:
    return bool(
        key
        and (
            key == "id"
            or key in {"mention_channels", "mention_roles", "roles"}
            or key.endswith(("_id", "_ids"))
        )
    )


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


def _channel_records(
    records: Sequence[Mapping[str, object]], export_root: Path
) -> list[Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    channels_root = export_root / "channels"
    for record in records:
        normalized = _stringify_ids(record)
        if not isinstance(normalized, Mapping) or not isinstance(
            normalized.get("id"), str
        ):
            raise TypeError("Discord returned an invalid channel record")

        channel_id = normalized["id"]
        channel_path = _channel_path(channels_root, channel_id, normalized.get("name"))
        enriched = dict(normalized)
        enriched["archive_path"] = channel_path.relative_to(export_root).as_posix()
        by_id[channel_id] = enriched

    ordered = [by_id[channel_id] for channel_id in sorted(by_id)]
    for record in ordered:
        channel_path = export_root / str(record["archive_path"])
        channel_path.mkdir(parents=True, exist_ok=True)
        _write_json(channel_path / "channel.json", record)
    return ordered


def _channel_path(channels_root: Path, channel_id: str, channel_name: object) -> Path:
    existing = sorted(channels_root.glob(f"*--{channel_id}"))
    if existing:
        return existing[0]

    name = str(channel_name or "channel").strip().lower()
    safe_name = (
        "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in name
        ).strip("-")
        or "channel"
    )
    return channels_root / f"{safe_name}--{channel_id}"


def _message_records(
    records: Sequence[Mapping[str, object]], channel_id: str
) -> dict[int, list[Mapping[str, object]]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for record in records:
        normalized = _stringify_ids(record)
        if not isinstance(normalized, Mapping):
            raise TypeError("Discord returned an invalid message record")
        message_id = normalized.get("id")
        if not isinstance(message_id, str):
            raise TypeError("Discord returned a message without an ID")
        if normalized.get("channel_id") != channel_id:
            raise ValueError("Discord returned a message for a different channel")
        _message_timestamp(normalized)
        by_id[message_id] = normalized

    by_year: dict[int, list[Mapping[str, object]]] = {}
    for record in by_id.values():
        year = _message_timestamp(record).year
        by_year.setdefault(year, []).append(record)
    for records_in_year in by_year.values():
        records_in_year.sort(
            key=lambda record: (_message_timestamp(record), str(record["id"]))
        )
    return by_year


async def _iter_channel_messages(
    source: GuildSource,
    channel_id: int,
    sleep: Callable[[float], Awaitable[None]],
    after_message_id: str | None = None,
) -> AsyncIterator[Mapping[str, object]]:
    cursor = after_message_id
    attempt = 0
    while True:
        try:
            async for record in source.iter_messages(channel_id, cursor):
                cursor = _record_id(record)
                yield record
            return
        except discord.RateLimited as error:
            if attempt == 2:
                raise
            attempt += 1
            await sleep(error.retry_after)
        except discord.DiscordServerError:
            if attempt == 2:
                raise
            attempt += 1
            await sleep(float(2 ** (attempt - 1)))


def _resume_decision(
    existing_records: Sequence[Mapping[str, object]], channel_state: object
) -> tuple[str | None, str]:
    last_message_id = (
        channel_state.get("last_message_id")
        if isinstance(channel_state, Mapping)
        else None
    )
    existing_ids = {_record_id(record) for record in existing_records}
    if last_message_id is not None and last_message_id not in existing_ids:
        return (
            None,
            f"starting full history; checkpoint {last_message_id} is absent from JSONL",
        )
    if existing_records:
        latest = _latest_message_record(existing_records)
        latest_id = _record_id(latest)
        latest_timestamp = latest.get("created_at")
        if not isinstance(latest_timestamp, str):
            raise TypeError("Archive message has an invalid creation timestamp")
        if isinstance(channel_state, Mapping) and channel_state.get("complete") is True:
            return latest_id, f"checking after {latest_id} at {latest_timestamp}"
        if last_message_id is not None and last_message_id != latest_id:
            return (
                latest_id,
                (
                    f"resuming after {latest_id} at {latest_timestamp}; "
                    f"JSONL is ahead of checkpoint {last_message_id}"
                ),
            )
        return latest_id, f"resuming after {latest_id} at {latest_timestamp}"
    if isinstance(channel_state, Mapping) and channel_state.get("complete") is True:
        return "0", "checking empty archive"
    return None, "starting full history"


def _record_id(record: Mapping[str, object]) -> str:
    normalized = _stringify_ids(record)
    if not isinstance(normalized, Mapping) or not isinstance(normalized.get("id"), str):
        raise TypeError("Discord returned a message without an ID")
    return normalized["id"]


def _latest_message_record(
    records: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    normalized: list[Mapping[str, object]] = []
    for record in records:
        value = _stringify_ids(record)
        if not isinstance(value, Mapping):
            raise TypeError("Discord returned an invalid message record")
        normalized.append(value)
    latest = max(
        normalized,
        key=lambda record: (_message_timestamp(record), _record_id(record)),
    )
    return latest


def _message_timestamp(record: Mapping[str, object]) -> datetime:
    value = record.get("created_at")
    if not isinstance(value, str):
        raise TypeError("Discord returned a message without a creation timestamp")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _read_existing_messages(paths: Sequence[Path]) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as file:
            lines = file.readlines()
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line.strip():
                continue
            if index == len(lines) - 1 and not raw_line.endswith(("\n", "\r")):
                raise ArchiveFormatError(path, index + 1)
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise ArchiveFormatError(path, index + 1) from None
            if not isinstance(value, Mapping):
                raise TypeError("Archive JSONL contains an invalid message record")
            records.append(value)
    return records


def _load_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"version": 1, "channels": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Archive state has an unsupported format")
    if not isinstance(value.get("channels"), dict):
        raise TypeError("Archive state has invalid channel entries")
    return value


def _incomplete_channel_state(previous: object = None) -> dict[str, object]:
    if isinstance(previous, Mapping):
        return {
            "last_message_id": previous.get("last_message_id"),
            "last_message_timestamp": previous.get("last_message_timestamp"),
            "complete": False,
        }
    return _message_channel_state(None, complete=False)


def _checkpoint_summary(
    channels: Sequence[Mapping[str, object]], channel_states: Mapping[str, object]
) -> tuple[int, int, int]:
    complete = partial = missing = 0
    for channel in channels:
        state = channel_states.get(str(channel["id"]))
        if not isinstance(state, Mapping):
            missing += 1
        elif state.get("complete") is True:
            complete += 1
        else:
            partial += 1
    return complete, partial, missing


def _message_channel_state(
    message: Mapping[str, object] | None, *, complete: bool
) -> dict[str, object]:
    if message is None:
        return {
            "last_message_id": None,
            "last_message_timestamp": None,
            "complete": complete,
        }
    timestamp = message.get("created_at")
    if not isinstance(timestamp, str):
        raise TypeError("Discord returned a message without a creation timestamp")
    return {
        "last_message_id": _record_id(message),
        "last_message_timestamp": timestamp,
        "complete": complete,
    }


def _complete_channel_state(
    messages: Mapping[int, Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    records = [record for year in messages.values() for record in year]
    if not records:
        return {
            "last_message_id": None,
            "last_message_timestamp": None,
            "complete": True,
        }
    latest = max(
        records,
        key=lambda record: (_message_timestamp(record), _record_id(record)),
    )
    timestamp = latest.get("created_at")
    if not isinstance(timestamp, str):
        raise TypeError("Discord returned a message without a creation timestamp")
    return {
        "last_message_id": _record_id(latest),
        "last_message_timestamp": timestamp,
        "complete": True,
    }


def _write_state_atomic(path: Path, state: Mapping[str, object]) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    _write_json(temporary_path, state)
    temporary_path.replace(path)


def _write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _write_jsonl(path: Path, values: Sequence[object]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for value in values:
            json.dump(value, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")


def _append_jsonl(path: Path, values: Sequence[object]) -> None:
    with path.open("a", encoding="utf-8") as file:
        for value in values:
            json.dump(value, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
