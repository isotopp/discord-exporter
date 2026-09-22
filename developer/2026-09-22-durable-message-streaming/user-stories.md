# Epic: Durable message streaming and observable resume

## Objective

Make each exported message independently durable so that stopping an export
loses at most the message currently being processed. Before a message is
appended, persist every referenced media outcome; immediately after appending
the complete JSONL line, atomically record that message as the channel's latest
durable checkpoint.

Make archive detection, resume decisions, metadata work, and incremental
message progress visible to the operator so that a resumed run cannot be
mistaken for a full restart or a stalled command.

## Bug description

Resume currently works only at the boundary of previously durable channel
data. The exporter obtains an entire channel's message history in memory,
downloads media for the complete result, appends the messages, and then writes
the channel checkpoint. If the process stops while fetching messages or
downloading their media, none of the newly fetched messages has been appended
and the channel restarts from its previous cursor, which may be the beginning.

The terminal output also always starts its channel-position counter at
`1/N`. It does not say whether that channel is new, partial, or already complete
and merely being checked for newer messages. Metadata discovery before the
channel loop remains silent, so operators cannot distinguish startup work from
a stalled process.

### Expected behavior

- Messages are consumed oldest-first without first accumulating a complete
  channel history.
- For each message, referenced media is downloaded or its permanent failure is
  recorded before the message's complete JSONL line is appended.
- `state.json` is atomically advanced immediately after each appended message,
  with the channel remaining incomplete until Discord has no newer messages.
- A restart reports the existing archive, the durable cursor selected for each
  partial channel, and the point from which it continues.
- Startup reports server, member, avatar, channel, and thread metadata work
  before per-channel message processing begins.

### Actual behavior

- Discord message methods build and return complete lists for a channel.
- `_fetch_channel_messages()` combines initial history and catch-up responses
  into another complete in-memory list.
- Message media for the complete channel result is processed before any of its
  new message records are appended.
- `state.json` is normally replaced only after the channel finishes.
- A channel failure replaces its state with an incomplete entry whose cursor is
  empty, even if earlier messages from that channel could have been durable.
- Progress begins only after server metadata, members, avatars, channels, and
  archived threads have already been fetched.

## Evidence

- `src/discord_exporter/discord_source.py:74-106` consumes Discord history into
  complete Python lists before returning it.
- `src/discord_exporter/archive.py:605-634` accumulates all initial and catch-up
  messages before returning to the channel export loop.
- `src/discord_exporter/archive.py:161-182` fetches the complete message set,
  then processes all media, then writes message files, and only then constructs
  completed channel state.
- `src/discord_exporter/archive.py:184-190` replaces failed channel state with
  an empty incomplete checkpoint.
- `src/discord_exporter/archive.py:198-199` writes state after channel
  processing rather than after each durable message.
- `src/discord_exporter/archive.py:85-143` performs metadata acquisition and
  writing before creating any visible progress output.

## User stories

### 1. Persist each message in media-first durable order

As an archive operator, I want each message persisted as soon as its referenced
media outcome is durable, so that stopping a large channel does not discard all
work completed within that channel.

#### Acceptance criteria

- Discord messages are consumed oldest-first as a stream rather than returned
  to the archive layer as a complete channel-sized list.
- Each message is normalized and routed to the yearly `messages.jsonl` selected
  by its creation timestamp.
- Before appending a message, the exporter downloads its attachments and embed
  images to their stable local paths.
- A permanent media failure is recorded in the message and manifest before the
  message is appended; this recorded outcome does not block subsequent
  messages, preserving the existing media-failure contract.
- The exporter appends exactly one complete newline-terminated JSON object for
  the message after all of that message's media outcomes are durable.
- Existing message lines are not rewritten and existing matching media files
  are reused.
- The exporter never advances a checkpoint for a message whose JSONL line was
  not completely appended.
- Message and media processing remains single-threaded and request pacing is
  preserved.

### 2. Resume an interrupted channel from each durable message

As an archive operator, I want every appended message to become a restart
point, so that interruption loses at most the message currently being
processed.

#### Acceptance criteria

- Immediately after a message line is appended, `state.json` is atomically
  replaced with that message ID and timestamp as the channel's latest durable
  cursor.
