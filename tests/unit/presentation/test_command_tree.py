"""Unit tests untuk NadiraCommandTree dan presentasi penanganan error."""

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from nadira_bot.application.errors import (
    BotMissingVoicePermission,
    UserNotInVoice,
    VoiceConnectionFailed,
)
from nadira_bot.presentation.command_tree import NadiraCommandTree
from nadira_bot.presentation.interactions import unwrap_command_error


class DummyClient(discord.Client):
    pass


class TestNadiraCommandTree:
    @pytest.mark.asyncio
    async def test_interaction_check_injects_correlation_id(self) -> None:
        client = DummyClient(intents=discord.Intents.default())
        tree = NadiraCommandTree(client)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.extras = {}

        result = await tree.interaction_check(interaction)

        assert result is True
        assert "correlation_id" in interaction.extras
        assert isinstance(interaction.extras["correlation_id"], uuid.UUID)

    @pytest.mark.asyncio
    async def test_on_error_handles_typed_application_errors(self) -> None:
        client = DummyClient(intents=discord.Intents.default())
        tree = NadiraCommandTree(client)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.extras = {"correlation_id": uuid.uuid4()}
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()

        # 1. UserNotInVoice
        err1 = app_commands.CommandInvokeError(
            MagicMock(), UserNotInVoice("Masuk ke voice channel terlebih dahulu.")
        )
        await tree.on_error(interaction, err1)
        interaction.response.send_message.assert_called_with(
            content="[ERROR] Masuk ke voice channel terlebih dahulu.",
            ephemeral=True,
        )

        # 2. BotMissingVoicePermission
        err2 = app_commands.CommandInvokeError(
            MagicMock(), BotMissingVoicePermission(("View Channel", "Connect"))
        )
        await tree.on_error(interaction, err2)
        assert "View Channel, Connect" in interaction.response.send_message.call_args[1]["content"]

    @pytest.mark.asyncio
    async def test_on_error_handles_unknown_exception_with_correlation_id(self) -> None:
        client = DummyClient(intents=discord.Intents.default())
        tree = NadiraCommandTree(client)

        corr_id = uuid.uuid4()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.extras = {"correlation_id": corr_id}
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.edit_original_response = AsyncMock()

        # Unknown internal runtime error
        err = app_commands.CommandInvokeError(
            MagicMock(), RuntimeError("Sensitive DB Connection String: secret_pass_123")
        )
        await tree.on_error(interaction, err)

        # Buktikan pesan aman dan berisi correlation ID, serta tidak membocorkan secret_pass_123
        call_content = interaction.edit_original_response.call_args[1]["content"]
        assert f"ID laporan: `{corr_id}`" in call_content
        assert "secret_pass_123" not in call_content
        assert "RuntimeError" not in call_content

    @pytest.mark.asyncio
    async def test_error_sanitization_does_not_leak_secrets(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = DummyClient(intents=discord.Intents.default())
        tree = NadiraCommandTree(client)

        corr_id = uuid.uuid4()
        interaction = MagicMock(spec=discord.Interaction)
        interaction.extras = {"correlation_id": corr_id}
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.edit_original_response = AsyncMock()

        # Typed application error with secret in underlying exception
        secret_exc = RuntimeError("Lavalink connection failed with password super_secret_password")
        typed_err = VoiceConnectionFailed()
        typed_err.__cause__ = secret_exc

        err = app_commands.CommandInvokeError(MagicMock(), typed_err)

        with caplog.at_level(logging.WARNING):
            await tree.on_error(interaction, err)

        call_content = interaction.edit_original_response.call_args[1]["content"]
        assert "super_secret_password" not in call_content
        for record in caplog.records:
            assert "super_secret_password" not in record.getMessage()

    def test_unwrap_command_error(self) -> None:
        root_err = ValueError("Root error")
        wrapped_1 = app_commands.CommandInvokeError(MagicMock(), root_err)
        wrapped_2 = app_commands.CommandInvokeError(MagicMock(), wrapped_1)

        unwrapped = unwrap_command_error(wrapped_2)
        assert unwrapped is root_err

    def test_no_mojibake_or_unicode_corruption_in_source(self) -> None:
        """Memverifikasi seluruh file di src/nadira_bot bebas dari pola mojibake/korup."""
        src_dir = Path(__file__).resolve().parent.parent.parent.parent / "src" / "nadira_bot"
        assert src_dir.exists()

        forbidden_mojibake_patterns = [
            "ÔØî",
            "ÔÅ│",
            "ÔÜá",
            "ƒöè",
            "ÔÅ╣",
            "\ufffd",
        ]

        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pattern in forbidden_mojibake_patterns:
                assert pattern not in content, (
                    f"File {py_file.name} mengandung pola mojibake '{pattern}'"
                )
