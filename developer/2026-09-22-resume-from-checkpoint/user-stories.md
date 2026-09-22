# Epic: Resume exports from durable checkpoints

## Objective

Make an interrupted or repeated export continue from durable archive progress
instead of fetching every channel's complete history again. Preserve the
existing guarantees that state never advances beyond durable data and that a
stale checkpoint cannot cause messages to be skipped.

## Bug description

The exporter is restart-safe but not genuinely resumable. It preserves
already-written archive data and avoids duplicate records, but every run still
requests complete history for every accessible channel.

This violates the existing operator story that a long export can resume
without starting over. With approximately 30 channels and six years of
history, stopping near the end can repeat almost the entire Discord request
load and the configured pacing delay.

### Expected behavior

- A rerun reconciles `state.json` with durable archive files.
- A channel with a valid durable cursor requests only messages newer than that
  cursor.
- A completed channel with no newer messages completes without requesting its
  historical message range again.
- An interrupted channel resumes from the newest message proven durable by the
  archive, when such a cursor exists.
- Missing or inconsistent archive data takes precedence over optimistic state;
  the exporter repairs or refetches rather than silently skipping messages.

### Actual behavior

- `state.json` is loaded and its channel map is used for manifest summaries.
- The export loop nevertheless processes every accessible channel.
- Each channel begins with `fetch_messages(channel_id)`, which requests its
  complete history.
- The saved `last_message_id` is never supplied to the message-fetch path.
- Existing JSONL is merged by source message ID and existing media is reused,
  so reruns avoid duplicate output but not duplicate Discord requests.

## Evidence

- `src/discord_exporter/archive.py:49-53` loads `state.json` and obtains
  `channel_states`.
- `src/discord_exporter/archive.py:92-122` iterates every channel and calls
  `_fetch_channel_messages()` without inspecting that channel's saved state.
- `src/discord_exporter/archive.py:510-536` always starts with
  `source.fetch_messages(channel_id)` and derives its catch-up cursor from the
  newly fetched full history.
- `src/discord_exporter/archive.py:633-653` records `last_message_id`,
  `last_message_timestamp`, and `complete`, but those values are not consumed
  by the fetch path.
- `src/discord_exporter/archive.py:572-592` merges existing and current records,
  which explains why output remains deduplicated despite the repeated fetch.
- The original acceptance criterion in
  `developer/2026-09-21-restartable-discord-export/user-stories.md:135-153`
  explicitly requires resume without starting over or skipping messages.

## User story

As an archive operator, I want a restarted export to continue from the newest
durable per-channel checkpoint, so that interrupting a multi-day export does
not repeat completed history or skip messages.

### Acceptance criteria

- The public command remains `uv run discord-exporter`; no manual resume flag
  is required when the configured export root contains an existing archive.
- Resume derives a usable channel cursor only after reconciling `state.json`
  with the channel's valid JSONL records.
- A valid saved cursor is passed to the incremental Discord history boundary;
  the full-history boundary is not called for that channel.
- Messages newer than the cursor are exported in chronological order, with the
  existing media, deduplication, and catch-up behavior preserved.
- A completed channel with no newer messages remains complete and does not
  rewrite unchanged yearly JSONL files.
- If state claims progress that the archive cannot prove, the exporter falls
  back to a safe earlier point or a full channel fetch.
- If durable JSONL is ahead of state because execution stopped before atomic
  state replacement, resume uses the archive evidence and does not duplicate
  records.
- State advances only after new message records and media outcomes are durable.
- One channel's invalid checkpoint or request failure does not prevent other
  channels from resuming.
- Tests observe whether the full-history or incremental Discord boundary was
  called and verify the completed archive through its public files.

## Scope

- Reconcile global channel state with durable per-channel JSONL.
- Select full-history or incremental fetching independently per channel.
- Resume completed and interrupted channels from proven durable cursors.
- Preserve atomic state replacement and existing failure isolation.
- Keep the archive format and command-line interface compatible.

## Out of scope

- Continuous mirroring after a finite export run completes.
- Detecting edits or deletions to messages at or before a durable cursor.
- Parallel channel export.
- Changing yearly archive sharding or media layout.
- Adding interactive controls or a graphical progress display.

## Completion criteria

The epic is complete when a test can interrupt an export after durable channel
progress, restart it, and prove that only messages after the reconciled cursor
are requested while the final archive remains complete and deduplicated.
