from pathlib import Path

import pytest

from discord_exporter.config import ConfigurationError, load_env


def test_process_environment_overrides_local_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DISCORD_TOKEN=file-token\n"
        "DISCORD_GUILD_ID=123\n"
        "DISCORD_EXPORT_ROOT=file-archive\n",
        encoding="utf-8",
    )

    config = load_env(
        environ={
            "DISCORD_TOKEN": "process-token",
            "DISCORD_GUILD_ID": "456",
            "DISCORD_EXPORT_ROOT": "process-archive",
        },
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert config.token == "process-token"
    assert config.guild_id == 456
    assert config.export_root == tmp_path / "process-archive"


def test_home_env_file_is_used_when_local_env_file_is_absent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".discord-export.env").write_text(
        "DISCORD_TOKEN=home-token\nDISCORD_GUILD_ID=123\nDISCORD_EXPORT_ROOT=archive\n",
        encoding="utf-8",
    )

    config = load_env(environ={}, cwd=tmp_path, home=home)

    assert config.token == "home-token"
    assert config.guild_id == 123
    assert config.export_root == tmp_path / "archive"


def test_invalid_guild_is_reported_without_echoing_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="DISCORD_GUILD_ID") as error:
        load_env(
            environ={
                "DISCORD_TOKEN": "secret-token",
                "DISCORD_GUILD_ID": "not-a-guild",
                "DISCORD_EXPORT_ROOT": "archive",
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )

    assert "secret-token" not in str(error.value)
