# Epic: Restartable Discord server export

## Objective

Export all content visible to a Discord bot into a stable, portable archive for
handoff to the `chatto.run` import project. The export must be deliberately
paced, safe to restart, and preserve enough source identity and relationship
data for a privileged importer to recreate historical messages with mapped
identities and timestamps.

The expected source has approximately 12 members, 30 channels, and six years
of history.

## Scope

- Use `discord.py` through a bot account.
- Export server, member, channel, thread, message, and attachment data visible
  to the bot.
- Store one channel directory with one subdirectory per active year.
- Store yearly messages as JSON Lines and downloaded message media beside the
  JSONL in a `media` directory.
- Store downloaded member avatars in a shared archive media directory.
- Preserve Discord IDs as strings and use them as canonical references.
- Keep exported records as close to native Discord JSON as practical.
- Produce the source catalogues and mapping inputs required by the separate
  `chatto.run` importer.
- Resume interrupted exports without losing or silently skipping content.

## Out of scope

- Importing data into `chatto.run`.
- Implementing or invoking the Owner-Operator-Interface.
- Recreating content in another Discord server.
- Recovering deleted content or content the bot cannot access.
- Continuously mirroring new Discord activity after an export is complete.

## Archive shape

```text
export/
├── server.json
├── members.jsonl
├── channels.jsonl
├── manifest.json
├── state.json
├── media/
│   └── avatars/
└── channels/
    └── channel-name--channel-id/
        ├── channel.json
        └── YYYY/
            ├── messages.jsonl
            └── media/
```

Empty years are omitted. Directory names include immutable Discord channel IDs
so channel renames do not change identity.

## User stories

### 1. Configure and connect the exporter

As an archive operator, I want to configure the Discord bot and export
location without putting credentials in the archive, so that I can run the
export safely in different environments.

This should likely use a `.env` file in the current working directory,  and if one is not found, `~/
.discord-export.env`.
The file should be loaded using `python-doten`, and the `load_env()` function.
The gives `os.environ` precendence over file content, which is intended.

Acceptance criteria:

- The operator can supply the bot token and select one source server.
- Secrets are not written to exported JSON, logs, checkpoints, or filenames.
- Startup verifies that the bot can access the selected server and reports a
  clear error when it cannot.
- The output root is explicit and an existing partial export can be reused.

### 2. Export server and member metadata

As a migration operator, I want a complete source member catalogue, so that
the downstream project can map Discord identities to `chatto.run` identities.

Acceptance criteria:

- `server.json` identifies the source server and records available server
  metadata and export provenance.
- `members.jsonl` contains one record per member visible to the bot.
- Each member record uses the Discord user ID as its canonical key and records
  available username, display-name, avatar, role, and membership metadata.
- Downloaded avatars are referenced through paths relative to the export root.
- User and role references elsewhere in the archive use Discord IDs rather
  than mutable names.
- Discord IDs are serialized as JSON strings.

### 3. Discover and describe message-bearing channels

As a migration operator, I want every accessible message-bearing location and
its metadata represented, so that the downstream project can map source
structure before importing messages.

Acceptance criteria:

- The exporter discovers accessible text channels, threads, forum posts, and
  other Discord locations that expose message history to the bot.
- `channels.jsonl` contains one summary record per discovered location.
- Each channel directory contains `channel.json` with available name, type,
  topic, parent, ordering, archival, and permission-related metadata.
- Parent, owner, and related-channel references use Discord IDs.
- Inaccessible locations are reported without aborting unrelated channels.

### 4. Export complete message history

As an archive operator, I want channel history stored as chronological JSONL,
so that large histories can be streamed and imported without loading an
entire archive into memory.

Acceptance criteria:

- Each message visible to the bot is written once to the year selected from
  its original UTC creation timestamp.
- Messages within each JSONL file are ordered chronologically.
- Each record preserves available source message ID, channel ID, author ID,
  creation and edit timestamps, text, mentions, reply references, embeds,
  reactions, attachment metadata, and other migration-relevant fields.
