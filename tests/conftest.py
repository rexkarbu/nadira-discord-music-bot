"""Pytest fixtures for Iwed Discord Music Bot test suite."""

from typing import Any

import pytest

from iwed_bot.settings import Settings


@pytest.fixture
def valid_env_dict() -> dict[str, Any]:
    """Mengembalikan dictionary environment variables yang valid untuk pengujian."""
    return {
        "DISCORD_TOKEN": "test_mock_discord_token_secret_123456",
        "DISCORD_APPLICATION_ID": 123456789012345678,
        "DISCORD_TEST_GUILD_ID": 987654321098765432,
        "LAVALINK_URI": "http://localhost:2333",
        "LAVALINK_PASSWORD": "test_lavalink_password_xyz",
        "SOURCE_POLICY_MODE": "prototype",
        "QUEUE_BACKEND": "memory",
        "MAX_PLAYLIST_TRACKS": 250,
        "QUEUE_MAX_TRACKS": 500,
        "IDLE_DISCONNECT_SECONDS": 180,
        "DEFAULT_VOLUME": 80,
        "LOG_LEVEL": "DEBUG",
    }


@pytest.fixture
def valid_settings(valid_env_dict: dict[str, Any]) -> Settings:
    """Fixture Settings terpopulasi dengan data valid tanpa kontaminasi .env disk."""
    return Settings(_env_file=None, **valid_env_dict)


@pytest.fixture
def sample_health_data() -> dict[str, Any]:
    """Sample data status kesehatan bot untuk pengujian build_health_embed."""
    return {
        "bot_status": "Online",
        "uptime_seconds": 3665,
        "uptime_str": "1j 1m 5d",
        "discord_latency_ms": 42.5,
        "lavalink_connected": True,
        "startup_mode": "Normal",
        "app_version": "0.1.0 • DAVE-compatible stack",
    }


@pytest.fixture
def degraded_health_data() -> dict[str, Any]:
    """Sample data status kesehatan ketika Lavalink offline (degraded)."""
    return {
        "bot_status": "Degraded",
        "uptime_seconds": 120,
        "uptime_str": "2m 0d",
        "discord_latency_ms": None,
        "lavalink_connected": False,
        "startup_mode": "Degraded (Audio Offline)",
        "app_version": "0.1.0 • DAVE-compatible stack",
    }
