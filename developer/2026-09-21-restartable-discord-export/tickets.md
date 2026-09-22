# Tickets: Restartable Discord server export

## Public interface proposed for approval

The user-facing entry point remains:

```console
uv run discord-exporter
```

The command reads configuration from the process environment. It first loads
`.env` from the current working directory, or `~/.discord-export.env` when the
local file does not exist, using `python-dotenv` with existing environment
values taking precedence. This is the default behavior of `load_dotenv()`.

Required environment variables:

- `DISCORD_TOKEN`
- `DISCORD_GUILD_ID`
- `DISCORD_EXPORT_ROOT`

Request-policy environment variables:

- `DISCORD_DELAY_MIN_SECONDS`
- `DISCORD_DELAY_MAX_SECONDS`
- `DISCORD_USER_AGENT`

The archive files and directories defined in `user-stories.md` are also public
interfaces. Discord IDs are represented as JSON strings throughout them.

## Test approach

Each ticket is one vertical TDD slice. Start with one failing test of observable
CLI or archive behavior, add only enough implementation to pass it, and repeat
one behavior at a time within the ticket. Tests use the public command and its
archive output. Test doubles are limited to the Discord/HTTP service, time, and
randomness boundaries; internal exporter modules are not mocked.

Every completed ticket must pass:

