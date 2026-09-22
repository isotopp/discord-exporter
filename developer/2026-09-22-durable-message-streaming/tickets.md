# Tickets: Durable message streaming and observable resume

## Public interface proposed for approval

The command remains:

```console
uv run discord-exporter
```

No resume flag or new configuration is introduced. An archive at
`DISCORD_EXPORT_ROOT` is detected automatically, reconciled with `state.json`,
and resumed.

Startup gains flushed `stderr` status for archive detection and metadata work:

```text
Existing archive detected at export; loading state.json
Fetching server metadata
Fetching members and avatars
Discovering channels and threads
Discovered 30 channels and 188 threads
Refreshing server.json, members.jsonl, channels.jsonl, and manifest.json
```

Per-channel progress distinguishes the selected history boundary and counts
messages made durable during the current run:

```text
Channel 7/218 (3%): general [123] — resuming after 456 at 2023-04-05T06:07:08+00:00; 0 new messages
Channel 7/218 (3%): general [123] — exporting; 42 new messages
Channel 7/218 (3%): general [123] — complete; 42 new messages
```

Interactive progress continues to replace the active line and finalize one
line per channel. Redirected output remains newline-delimited without terminal
controls. Message progress has no percentage because Discord does not provide
a reliable total message count.

## Durability contract

For every source message, the externally observable order is:

```text
fetch one message
→ persist or record every referenced media outcome
→ append one complete newline-terminated JSONL record
→ atomically replace state.json with that durable cursor and complete: false
```

The channel is marked `complete: true` only after Discord returns no newer
message. A permanent media failure counts as a durable media outcome only after
the failure is present in the message record and manifest.

## Test approach

Each ticket is one vertical TDD slice. Tests drive `export_guild()` through a
controlled source and inspect only source calls, progress output, archive files,
media files, manifest data, and `state.json`. Interruption points are injected
at public filesystem or source boundaries rather than by testing private
helpers.

Every completed ticket must pass:

