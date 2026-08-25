"""Fungsi validasi izin dan voice state untuk slash commands Iwed."""

import discord

from iwed_bot.application.errors import (
    BotMissingVoicePermission,
    GuildOnlyCommand,
    UnsupportedVoiceChannel,
    UserNotInVoice,
    VoiceChannelFull,
)


def ensure_guild_context(interaction: discord.Interaction) -> discord.Guild:
    """Memastikan interaksi dijalankan di dalam guild dan user adalah Member."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise GuildOnlyCommand()
    return interaction.guild


def ensure_user_in_voice(interaction: discord.Interaction) -> discord.VoiceChannel:
    """Memastikan pengguna yang menjalankan perintah berada dalam VoiceChannel standar."""
    if (
        not isinstance(interaction.user, discord.Member)
        or interaction.user.voice is None
        or interaction.user.voice.channel is None
    ):
        raise UserNotInVoice()

    channel = interaction.user.voice.channel
    if isinstance(channel, discord.StageChannel):
        raise UnsupportedVoiceChannel(
            "Iwed saat ini hanya mendukung voice channel biasa (bukan Stage Channel)."
        )

    if not isinstance(channel, discord.VoiceChannel):
        raise UnsupportedVoiceChannel(
            "Channel yang dimasuki bukan merupakan voice channel standar."
        )

    return channel


def check_bot_voice_permissions(channel: discord.VoiceChannel, me: discord.Member) -> None:
    """Memvalidasi bahwa bot memiliki hak akses View Channel, Connect, dan Speak."""
    permissions = channel.permissions_for(me)
    missing: list[str] = []

    if not permissions.view_channel:
        missing.append("View Channel")
    if not permissions.connect:
        missing.append("Connect")
    if not permissions.speak:
        missing.append("Speak")

    if missing:
        raise BotMissingVoicePermission(tuple(missing))

    # Periksa kapasitas channel
    if (
        channel.user_limit > 0
        and len(channel.members) >= channel.user_limit
        and not permissions.move_members
        and me not in channel.members
    ):
        raise VoiceChannelFull("Voice channel tujuan sudah penuh.")
