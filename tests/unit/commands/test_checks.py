"""Unit tests untuk command checks dan permission validators."""

from unittest.mock import MagicMock

import discord
import pytest

from nadira_bot.application.errors import (
    BotMissingVoicePermission,
    GuildOnlyCommand,
    UnsupportedVoiceChannel,
    UserNotInVoice,
    VoiceChannelFull,
)
from nadira_bot.commands.checks import (
    check_bot_voice_permissions,
    ensure_guild_context,
    ensure_user_in_voice,
)


class TestCommandChecks:
    def test_ensure_guild_context_dm_rejected(self) -> None:
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = None
        interaction.user = MagicMock(spec=discord.User)

        with pytest.raises(GuildOnlyCommand):
            ensure_guild_context(interaction)

    def test_ensure_guild_context_in_guild_accepted(self) -> None:
        guild = MagicMock(spec=discord.Guild)
        member = MagicMock(spec=discord.Member)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = guild
        interaction.user = member

        result = ensure_guild_context(interaction)
        assert result is guild

    def test_ensure_user_in_voice_not_in_voice_rejected(self) -> None:
        member = MagicMock(spec=discord.Member)
        member.voice = None
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = member

        with pytest.raises(UserNotInVoice):
            ensure_user_in_voice(interaction)

    def test_ensure_user_in_voice_stage_channel_rejected(self) -> None:
        stage_ch = MagicMock(spec=discord.StageChannel)
        voice_state = MagicMock(spec=discord.VoiceState)
        voice_state.channel = stage_ch

        member = MagicMock(spec=discord.Member)
        member.voice = voice_state

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = member

        with pytest.raises(UnsupportedVoiceChannel, match="bukan Stage Channel"):
            ensure_user_in_voice(interaction)

    def test_ensure_user_in_voice_standard_channel_accepted(self) -> None:
        voice_ch = MagicMock(spec=discord.VoiceChannel)
        voice_state = MagicMock(spec=discord.VoiceState)
        voice_state.channel = voice_ch

        member = MagicMock(spec=discord.Member)
        member.voice = voice_state

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = member

        result = ensure_user_in_voice(interaction)
        assert result is voice_ch

    def test_check_bot_voice_permissions_missing(self) -> None:
        channel = MagicMock(spec=discord.VoiceChannel)
        me = MagicMock(spec=discord.Member)

        perms = MagicMock()
        perms.view_channel = True
        perms.connect = False
        perms.speak = False
        channel.permissions_for.return_value = perms

        with pytest.raises(BotMissingVoicePermission) as exc_info:
            check_bot_voice_permissions(channel, me)

        assert "Connect" in exc_info.value.missing_permissions
        assert "Speak" in exc_info.value.missing_permissions
        assert "View Channel" not in exc_info.value.missing_permissions

    def test_check_bot_voice_permissions_channel_full(self) -> None:
        channel = MagicMock(spec=discord.VoiceChannel)
        me = MagicMock(spec=discord.Member)

        perms = MagicMock()
        perms.view_channel = True
        perms.connect = True
        perms.speak = True
        perms.move_members = False
        channel.permissions_for.return_value = perms

        channel.user_limit = 5
        channel.members = [MagicMock() for _ in range(5)]  # 5 members present
        # me is not in members
        assert me not in channel.members

        with pytest.raises(VoiceChannelFull):
            check_bot_voice_permissions(channel, me)
