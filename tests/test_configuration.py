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
            "DISCORD_USER_AGENT": "Firefox/test",
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
        "DISCORD_TOKEN=home-token\n"
        "DISCORD_GUILD_ID=123\n"
        "DISCORD_EXPORT_ROOT=archive\n"
        "DISCORD_USER_AGENT=Firefox/test\n",
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
                "DISCORD_USER_AGENT": "Firefox/test",
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )

    assert "secret-token" not in str(error.value)


def test_request_policy_settings_are_loaded_and_validated(tmp_path: Path) -> None:
    config = load_env(
        environ={
            "DISCORD_TOKEN": "token",
            "DISCORD_GUILD_ID": "123",
            "DISCORD_EXPORT_ROOT": "archive",
            "DISCORD_DELAY_MIN_SECONDS": "1.5",
            "DISCORD_DELAY_MAX_SECONDS": "3.5",
            "DISCORD_USER_AGENT": "Firefox/test",
        },
        cwd=tmp_path,
        home=tmp_path / "home",
    )

    assert config.delay_min_seconds == 1.5
    assert config.delay_max_seconds == 3.5
    assert config.user_agent == "Firefox/test"


def test_missing_user_agent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="DISCORD_USER_AGENT"):
        load_env(
            environ={
                "DISCORD_TOKEN": "token",
                "DISCORD_GUILD_ID": "123",
                "DISCORD_EXPORT_ROOT": "archive",
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )


def test_invalid_delay_range_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="DISCORD_DELAY_MIN_SECONDS"):
        load_env(
            environ={
                "DISCORD_TOKEN": "token",
                "DISCORD_GUILD_ID": "123",
                "DISCORD_EXPORT_ROOT": "archive",
                "DISCORD_DELAY_MIN_SECONDS": "4",
                "DISCORD_DELAY_MAX_SECONDS": "3",
                "DISCORD_USER_AGENT": "Firefox/test",
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