- A channel remains `complete: false` while its message stream is active and is
  marked complete only after Discord returns no newer message.
- Interruption or request failure preserves the latest durable message ID and
  timestamp instead of resetting them to `null`.
- On restart, the exporter reconciles state with valid JSONL and resumes after
  the newest cursor proven by the archive.
- If JSONL append succeeds but state replacement does not, restart uses the
  newer valid JSONL record without duplicating it.
- If media persistence or JSONL append does not complete, restart requests that
  message again.
- Completed channels are checked only for messages newer than their durable
  cursor.
- Tests interrupt processing before media persistence, after media persistence,
  after JSONL append, and after state replacement and verify no missing or
  duplicate message IDs.

### 3. Report archive detection and resume decisions

As an archive operator, I want the exporter to explain what existing progress
it found and where each channel continues, so that a resumed run does not look
like a restart from zero.

#### Acceptance criteria

- Startup reports whether the configured export root is new or contains an
  existing archive.
- Existing archive output reports the number of complete, partial, and missing
  channel checkpoints once channel discovery makes those counts meaningful.
- Before reading a channel, progress distinguishes:
  - starting full history with no proven cursor;
  - resuming a partial channel after a specific message ID and timestamp; and
  - checking a completed channel for messages newer than its cursor.
- If state and JSONL disagree, progress reports which durable source won and
  the cursor selected, without printing message content.
- The active channel line includes an incrementing count of messages made
  durable during the current run.
- Final channel output reports complete, skipped, or failed status and the
  number of new messages appended during the run.
- Channel-position percentage remains channel-based. No message percentage is
  shown because Discord does not provide a reliable total history count.
- Interactive output continues to replace one active terminal line; redirected
  output remains newline-delimited and contains no terminal-control sequences.

### 4. Report metadata and discovery phases

As an archive operator, I want visible status while metadata is loaded,
downloaded, reused, and written, so that the command is never silently busy
before channel processing begins.

#### Acceptance criteria

- Before channel progress, the command reports these phases in order:
  - detecting the export archive and loading `state.json`;
  - fetching server metadata;
  - fetching members;
  - downloading or reusing member avatars;
  - discovering channels, active threads, and archived threads; and
  - writing `server.json`, `members.jsonl`, `channels.jsonl`, and the manifest.
- Existing metadata files are identified as existing and being refreshed;
  existing reusable avatar and media files are identified as reused rather
  than downloaded again.
- Discovery reports separate channel and thread counts, for example
  `30 channels and 188 threads`, instead of presenting every thread as an
  unexplained channel.
- Phase output is flushed to `stderr`, includes no credentials or message
  content, and behaves correctly for interactive and redirected output.
- Metadata files remain finite replace-on-run snapshots; message-level
  checkpoint machinery is not added to these comparatively small files.

## Scope

- Stream Discord messages to the archive layer oldest-first.
- Persist message media outcomes before appending the referencing message.
- Append and checkpoint one message at a time.
- Preserve partial channel cursors on interruption and failure.
- Explain archive detection, reconciliation, resume decisions, and message
  progress.
- Report metadata and channel/thread discovery phases.
- Preserve the existing yearly layout, global atomic state file, request
  pacing, failure isolation, and portable relative media paths.

## Out of scope

- Parallel message, channel, or media downloads.
- Continuous mirroring after a finite export completes.
- A message-level completion percentage or estimated remaining time.
- Changing yearly archive sharding or the flat channel/thread directory layout.
- Incremental mutation of server, member, or channel metadata JSONL.
- Automatically repairing malformed JSONL.

## Completion criteria

The epic is complete when a test can interrupt a channel after any durable
message, restart the same command, and prove that processing resumes after that
message without missing or duplicating records. Every appended message must
reference media that is already stored or a media failure that is already
recorded. Operators must see startup metadata phases, archive detection, the
selected resume cursor, incrementing durable-message progress, and distinct
channel and thread counts.

## Comments

- The durable unit is one message, not a Discord page or a complete channel.
- Metadata snapshots are intentionally refreshed as whole files because the
  expected server has approximately 12 users and their size is not the source
  of the restart problem.
- Discord supplies no reliable total message count for a channel, so progress
  reports durable messages added during this run while retaining the existing
  channel-position percentage.
