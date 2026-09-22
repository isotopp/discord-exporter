# Development guide

## For human developers

This is a Python 3.14+ command-line exporter for one Discord server. The entry point is `discord-exporter`, which loads `.env` configuration, reads from Discord through `DiscordGuildSource`, and writes an on-disk archive through `export_guild`.

Use uv for local development:

```sh
uv sync
uv run ruff check --fix
uv run ruff format
uv run ty check
uv run pytest
```

Keep the archive format backward-compatible. `server.json`, `members.jsonl`, `channels.jsonl`, per-channel `channel.json`, per-year `messages.jsonl`, `manifest.json`, and `state.json` are user data. IDs must remain strings in exported JSON, message records must remain ordered by timestamp then ID, and a stopped export must be safe to rerun in the same directory.

Add or update the smallest focused test in `tests/` for behavior changes. Use `sample.env` only as a non-secret template; real tokens belong in an ignored `.env` file.

The exporter is single-threaded. `DiscordGuildSource.iter_messages()` yields history oldest-first; the archive loop must process one message at a time. Do not reintroduce channel-sized message lists or whole-channel rewrites.

The durability invariant is strict: resolve every referenced message attachment/embed media outcome first, append exactly one complete newline-terminated message record, then atomically replace `state.json` with the new channel cursor and `complete: false`. A channel becomes complete only after its stream is exhausted. The global `state.json` contains per-channel cursors; it is not a per-message log and must be replaceable with `Path.replace()`.

Metadata snapshots (`server.json`, `members.jsonl`, `channels.jsonl`, per-channel `channel.json`, and `manifest.json`) may be replaced on each run. Message JSONL is append-only. Preserve string IDs, yearly placement, relative media paths, and the rule that existing JSONL ahead of state wins when it is valid. Invalid or unterminated JSONL is an operator-repair condition; never silently truncate it.

## Agent-only guardrails

Before editing, inspect the relevant source and tests and check `git status`; do not alter unrelated user changes. Work in a dedicated worktree when a task requests one, and do not delete a worktree or branch until its changes are merged and verified.

Never read, print, commit, or transmit `.env`, Discord tokens, archive contents, or other user data. Do not use a user token or self-bot workflow: this project authenticates only with a Discord App token.

Do not broaden the exporter’s Discord permissions, enable new intents, make network calls, or change the archive schema without an explicit request. Preserve retry, pacing, resumability, and incomplete-export reporting. Run the relevant checks before handing off a change, and report any check that could not run.

### Guardrails for agents

- Do not checkpoint before media outcomes and the complete JSONL line are durable.
- Do not reset a non-null cursor to `null` after an error; preserve the latest durable cursor and mark that channel incomplete.
- Do not create a second initial/incremental export path or leave dead batch-fetch and batch-write helpers after a streaming change. Search callers before removing or retaining code.
- Keep progress output limited to phases, channel name/ID, counts, resume decisions, and errors. Never print message content, tokens, or archive records.
- Test the interruption windows: during history, during media, after JSONL append before state replacement, and with a malformed final JSONL line. Verify restart, duplicate avoidance, isolated channel failure, and atomic state replacement.
