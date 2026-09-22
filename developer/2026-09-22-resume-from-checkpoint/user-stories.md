# Epic: Resume exports from durable checkpoints

## Objective

Make an interrupted or repeated export continue from durable archive progress
instead of fetching every channel's complete history again. Preserve the
existing guarantees that state never advances beyond durable data and that a
stale checkpoint cannot cause messages to be skipped.

Give the operator compact live feedback during long runs while retaining one
durable terminal line for every channel that finishes processing.

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
  the exporter reports or refetches rather than silently skipping messages.

### Actual behavior

- `state.json` is loaded and its channel map is used for manifest summaries.
- The export loop nevertheless processes every accessible channel.
- Each channel begins with `fetch_messages(channel_id)`, which requests its
  complete history.
- The saved `last_message_id` is never supplied to the message-fetch path.
- Existing JSONL is merged by source message ID and existing media is reused,
  so reruns avoid duplicate output but not duplicate Discord requests.
- Existing yearly JSONL is rewritten rather than extended by appending only
  newer complete records.
- A malformed final JSONL line is currently ignored without asking the
  operator to repair the archive.

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
  then rewrites each yearly JSONL, which explains why output remains
  deduplicated despite the repeated fetch.
- `src/discord_exporter/archive.py:595-607` stops reading when the final JSONL
  line is malformed instead of reporting the damaged file to the operator.
- The original acceptance criterion in
  `developer/2026-09-21-restartable-discord-export/user-stories.md:135-153`
  explicitly requires resume without starting over or skipping messages.

## User stories

### 1. Resume from durable channel progress

As an archive operator, I want a restarted export to continue from the newest
durable per-channel checkpoint, so that interrupting a multi-day export does
not repeat completed history or skip messages.

#### Acceptance criteria

- The public command remains `uv run discord-exporter`; no manual resume flag
  is required when the configured export root contains an existing archive.
- Resume derives a usable channel cursor only after reconciling `state.json`
  with the channel's valid JSONL records.
- A valid saved cursor is passed to the incremental Discord history boundary;
  the full-history boundary is not called for that channel.
- Messages newer than the cursor are exported in chronological order, with the
  existing media, deduplication, and catch-up behavior preserved.
- New messages are appended to their yearly JSONL as complete newline-terminated
  records; existing valid records are not rewritten.
- A completed channel with no newer messages remains complete and does not
  rewrite unchanged yearly JSONL files.
- If state claims progress that the archive cannot prove, the exporter falls
  back to a safe earlier point or a full channel fetch.
- If durable JSONL is ahead of state because execution stopped before atomic
  state replacement, resume uses the archive evidence and does not duplicate
  records.
- A malformed or partial JSONL line is not used as cursor evidence or repaired
  automatically. The exporter identifies the affected file and leaves the
  channel incomplete for operator repair.
- State advances only after new message records and media outcomes are durable.
- One channel's invalid checkpoint or request failure does not prevent other
  channels from resuming.
- Tests observe whether the full-history or incremental Discord boundary was
  called and verify the completed archive through its public files.

### 2. Report live per-channel progress

As an archive operator, I want the active channel and overall channel position
shown while an export runs, so that I can distinguish slow progress from a
stalled process and retain a concise record of completed channels.

The current command provides no live progress. `src/discord_exporter/__init__.py`
prints only after `export_guild()` returns, and the channel loop in
`src/discord_exporter/archive.py` emits no status while work is in progress.

#### Acceptance criteria

- Once the channel catalogue is known, progress identifies the active channel
  by sanitized name and Discord channel ID.
- Progress includes an incrementing channel position such as `7/30` and, when
  the total is known, a channel-position percentage such as `23%`.
- The percentage is explicitly channel-based; it does not claim to represent
  messages, bytes, elapsed time, or equal amounts of work.
- While a channel is active, status updates replace one terminal line using a
  carriage return and line clearing rather than printing new lines.
- Whenever processing of a channel finishes, including completion, skip, or
  recorded failure, its final status replaces the active line and prints a
  newline. The next channel then starts progress on a new active line.
- Before a total channel count is available, phase status may omit the counter
  and percentage.
- Completion, failure, and operator interruption leave the terminal at the
  beginning of a clean new line.
- When the progress stream is not an interactive terminal, output contains no
  terminal-control sequences and uses ordinary newline-delimited status.
- Progress is written to `stderr`, is flushed promptly, and never includes bot
  credentials or message content.
- Channel names cannot inject control sequences or force additional terminal
  lines; long display names may be truncated for the active terminal width.
- The final command message describes the actual result as complete or
  incomplete rather than saying that an already-finished export was merely
  initialized.
- The behavior uses the Python standard library and does not require an
  additional terminal-rendering dependency.

## Scope

- Reconcile global channel state with durable per-channel JSONL.
- Select full-history or incremental fetching independently per channel.
- Resume completed and interrupted channels from proven durable cursors.
- Append new complete records without rewriting existing message history.
- Preserve atomic state replacement and existing failure isolation.
- Report single-line live progress and retain one final line per processed
  channel.
- Keep the archive format and command-line interface compatible.

## Out of scope

- Continuous mirroring after a finite export run completes.
- Detecting edits or deletions to messages at or before a durable cursor.
- Parallel channel export.
- Changing yearly archive sharding or media layout.
- Automatically repairing or discarding malformed JSONL content.
- Interactive controls, a graphical interface, and message- or byte-level
  completion percentages.

## Completion criteria

The epic is complete when tests can interrupt an export after durable channel
progress, restart it, and prove that only messages after the reconciled cursor
are requested while the final archive remains complete and deduplicated. A
run must also expose the active channel on one changing terminal line and
retain one final newline-terminated status line for each processed channel.
