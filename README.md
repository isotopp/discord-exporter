# Discord Exporter

Discord Exporter makes a local, restartable archive of one Discord server that an installed Discord App can access. It saves server metadata, members, channels, messages, avatars, attachments, and embedded images to ordinary JSON/JSONL files and media files; it never modifies the server.

## Installation and first export

These steps are for the Discord server operator. You need permission to manage the server and access to the Discord Developer Portal. Export only servers and data you are authorized to retain.

1. Install [uv](https://docs.astral.sh/uv/) and Python 3.14 or later.

2. Download or clone this project, then change into its directory.

   ```sh
   git clone <repository-url> discord-exporter
   cd discord-exporter
   ```

3. Create the Discord App.

   1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select **New Application**.
   2. Give the App a recognizable name, accept the terms, and create it.
   3. In the App's installation settings, enable **Guild Install**. Add the `bot` scope and configure the App to request only these server permissions:
      - View Channels
      - Read Message History
   4. Save the installation settings, use the generated install link, and add the App to the server you are exporting. Select that server and approve the requested permissions.

4. Allow the App to read the data this exporter needs. In the App settings, enable the privileged gateway options for **Server Members Intent** and **Message Content Intent**, then save. An App installed in many servers may need Discord approval before those options can be used; see Discord's [privileged-intent guidance](https://support-dev.discord.com/hc/en-us/articles/6205754771351-How-do-I-get-Privileged-Intents-for-my-bot).

5. Copy the App token. On the App settings page, reset or copy its token, store it only in the local configuration file in the next step, and never share or commit it. Reset the token immediately if it is exposed.

6. Copy the example configuration and edit it.

   ```sh
   cp sample.env .env
   ```

   Set these values in `.env`:

   - `DISCORD_TOKEN`: the App token from step 5.
   - `DISCORD_GUILD_ID`: the numeric ID of the server to export. In Discord, enable **Developer Mode** in your advanced settings, then right-click the server icon and choose **Copy Server ID**.
   - `DISCORD_EXPORT_ROOT`: where the archive will be stored, such as `./export` or an absolute path on a disk with enough free space.
   - `DISCORD_USER_AGENT`: an identifying HTTP user-agent for media downloads. The sample value is usable as-is.
   - `DISCORD_DELAY_MIN_SECONDS` and `DISCORD_DELAY_MAX_SECONDS`: optional pacing limits for Discord and CDN requests. Leave the defaults unless you have a reason to change them.

7. Install the project and run the export.

   ```sh
   uv sync
   uv run discord-exporter
   ```

   The program exits with status `0` when it completes. It reports configuration errors before contacting Discord. Re-run the same command with the same export directory to continue a stopped or incomplete export; completed message files and downloaded media are retained.

8. Check `<DISCORD_EXPORT_ROOT>/manifest.json`. `"status": "complete"` means every accessible channel finished. `"status": "incomplete"` means one or more channels could not be read; inspect `incomplete_channels` and `failures`, correct the App's access if needed, and run the command again.

## Export format and directory layout

All IDs are strings so they retain Discord's full numeric precision. JSON files are pretty-printed objects; `.jsonl` files contain one JSON object per line. Times are ISO 8601 timestamps in UTC when supplied by Discord.

```
<DISCORD_EXPORT_ROOT>/
├── server.json                         # Server metadata, roles, and custom emoji metadata
├── members.jsonl                       # One server-member record per line
├── channels.jsonl                      # One channel or thread record per line
├── manifest.json                       # Current/final status, channel completion, and failures
├── state.json                          # Resume state; keep this with the archive
├── media/
│   └── avatars/
│       └── <member-id>--<avatar-id>.<ext>
└── channels/
    └── <safe-channel-name>--<channel-id>/
        ├── channel.json                # Metadata plus this directory's archive_path
        └── <year>/
            ├── messages.jsonl          # Messages in timestamp order for that calendar year
            └── media/
                ├── <message-id>--<attachment-id>--<filename>
                └── <message-id>--embed-<hash>.<ext>
```

`members.jsonl` includes the member's `avatar_path` when its avatar download succeeded. Message attachments and embed images include `local_path` when downloaded; failures stay in the metadata as `download_error` and are also summarized in `manifest.json`. Stickers and other Discord objects are preserved as metadata unless the layout shows a downloaded file. Channel directory names are safe display names paired with the immutable channel ID, so links remain stable if a channel is renamed.