```console
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

## 1. Load and validate operator configuration

Outcome: the operator can start the command from environment-backed
configuration without exposing credentials.

Observable behavior:

- Existing process environment values override values in an environment file.
- A local `.env` is preferred over `~/.discord-export.env`.
- The command reports missing or invalid required settings and exits non-zero.
- The token is absent from errors and normal output.
- The configured export root may already contain a partial archive.

First TDD slice: invoke the command with a local environment file and an
overriding process value, and observe that the selected guild and output root
reach the Discord boundary while the token never appears in output.

## 2. Export one server as an end-to-end tracer bullet

Outcome: one command run against an accessible server produces the smallest
valid archive and proves the complete CLI-to-filesystem path.

Observable behavior:

- An inaccessible or unknown configured guild produces a clear non-zero exit.
- An accessible guild produces `server.json`, `manifest.json`, `state.json`,
  and the archive root directories.
- `server.json` contains the guild ID, name, available native metadata, roles,
  and custom-emoji metadata without credentials.
- The initial manifest identifies the archive format version and source guild.
- JSON output is deterministic for the same Discord data.

First TDD slice: expose one empty guild through the Discord boundary, run the
command, and inspect the resulting archive only through its documented files.

## 3. Export the member identity catalogue

Outcome: the handoff contains the source identities needed for later mapping.

Observable behavior:

- `members.jsonl` contains one record for every member visible to the bot.
- Records retain available native Discord identity and membership fields,
  including username, display name, avatar metadata, roles, and join time.
- Member and role IDs are strings and roles resolve to the server catalogue.
- Re-running without source changes does not duplicate members.

First TDD slice: export a guild with two members sharing a display name and
verify that their distinct Discord IDs remain the canonical identities.

## 4. Discover channels, threads, and forum posts

Outcome: every accessible message-bearing location has a stable archive
identity and enough metadata for target mapping.

Observable behavior:

- `channels.jsonl` includes accessible text channels, message-capable voice
  channels, active and archived threads, and forum posts exposed by Discord.
- Each location has a `channel-name--channel-id/channel.json` directory and
  metadata record.
- Parent, owner, and related-channel IDs are strings and resolve within the
  exported catalogue when their source objects are visible.
- Renaming a channel does not create a second identity for the same ID.
- An inaccessible location is recorded and does not stop other locations.

First TDD slice: export one text channel and one child thread, then verify the
catalogue and parent relationship through archive files.

## 5. Export chronological yearly message JSONL

Outcome: the exporter writes a streamable, deterministic message history.

Observable behavior:

- Messages are assigned to the year of their UTC creation timestamp.
- Each yearly `messages.jsonl` is oldest-first with one valid JSON object per
  line.
- Source message, channel, author, and related IDs are strings.
- A second run does not duplicate already exported message IDs.
- Empty years are not created.

First TDD slice: export messages spanning a UTC year boundary and observe one
chronological record in each expected yearly file.

## 6. Preserve migration-relevant message relationships

Outcome: the archive retains enough Discord structure for `chatto.run` to
reconstruct conversations and mapped authorship.

Observable behavior:

- Message records retain native Discord names and nesting where practical.
- Content, edit timestamps, mentions, replies, embeds, reactions, attachment
  metadata, and available message-type data are preserved.
- References use source IDs and can cross channel and yearly file boundaries.
- Unsupported or absent optional fields do not remove supported data or abort
  the channel export.

First TDD slice: export a reply containing a user mention, embed, reaction,
and attachment metadata, then resolve each relationship using archive IDs.

## 7. Catch up with messages created during export

Outcome: a finite run includes messages that arrive while older history is
being downloaded.

Observable behavior:

- After historical pages are exhausted, the exporter checks for messages newer
  than its current cursor.
- Messages appearing during the run are appended in chronological order.
- The channel completes after a catch-up check returns no newer messages.
- The command does not become a continuous mirroring process.

First TDD slice: have the Discord boundary reveal a new message after the
historical page, then return an empty catch-up page and verify both messages
are present before the channel is complete.

## 8. Persist global progress atomically

Outcome: durable archive data and the single global cursor state cannot be
advanced out of order.

Observable behavior:

- `state.json` is keyed by channel ID and records each channel's last durable
  message ID and completion state.
- State advances only after the corresponding JSONL records and either media
  results or recorded media failures are durable.
- State replacement leaves a complete old or new JSON document if execution
  stops during the update.
- Completed channels remain recorded for audit and subsequent runs.

First TDD slice: interrupt execution immediately before state replacement and
observe durable messages with the previous valid state, then resume without
skipping them.

## 9. Recover cleanly from an interrupted JSONL append

Outcome: restart is at-least-once internally but exactly-once in the completed
archive.

Observable behavior:

- A partial final JSONL line is detected and removed or replaced on resume.
- Records written before a state update are recognized by source message ID
  and are not duplicated.
- Missing archive records are not skipped merely because `state.json` claims a
  later cursor.
- Resume continues other completed and incomplete channels correctly without
  depending on Discord channel order.

First TDD slice: start from an archive containing one durable record, one
truncated line, and stale state, then resume to a valid deduplicated JSONL.

## 10. Pace and identify outbound requests

Outcome: externally visible requests follow the configured moderated request
policy.

Observable behavior:

- Each exporter-controlled HTTPS request is separated by a random delay within
  the configured minimum and maximum.
- Discord API requests retain discord.py's native bot headers; media downloads
  use the configured Firefox user agent with gzip/deflate and language headers.
- Invalid delay ranges or a missing user agent fail configuration validation.
- Tests observe request timing and headers through the external boundary with
  controlled time and randomness.

First TDD slice: run two requests with deterministic boundary time and
randomness, then observe the configured delay range and header profile.

## 11. Honor rate limits and isolate request failures

Outcome: transient Discord or download failures do not corrupt progress or
unnecessarily stop unrelated channels.

Observable behavior:

- Rate-limit responses delay further requests for the indicated interval.
- Transient failures retry with bounded backoff and a finite attempt limit.
- Permanent failures record the source operation and object ID without message
  content or credentials.
- A failed or inaccessible channel does not abort export of another channel.
- Failed work never advances the affected channel beyond durable archive data.

First TDD slice: return a rate limit, then success, and observe one eventual
archive record with no premature state advancement.

## 12. Finalize a verifiable migration handoff

Outcome: the completed archive explains what it contains and supplies all
source-side mapping inputs without querying Discord again.

Observable behavior:

- `manifest.json` records the schema version, source guild, export timestamps,
  included channel IDs, completion states, inaccessible content, and failures.
- Server, member, channel, thread, role, emoji, message, reply, and mention
  relationships can be resolved from source IDs in the archive or are marked
  unavailable.
- Every archive path stored in JSON is relative to the export root.
- Moving the export directory does not invalidate stored paths.
- The manifest distinguishes unfinished channels from recorded media failures
  that do not block completion.

First TDD slice: export a small related fixture, move its archive directory,
and verify all catalogued relationships and paths from archive data alone.

## 13. Download and reference member avatars

Outcome: member identity records no longer depend on Discord-hosted avatar
URLs.

Observable behavior:

- Accessible avatars are stored below `media/avatars` with stable user and
  asset identifiers in their filenames.
- Member records retain original avatar metadata and an export-root-relative
  local path.
- Existing matching avatar files are reused on resume.
- Users without downloadable avatars remain valid member records.

First TDD slice: export two users whose source filenames collide and verify
distinct stable files and relative member references.

## 14. Download and reference message files and images

Outcome: message attachments and images are portable while excluded media
types remain metadata-only.

Observable behavior:

- Accessible attachments, files, and message or embed images are stored beside
  the referencing yearly JSONL under `media`.
- Filenames include stable message and attachment or asset IDs and cannot
  collide when source names repeat.
- Message records retain original metadata and relative local paths.
- Matching existing files are reused rather than downloaded again.
- Emoji and sticker assets are not downloaded.

First TDD slice: export two messages with colliding attachment names plus an
emoji and sticker, then observe two distinct local files and no downloaded
emoji or sticker assets.

## 15. Record media failures without blocking completion

Outcome: missing media is explicit and retryable while the remaining archive
can complete.

Observable behavior:

- Failed media downloads are recorded in the corresponding member or message
  record and in `manifest.json` with their source IDs and URLs.
- A media failure does not discard its member or message record.
- Other media and channels continue after a permanent failure.
- A channel may complete once every media item has either a durable local file
  or a durable failure record.
- Re-running may retry a recorded failure without duplicating successful
  downloads or archive records.

First TDD slice: make one of two media downloads fail permanently, verify the
message and failure records, then resume with success and observe one local
file and a reconciled manifest.

## Approval gate

Approve the proposed command/configuration interface and prioritized behavior
list before implementation. After approval, commit this file before starting
ticket 1. Implement and commit tickets strictly in order, returning here for
approval if implementation reveals new scope.
