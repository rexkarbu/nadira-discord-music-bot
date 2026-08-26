"""Central Command Tree dan global error handler untuk Iwed Discord Music Bot."""

import logging
import uuid

import discord
from discord import app_commands

from iwed_bot.application.errors import IwedApplicationError
from iwed_bot.domain.errors import IwedDomainError
from iwed_bot.presentation.interactions import (
    format_user_error_message,
    respond_or_edit,
    unwrap_command_error,
)

logger = logging.getLogger(__name__)


class IwedCommandTree(app_commands.CommandTree):
    """Subclass CommandTree yang menyuntikkan correlation ID dan menangani error secara terpusat."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Membuat correlation ID unik untuk setiap interaksi slash command."""
        if "correlation_id" not in interaction.extras:
            interaction.extras["correlation_id"] = uuid.uuid4()
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Global error handler untuk seluruh slash command Iwed."""
        correlation_id: uuid.UUID = interaction.extras.get("correlation_id", uuid.uuid4())
        original_error = unwrap_command_error(error)

        # 1. Periksa apakah error merupakan domain/application typed error yang diharapkan
        is_known_error = isinstance(original_error, (IwedApplicationError, IwedDomainError))

        if is_known_error:
            logger.warning(
                "Command validation / operational error",
                extra={
                    "correlation_id": str(correlation_id),
                    "guild_id": interaction.guild_id,
                    "user_id": interaction.user.id,
                    "command_name": interaction.command.name if interaction.command else None,
                    "error_type": type(original_error).__name__,
                },
            )
        else:
            logger.error(
                "Unhandled internal exception dalam slash command",
                exc_info=original_error,
                extra={
                    "correlation_id": str(correlation_id),
                    "guild_id": interaction.guild_id,
                    "user_id": interaction.user.id,
                    "command_name": interaction.command.name if interaction.command else None,
                    "error_type": type(original_error).__name__,
                },
            )

        # 2. Format pesan yang aman dan ramah pengguna
        user_message = format_user_error_message(original_error, correlation_id)

        # 3. Kirim respons ephemeral ke Discord
        try:
            await respond_or_edit(interaction, user_message, ephemeral=True)
        except Exception as delivery_err:
            logger.error(
                "Gagal mengirim pesan error ke pengguna",
                exc_info=delivery_err,
                extra={"correlation_id": str(correlation_id)},
            )
