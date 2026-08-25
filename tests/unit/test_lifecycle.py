"""Unit tests untuk bot lifecycle, intents, observabilitas, dan supervisor Nadira."""

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from nadira_bot.bot import NadiraBot
from nadira_bot.observability.logging import StructuredJsonFormatter
from nadira_bot.settings import Settings


@pytest.mark.asyncio
async def test_bot_intents_configuration(valid_settings: Settings) -> None:
    """Memverifikasi bahwa intent bot dikonfigurasi dan message_content dinonaktifkan."""
    bot = NadiraBot(valid_settings)
    try:
        # Invariant: message_content HARUS bernilai False
        assert bot.intents.message_content is False
        assert bot.intents.guilds is True
        assert bot.intents.voice_states is True
        assert bot.application_id == valid_settings.DISCORD_APPLICATION_ID
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_bot_health_status_structure(valid_settings: Settings) -> None:
    """Memverifikasi struktur data yang dihasilkan oleh get_health_status()."""
    bot = NadiraBot(valid_settings)
    try:
        status = bot.get_health_status()
        assert isinstance(status["bot_status"], str)
        assert isinstance(status["uptime_seconds"], int)
        assert status["uptime_seconds"] >= 0
        assert isinstance(status["uptime_str"], str)
        assert "lavalink_connected" in status
        assert "startup_mode" in status
        assert status["app_version"] == "0.1.0 • DAVE-compatible stack"
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_command_sync_to_test_guild(valid_settings: Settings) -> None:
    """Memverifikasi sync commands ke test guild saat DISCORD_TEST_GUILD_ID disetel."""
    bot = NadiraBot(valid_settings)
    try:
        with (
            patch.object(bot, "load_extension", new_callable=AsyncMock),
            patch.object(bot.tree, "copy_global_to") as mock_copy_global,
            patch.object(bot.tree, "sync", new_callable=AsyncMock, return_value=[]) as mock_sync,
            patch("wavelink.Pool.connect", new_callable=AsyncMock),
        ):
            await bot.setup_hook()
            mock_copy_global.assert_called_once()
            mock_sync.assert_awaited_once()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_command_sync_global_fallback(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi sinkronisasi global saat DISCORD_TEST_GUILD_ID bernilai None."""
    data = dict(valid_env_dict)
    data["DISCORD_TEST_GUILD_ID"] = None
    settings = Settings(_env_file=None, **data)

    bot = NadiraBot(settings)
    try:
        with (
            patch.object(bot, "load_extension", new_callable=AsyncMock),
            patch.object(bot.tree, "copy_global_to") as mock_copy_global,
            patch.object(bot.tree, "sync", new_callable=AsyncMock, return_value=[]) as mock_sync,
            patch("wavelink.Pool.connect", new_callable=AsyncMock),
        ):
            await bot.setup_hook()
            mock_copy_global.assert_not_called()
            mock_sync.assert_awaited_once_with()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_wavelink_mocked_lifecycle_events(valid_settings: Settings) -> None:
    """Memverifikasi event handler Wavelink ready dan disconnected memperbarui state."""
    bot = NadiraBot(valid_settings)
    try:
        assert bot.lavalink_connected is False
        assert not bot._lavalink_ready_event.is_set()

        # Simulasi NodeReadyEventPayload
        ready_payload = MagicMock(spec=wavelink.NodeReadyEventPayload)
        ready_payload.resumed = False
        await bot.on_wavelink_node_ready(ready_payload)

        assert bot.lavalink_connected is True
        assert bot._lavalink_ready_event.is_set()
        assert bot._current_backoff_seconds == 5.0

        # Simulasi NodeDisconnectedEventPayload
        disconnect_payload = MagicMock(spec=wavelink.NodeDisconnectedEventPayload)
        with patch.object(bot, "_start_reconnect_supervisor") as mock_start_sup:
            await bot.on_wavelink_node_disconnected(disconnect_payload)
            assert bot.lavalink_connected is False
            assert not bot._lavalink_ready_event.is_set()
            mock_start_sup.assert_called_once()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_reconnect_supervisor_no_duplicates_and_clean_close(
    valid_settings: Settings,
) -> None:
    """Memverifikasi bahwa supervisor tidak terduplikasi dan dibatalkan saat close()."""
    bot = NadiraBot(valid_settings)

    try:
        bot._start_reconnect_supervisor()
        first_task = bot._reconnect_task
        assert first_task is not None
        assert not first_task.done()

        # Panggil start supervisor kedua kali; tidak boleh membuat task baru
        bot._start_reconnect_supervisor()
        assert bot._reconnect_task is first_task

        # Test supervisor stops naturally when ready event is set
        bot._lavalink_ready_event.set()
        await asyncio.sleep(0.05)
    finally:
        await bot.close()
        assert bot._reconnect_task is not None
        assert bot._reconnect_task.done()


@pytest.mark.asyncio
async def test_reconnect_backoff_progression_when_node_disconnected(
    valid_settings: Settings,
) -> None:
    """Memverifikasi urutan peningkatan backoff meskipun Pool.reconnect() tidak throw exception."""
    bot = NadiraBot(valid_settings)
    mock_node = MagicMock(spec=wavelink.Node)
    mock_node.status = wavelink.NodeStatus.DISCONNECTED
    bot._lavalink_node = mock_node

    recorded_backoffs: list[float] = []
    iteration_count = 0

    async def mock_sleep(_delay: float) -> None:
        nonlocal iteration_count
        iteration_count += 1
        recorded_backoffs.append(bot._current_backoff_seconds)
        if iteration_count >= 5:
            # Hentikan loop setelah 5 iterasi
            bot._lavalink_ready_event.set()

    try:
        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("wavelink.Pool.reconnect", new_callable=AsyncMock) as mock_reconnect,
        ):
            await bot._reconnect_supervisor_loop()

            assert mock_reconnect.await_count >= 4
            # Urutan backoff harus meningkat: 5.0 -> 10.0 -> 20.0 -> 40.0 -> 60.0
            assert recorded_backoffs[0] == 5.0
            assert recorded_backoffs[1] == 10.0
            assert recorded_backoffs[2] == 20.0
            assert recorded_backoffs[3] == 40.0
            assert recorded_backoffs[4] == 60.0
    finally:
        await bot.close()


def test_structured_json_logging_formatter() -> None:
    """Memverifikasi bahwa StructuredJsonFormatter menghasilkan output JSON yang valid."""
    formatter = StructuredJsonFormatter(environment="test-env")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test event message",
        args=(),
        exc_info=None,
    )
    # Tambahkan custom extra field
    record.__dict__["correlation_id"] = "corr-12345"

    formatted_output = formatter.format(record)
    log_data = json.loads(formatted_output)

    assert log_data["level"] == "INFO"
    assert log_data["logger"] == "test_logger"
    assert log_data["event"] == "Test event message"
    assert log_data["environment"] == "test-env"
    assert log_data["correlation_id"] == "corr-12345"
    assert "timestamp" in log_data
