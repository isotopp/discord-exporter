# Tickets: Resume exports from durable checkpoints

## Public interface proposed for approval

The command remains:

```console
uv run discord-exporter
```

An existing archive at `DISCORD_EXPORT_ROOT` is resumed automatically. No
resume flag or new configuration is introduced. The archive format remains
compatible with the existing `state.json`, yearly `messages.jsonl`, manifest,
and media paths.

During a run, `stderr` gains an operator-facing progress contract. For an
interactive terminal, the active channel occupies one changing line:

```text
Channel 7/30 (23%): general [123456789] — exporting
```

The line is finalized with a newline whenever that channel completes, is
skipped, or fails. Non-interactive output uses ordinary newline-delimited
status without terminal-control characters. The percentage represents channel
position only.

## Test approach

Each ticket is one vertical TDD slice. Tests observe the Discord boundary,
archive files, state, and command output rather than internal helper calls.
Discord responses, filesystem interruption points, and terminal streams may be
controlled at those external boundaries.

Every completed ticket must pass:

```console
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

## 1. Resume and append after a proven durable cursor

Outcome: a repeated export checks only for messages newer than a cursor that
is supported by both state and durable archive data.

Observable behavior:

- When `state.json` names a channel cursor and that message exists in valid
  channel JSONL, the exporter calls the incremental history boundary with that
  message ID.
- The full-history boundary is not called for a channel with a proven cursor.
- A completed empty channel uses the incremental boundary from the beginning
  rather than an unnecessary full-history pass.
- Newly returned messages are appended to their yearly JSONL as complete
  newline-terminated records; bytes belonging to existing valid records are
  not rewritten.
- Appended messages retain chronological yearly placement, media handling,
  catch-up, deduplication, and atomic state advancement.
- A completed channel with no newer messages remains complete and its
  unchanged yearly JSONL remains byte-for-byte unchanged.

First TDD slice: export a channel once, rerun it with one newer source message,
and observe that the source receives only `fetch_messages_after()` with the
saved message ID while the original JSONL bytes remain an unchanged prefix of
the final file and each message appears exactly once.

## 2. Reject malformed JSONL and reconcile checkpoint disagreement

Outcome: disagreement between state and durable archive data chooses the most
advanced cursor that can be proven without allowing optimistic state to skip
messages.

Observable behavior:

- If durable JSONL is ahead of state because data persisted before state
  replacement, the exporter resumes after the newest valid archived message.
- If state names a message that is absent from the archive, the exporter falls
  back to a full-history fetch.
- A malformed or partial JSONL line is never accepted as cursor evidence.
- The exporter reports the affected path, leaves the file untouched, and marks
  the channel incomplete so the operator can repair it explicitly.
- Reconciliation never duplicates source message IDs in completed JSONL.
- An invalid checkpoint or request failure for one channel does not stop other
  channels from resuming.
- Reconciled state is written only after new records and media outcomes are
  durable.

First TDD slice: start with a channel JSONL containing a partial final line and
another valid channel, then observe an actionable path-specific error, the
unchanged malformed file, incomplete state for that channel, and successful
processing of the unrelated channel.

## 3. Report live per-channel terminal progress

Outcome: the operator can see which channel is active and retain one concise
terminal line for every channel that finishes.

Observable behavior:

- Interactive progress identifies the sanitized channel name and ID, current
  channel position, total channels, channel-position percentage, and status.
- Updates for the active channel use carriage return and line clearing without
  adding lines.
- Completion, skip, or recorded failure replaces the active line and ends it
  with a newline before the next channel starts.
- Completion percentage is channel-based and reaches `100%` on the final
  processed channel; no message- or byte-level percentage is shown.
- Before channel discovery supplies a total, phase output omits percentage.
- Interrupt, command failure, and normal completion leave the terminal on a
  clean new line.
- Non-interactive output contains no carriage returns or terminal-control
  sequences and uses ordinary newline-delimited status.
- Channel names cannot inject terminal controls or extra lines and may be
  truncated to the available terminal width.
- Progress is flushed to `stderr` and never includes credentials or message
  content.
- Successful command output says that the export completed; it no longer says
  that the completed export was merely initialized.
- The behavior uses the Python standard library and adds no rendering
  dependency.

First TDD slice: run an export with two channels and a controlled interactive
progress stream, then observe in order one replaceable active line and one
newline-terminated final line per channel with counters `1/2` and `2/2`.

## Approval gate

Implementation starts only after these ticket outcomes, public output, and TDD
slices are approved and this document is committed.
