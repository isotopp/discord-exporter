# Development guide

## For human developers

This is a Python 3.14+ command-line exporter for one Discord server. The entry point is `discord-exporter`, which loads `.env` configuration, reads from Discord through `DiscordGuildSource`, and writes an on-disk archive through `export_guild`.

Use uv for local development:

```sh
uv sync
uv run pytest
uv run ruff check
uv run ty check
```

Keep the archive format backward-compatible. `server.json`, `members.jsonl`, `channels.jsonl`, per-channel `channel.json`, per-year `messages.jsonl`, `manifest.json`, and `state.json` are user data. IDs must remain strings in exported JSON, message records must remain ordered by timestamp then ID, and a stopped export must be safe to rerun in the same directory.

Add or update the smallest focused test in `tests/` for behavior changes. Use `sample.env` only as a non-secret template; real tokens belong in an ignored `.env` file.

## Agent-only guardrails

Before editing, inspect the relevant source and tests and check `git status`; do not alter unrelated user changes. Work in a dedicated worktree when a task requests one, and do not delete a worktree or branch until its changes are merged and verified.

Never read, print, commit, or transmit `.env`, Discord tokens, archive contents, or other user data. Do not use a user token or self-bot workflow: this project authenticates only with a Discord App token.

Do not broaden the exporter’s Discord permissions, enable new intents, make network calls, or change the archive schema without an explicit request. Preserve retry, pacing, resumability, and incomplete-export reporting. Run the relevant checks before handing off a change, and report any check that could not run.
