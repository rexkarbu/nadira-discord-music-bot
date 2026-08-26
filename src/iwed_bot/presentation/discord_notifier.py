"""Implementasi PlaybackNotifier menggunakan Discord API."""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from iwed_bot.ports.notifications import PlaybackNotifier

if TYPE_CHECKING:
    from iwed_bot.bot import IwedBot

logger = logging.getLogger(__name__)


class DiscordPlaybackNotifier(PlaybackNotifier):
    """Notifier yang mengirimkan pesan status playback ke Discord text channel."""

    def __init__(self, bot: "IwedBot | discord.Client | Any") -> None:
        self.bot = bot

    async def notify_playback_halted(
        self,
        guild_id: int,
        text_channel_id: int | None,
        operation_id: UUID,
        failed_count: int,
    ) -> None:
        """Mengirim embed peringatan ke text channel saat safety cap failure tercapai."""
        try:
            channel = None
            if text_channel_id is not None:
                channel = self.bot.get_channel(text_channel_id)
                if channel is None and hasattr(self.bot, "fetch_channel"):
                    try:
                        channel = await self.bot.fetch_channel(text_channel_id)
                    except Exception:
                        channel = None

            if channel is None:
                guild = self.bot.get_guild(guild_id)
                if guild is not None:
                    channel = guild.system_channel

            if channel is not None and isinstance(channel, (discord.TextChannel, discord.Thread)):
                embed = discord.Embed(
                    title="⚠️ Pemutaran Musik Dihentikan Otomatis",
                    description=(
                        f"Pemutaran musik dihentikan setelah terjadi kegagalan pemutaran pada "
                        f"**{failed_count} lagu** berturut-turut.\n\n"
                        "Antrean lagu yang belum dicoba tetap tersimpan. "
                        "Gunakan `/play` untuk mencoba memutar lagu kembali."
                    ),
                    color=discord.Color.orange(),
                )
                embed.set_footer(text=f"ID Operasi: {operation_id}")
                await channel.send(embed=embed)
        except Exception as err:
            logger.warning(
                "Gagal mengirim notifikasi discord failure halt",
                extra={"guild_id": guild_id, "error_type": type(err).__name__},
            )