```console
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

## 1. Make every appended message a durable checkpoint

Outcome: message history is consumed and persisted incrementally, so an
interruption within a large channel retains all messages completed before it.

Observable behavior:

- Messages are consumed oldest-first without waiting for a complete
  channel-sized result.
- Each message is normalized and appended to the `messages.jsonl` for its UTC
  creation year as one complete newline-terminated record.
- Immediately after the append, `state.json` is atomically replaced with that
  message ID and timestamp and `complete: false` for the channel.
- Existing JSONL bytes are not rewritten and duplicate source message IDs are
  not appended.
- When the source message stream ends normally, the same cursor is retained and
  the channel becomes `complete: true`.
- An empty channel becomes complete with a null cursor.
- Processing remains single-threaded and existing request pacing is preserved.

First TDD slice: provide a source stream that yields one message and then is
interrupted. Observe that the message is already present as a complete JSONL
line and `state.json` records its ID and timestamp with `complete: false`.

## 2. Persist each message's media outcome before its JSONL record

Outcome: every durable message references media that already exists locally or
contains a durable, explicit download failure.

Observable behavior:

- Attachments and embed images for one message are processed before that
  message is appended.
- If processing is interrupted before a media outcome is durable, neither the
  message line nor its checkpoint is written.
- Successful media paths are relative to the export root and resolve to files
  that already exist when the message becomes visible in JSONL.
- Existing matching media is reused on restart.
- A permanent media failure is recorded in the message and manifest before the
  message is appended and does not prevent later messages from being exported.
- Member-avatar behavior and the existing decision not to download emoji or
  sticker media remain unchanged.

First TDD slice: interrupt an attachment download for the first streamed
message and observe no message line and no advanced cursor; rerun successfully
and observe the media file, then the referencing message, then the checkpoint.

## 3. Resume safely across every interruption window

Outcome: restart chooses the newest provable durable cursor and never loses or
duplicates a message across media, JSONL, and state boundaries.

Observable behavior:

- Interruption or request failure preserves the latest non-null durable cursor
  while marking the channel incomplete.
- Restart requests messages strictly after that cursor.
- If JSONL append succeeded but atomic state replacement did not, valid JSONL
  wins reconciliation and restart resumes after its newest message.
- If state names a message absent from valid JSONL, restart falls back to safe
  full history.
- If media or JSONL append did not complete, the uncommitted message is
  requested again.
- A malformed JSONL line remains an operator-repair condition and is never used
  as cursor evidence.
- Completed channels check only for messages newer than their proven cursor.
- Failure in one channel does not stop unrelated channels.

First TDD slice: interrupt after the first message checkpoint, restart with a
second message available, and observe an incremental request after the first
ID, exactly two final message IDs, and a completed cursor at the second ID.

## 4. Explain resume decisions and count durable messages

Outcome: operators can tell a genuine full-history start from partial resume or
an incremental check of a completed channel.

Observable behavior:

- Startup reports whether the export root is new or contains an existing
  archive and whether `state.json` was found.
- Once discovery is complete, output summarizes complete, partial, and missing
  channel checkpoints.
- Before source history is read, channel progress says one of:
  - `starting full history` when no cursor is provable;
  - `resuming after <id> at <timestamp>` for partial progress; or
  - `checking after <id> at <timestamp>` for a completed channel.
- Reconciliation output identifies when valid JSONL is ahead of state or an
  optimistic state cursor is rejected.
- The active line increments only after a message and its checkpoint are
  durable and reports messages added during the current run.
- Final complete, skipped, and failed lines retain the run's new-message count.
- Output never contains credentials or message content.
- Interactive and redirected output retain their existing control-character
  contracts.

First TDD slice: resume a partial channel with one durable message, export one
new message, and observe in order the detected archive, selected cursor,
`0 new messages`, `1 new message`, and a newline-terminated completed status.

## 5. Report metadata and channel/thread discovery work

Outcome: work before message export is visible and the large channel total is
explained by separate channel and thread counts.

Observable behavior:

- Status is emitted and flushed before each potentially slow phase:
  - loading archive state;
  - fetching server metadata;
  - fetching members;
  - downloading or reusing avatars;
  - discovering channels and active and archived threads; and
  - writing metadata and manifest files.
- Existing `server.json`, `members.jsonl`, `channels.jsonl`, and
  `manifest.json` are described as refreshed; absent files are described as
  created.
- Avatar status reports downloaded, reused, and failed counts without printing
  user secrets.
- Discovery reports ordinary channel and thread counts separately while the
  per-item progress denominator continues to include both.
- Metadata remains a finite replace-on-run snapshot rather than gaining
  message-style incremental checkpoints.
- Interactive phase output leaves a clean line before channel progress;
  redirected output contains no carriage returns or ANSI sequences.

First TDD slice: run against a controlled source with two ordinary channels and
three threads and observe ordered metadata phases followed by
`Discovered 2 channels and 3 threads` before the first channel progress line.

## 6. Detect and remove dead or duplicate export code

Outcome: the completed streaming design has one production path for message
retrieval, media-first persistence, JSONL append, checkpoint advancement, and
progress reporting.

Observable behavior:

- The post-change source tree is inspected for callers of every superseded
  list-based message-fetching and channel-sized write helper.
- Message-list accumulation paths that became unreachable after streaming are
  removed rather than retained as compatibility code.
- Obsolete protocol methods, test doubles, imports, state constructors, and
  media or JSONL helpers are removed when they have no remaining caller.
- Near-duplicate paths for initial history and incremental history are reduced
  to one behavior where doing so preserves the approved source boundary.
- Near-duplicate interactive and redirected progress logic is consolidated
  only where the resulting code remains simpler and preserves exact output.
- No speculative abstraction or new dependency is introduced for dead-code or
  duplicate-code detection.
- Public command behavior, archive files, failure isolation, and resume
  semantics remain unchanged.
- Ruff, `ty`, and the complete test suite pass after each removal.

Verification slice: inventory the old and new message paths with repository
reference searches, remove one proven-unreferenced path at a time, and use the
existing integration suite as characterization coverage after each removal.

## 7. Update operator and developer documentation

Outcome: user-facing and contributor-facing documentation describes the final
streaming, durability, resume, progress, and thread behavior accurately.

Observable behavior:

- `README.md` explains:
  - archive detection and automatic resume without a flag;
  - media-first message persistence and per-message checkpoints;
  - what happens when the operator stops and restarts the command;
  - startup metadata phases and per-channel message progress;
  - why channels and threads both appear in the progress denominator;
  - yearly JSONL and adjacent media layout; and
  - operator action for malformed or partial JSONL.
- Configuration and operation examples remain consistent with the actual CLI
  and existing environment variables; no unimplemented option is documented.
- `AGENTS.md` records the durability ordering as a development invariant:
  media outcome, complete JSONL append, then atomic state replacement.
- `AGENTS.md` records guardrails against channel-sized message accumulation,
  checkpoint advancement before durable data, loss of a partial cursor on
  failure, duplicate message paths, and terminal controls in redirected output.
- Developer instructions identify the required Ruff, formatter, `ty`, and
  pytest commands and the interruption scenarios that must remain covered.
- Documentation uses English and distinguishes metadata snapshots from
  incrementally appended message history.

Verification slice: compare every README operation and progress example with
the final integration-test output and archive layout, then run the repository's
documentation-adjacent checks and search for stale descriptions of
channel-sized fetching or channel-only checkpointing.

## Approval gate

Implementation starts only after these ticket outcomes, durability ordering,
public output, and first TDD slices are approved and this document is
committed.
