"""Slash commands /join dan /stop serta event listener voice state Iwed."""

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from iwed_bot.commands.checks import (
    check_bot_voice_permissions,
    ensure_guild_context,
    ensure_user_in_voice,
)
from iwed_bot.presentation.interactions import respond_or_edit

if TYPE_CHECKING:
    from iwed_bot.bot import IwedBot

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    """Cog yang memuat perintah kontrol koneksi voice (/join, /stop) dan rekonsiliasi state."""

    def __init__(self, bot: "IwedBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="join",
        description="Hubungkan Iwed ke voice channel kamu.",
    )
    async def join(self, interaction: discord.Interaction) -> None:
        """Menghubungkan bot ke voice channel pengguna atau memindahkan jika ada izin."""
        # Defer interaksi secara ephemeral sebelum melakukan network I/O
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = ensure_guild_context(interaction)
        channel = ensure_user_in_voice(interaction)

        me = guild.me
        if me is None and self.bot.user:
            me = await guild.fetch_member(self.bot.user.id)

        if me is not None:
            check_bot_voice_permissions(channel, me)

        # Periksa apakah pengguna memiliki hak Move Members pada channel tujuan dan channel asal bot
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        target_permissions = channel.permissions_for(member) if member else None
        can_move_target = target_permissions.move_members if target_permissions else False

        can_move_origin = True
        origin_channel_id: int | None = None
        bot_voice = me.voice if (me and me.voice) else None
        if bot_voice and bot_voice.channel:
            origin_channel_id = bot_voice.channel.id
            if bot_voice.channel.id != channel.id:
                origin_permissions = bot_voice.channel.permissions_for(member) if member else None
                can_move_origin = origin_permissions.move_members if origin_permissions else False

        can_move = can_move_target and can_move_origin

        _conn, _session, is_move, is_noop = await self.bot.voice_service.join(
            guild_id=guild.id,
            channel_id=channel.id,
            text_channel_id=interaction.channel_id,
            origin_channel_id=origin_channel_id,
            can_move_members=can_move,
        )

        if is_noop:
            msg = f"[INFO] Iwed sudah berada di voice channel: **{channel.name}**."
        elif is_move:
            msg = f"[BERHASIL] Memindahkan Iwed ke voice channel: **{channel.name}**."
        else:
            msg = f"[BERHASIL] Terhubung ke voice channel: **{channel.name}**."

        await respond_or_edit(interaction, msg, ephemeral=True)
        logger.info(
            "Slash command /join berhasil dieksekusi",
            extra={
                "guild_id": guild.id,
                "channel_id": channel.id,
                "user_id": interaction.user.id,
                "is_move": is_move,
                "is_noop": is_noop,
            },
        )

    @app_commands.command(
        name="stop",
        description="Hentikan pemutaran, kosongkan antrean, dan putuskan koneksi voice.",
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        """Menghentikan pemutaran musik dan memutuskan bot dari voice channel."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = ensure_guild_context(interaction)

        requester_channel_id = None
        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.voice
            and interaction.user.voice.channel is not None
        ):
            requester_channel_id = interaction.user.voice.channel.id

        _session, was_active = await self.bot.voice_service.stop(
            guild_id=guild.id,
            requester_channel_id=requester_channel_id,
        )

        if was_active:
            msg = (
                "[INFO] Pemutaran dihentikan, antrean dikosongkan, "
                "dan bot telah keluar dari voice channel."
            )
        else:
            msg = "[INFO] Iwed sudah dalam keadaan berhenti dan tidak terhubung ke voice channel."

        await respond_or_edit(interaction, msg, ephemeral=True)
        logger.info(
            "Slash command /stop berhasil dieksekusi",
            extra={
                "guild_id": guild.id,
                "user_id": interaction.user.id,
                "was_active": was_active,
            },
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Merekonsiliasi state internal saat bot dipindahkan atau di-kick dari voice channel."""
        if not self.bot.user or member.id != self.bot.user.id:
            return

        old_ch = before.channel.id if before.channel else None
        new_ch = after.channel.id if after.channel else None
        is_stage = isinstance(after.channel, discord.StageChannel)

        try:
            await self.bot.voice_service.handle_voice_state_update(
                guild_id=member.guild.id,
                old_channel_id=old_ch,
                new_channel_id=new_ch,
                is_stage=is_stage,
            )
        except Exception as err:
            logger.error(
                "Gagal merekonsiliasi voice state update eksternal bot",
                exc_info=err,
                extra={"guild_id": member.guild.id},
            )


async def setup(bot: "IwedBot") -> None:
    """Fungsi entrypoint untuk memuat VoiceCog ke dalam bot."""
    await bot.add_cog(VoiceCog(bot))