- Records retain native Discord field names and structure where practical.
- Message, channel, user, role, emoji, and referenced-message IDs are JSON
  strings.
- Unknown or newly introduced Discord fields do not cause already supported
  message data to be discarded.
- The exporter continues until it catches up with messages created while the
  export is running rather than stopping at a fixed start-time cutoff.

### 5. Resume safely after interruption

As an archive operator, I want progress for all channels recorded in one state
file, so that a long single-threaded export can resume without starting over
or skipping messages.

Acceptance criteria:

- A single `state.json` records the last durably exported message ID and
  completion state for each channel, keyed by channel ID.
- A channel's state is advanced only after its message records and media have
  been persisted successfully or a media failure has been recorded.
- The state is written to a temporary file in the export root and atomically
  installed with `pathlib.Path.replace()` or equivalent behavior.
- Restarting tolerates a partial final JSONL line and records repeated by a
  crash between data persistence and state replacement.
- Resume deduplicates by source message ID and never treats the state file as
  proof of data that is absent from the archive.
- Completed channels retain an auditable `complete` state.

### 6. Pace requests and recover from transient failures

As a Discord server operator, I want export traffic deliberately limited, so
that archival work does not create avoidable load or compete aggressively
with normal server use.

Acceptance criteria:

- The operator can configure a delay or comparable pacing control.
- Discord rate-limit responses are honored.
- Transient API and download failures are retried with bounded backoff.
- Permanent failures are recorded with enough source context to retry or
  investigate later.
- Progress and errors are observable without logging message contents or bot
  credentials unnecessarily.
- The export is handled at a configurable, moderated pace, with a randomly jittered delay between each HTTPS request.
- Discord API requests retain discord.py's native bot identity.
- Media downloads use the configured Firefox-like user agent and language
  headers, and request only gzip or deflate response compression.

### 7. Produce a verifiable migration handoff

As the `chatto.run` migration operator, I want a self-describing archive and
source mappings, so that the custom importer can map identities and structure
without querying Discord again.

Acceptance criteria:

- `manifest.json` identifies the archive format version, source server,
  export time, included channels, and completion state.
- The exporter defines the JSON schemas and mapping-file names, keeping them
  as close to native Discord JSON as practical.
- The archive provides source user, role, channel, thread, and custom-emoji
  identifiers needed to construct target mappings.
- Replies, mentions, authorship, and structural relationships can be resolved
  using only archive records and supplied target mappings.
- All local paths are relative to the export root and remain valid when the
  archive is moved.
- The archive distinguishes unfinished channels and inaccessible content from
  media failures that were recorded while the export continued.
- The handoff does not depend on the Discord bot token or on importer-specific
  Owner-Operator-Interface behavior.

### 8. Download and reference media

As a migration operator, I want relevant media stored with stable local
references, so that the handoff does not depend on source-hosted URLs
remaining available.

Acceptance criteria:

- Accessible member avatars are downloaded into `media/avatars`.
- Accessible attachments, files, and images referenced by messages or embeds
  are downloaded into the `media` directory beside the yearly
  `messages.jsonl` that references them.
- Emoji and sticker media are not downloaded.
- Media filenames include stable source IDs and cannot collide when original
  filenames repeat.
- Member and message records retain original media metadata and relative local
  paths.
- Existing matching downloads are reused when an export resumes.
- Failed or unavailable downloads are recorded explicitly in the manifest and
  corresponding records, and do not prevent the remaining export from
  completing.

## Completion criteria

The epic is complete when an interrupted export can resume, catch up with
messages created during the run, and produce a portable archive containing all
Discord records visible to the bot. The archive records unavailable media and
contains sufficient source identifiers for the separate `chatto.run` project
to map and import the history.

## Comments

- Yearly message files are the current default. With approximately 12 users,
  30 channels, and six years of history, monthly sharding is deferred until
  measured file sizes show a need.
- The archive preserves source truth and source identifiers. Target identity
  mapping and privileged import behavior belong to the separate `chatto.run`
  project.
- Export completeness is bounded by the history and metadata visible to the
  configured Discord bot.
