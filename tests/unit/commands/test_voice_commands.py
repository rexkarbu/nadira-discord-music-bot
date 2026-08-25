"""Unit tests untuk VoiceCog (/join, /stop, dan voice_state_update)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from nadira_bot.application.errors import DifferentVoiceChannel
from nadira_bot.commands.voice import VoiceCog
from nadira_bot.domain.models import PlaybackState, VersionedGuildSession
from nadira_bot.ports.voice import VoiceConnectionSnapshot


class TestVoiceCog:
    @pytest.mark.asyncio
    async def test_join_defers_before_service_call(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345

        cog = VoiceCog(bot)

        # Setup mocks
        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100
        guild.me = MagicMock(spec=discord.Member)
        guild.me.voice = None  # Bot belum terhubung

        voice_ch = MagicMock(spec=discord.VoiceChannel)
        voice_ch.id = 555
        voice_ch.name = "General Voice"
        voice_ch.user_limit = 0

        perms = MagicMock()
        perms.view_channel = True
        perms.connect = True
        perms.speak = True
        perms.move_members = False  # Bot belum connected, tidak butuh move
        voice_ch.permissions_for.return_value = perms

        member = MagicMock(spec=discord.Member)
        voice_state = MagicMock(spec=discord.VoiceState)
        voice_state.channel = voice_ch
        member.voice = voice_state

        interaction.guild = guild
        interaction.user = member
        interaction.channel_id = 999
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()

        # Track execution order: defer must happen before voice_service.join
        call_order = []

        async def defer_side_effect(*_args: object, **_kwargs: object) -> None:
            call_order.append("DEFER")
            interaction.response.is_done.return_value = True

        interaction.response.defer.side_effect = defer_side_effect

        snap = VoiceConnectionSnapshot(guild_id=100, channel_id=555, is_connected=True)
        session = VersionedGuildSession(guild_id=100, state=PlaybackState.IDLE)

        async def join_side_effect(
            *_args: object, **_kwargs: object
        ) -> tuple[VoiceConnectionSnapshot, VersionedGuildSession, bool, bool]:
            call_order.append("SERVICE_JOIN")
            return snap, session, False, False

        bot.voice_service.join = AsyncMock(side_effect=join_side_effect)
        interaction.edit_original_response = AsyncMock()

        await cog.join.callback(cog, interaction)  # type: ignore[misc]

        assert call_order == ["DEFER", "SERVICE_JOIN"]
        interaction.response.defer.assert_called_once_with(ephemeral=True, thinking=True)
        interaction.edit_original_response.assert_called_once()
        assert (
            "Terhubung ke voice channel"
            in interaction.edit_original_response.call_args[1]["content"]
        )

    @pytest.mark.asyncio
    async def test_join_move_members_origin_false_target_true_rejected(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345
        cog = VoiceCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100

        # Bot berada di channel 555
        origin_ch = MagicMock(spec=discord.VoiceChannel)
        origin_ch.id = 555
        bot_voice = MagicMock(spec=discord.VoiceState)
        bot_voice.channel = origin_ch
        guild.me = MagicMock(spec=discord.Member)
        guild.me.voice = bot_voice

        # User berada di target channel 777
        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        target_ch.user_limit = 0

        member = MagicMock(spec=discord.Member)
        user_voice = MagicMock(spec=discord.VoiceState)
        user_voice.channel = target_ch
        member.voice = user_voice

        # Permission: Target=True, Origin=False
        perms_bot = MagicMock(view_channel=True, connect=True, speak=True)
        perms_origin = MagicMock(move_members=False)
        perms_target = MagicMock(move_members=True)

        def channel_permissions_for(m: object) -> MagicMock:
            if m == guild.me:
                return perms_bot
            return perms_target

        target_ch.permissions_for.side_effect = channel_permissions_for
        origin_ch.permissions_for.return_value = perms_origin

        interaction.guild = guild
        interaction.user = member
        interaction.response = MagicMock(is_done=MagicMock(return_value=True), defer=AsyncMock())

        bot.voice_service.join = AsyncMock(
            side_effect=DifferentVoiceChannel(
                "Bot sedang berada di voice channel lain. Diperlukan izin Move Members."
            )
        )

        with pytest.raises(DifferentVoiceChannel):
            await cog.join.callback(cog, interaction)  # type: ignore[misc]

        # Buktikan can_move_members diteruskan sebagai False dan origin_channel_id=555
        bot.voice_service.join.assert_called_once_with(
            guild_id=100,
            channel_id=777,
            text_channel_id=interaction.channel_id,
            origin_channel_id=555,
            can_move_members=False,
        )

    @pytest.mark.asyncio
    async def test_join_move_members_origin_true_target_false_rejected(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345
        cog = VoiceCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100

        # Bot berada di channel 555
        origin_ch = MagicMock(spec=discord.VoiceChannel)
        origin_ch.id = 555
        bot_voice = MagicMock(spec=discord.VoiceState)
        bot_voice.channel = origin_ch
        guild.me = MagicMock(spec=discord.Member)
        guild.me.voice = bot_voice

        # User berada di target channel 777
        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        target_ch.user_limit = 0

        member = MagicMock(spec=discord.Member)
        user_voice = MagicMock(spec=discord.VoiceState)
        user_voice.channel = target_ch
        member.voice = user_voice

        # Permission: Target=False, Origin=True
        perms_bot = MagicMock(view_channel=True, connect=True, speak=True)
        perms_origin = MagicMock(move_members=True)
        perms_target = MagicMock(move_members=False)

        def channel_permissions_for(m: object) -> MagicMock:
            if m == guild.me:
                return perms_bot
            return perms_target

        target_ch.permissions_for.side_effect = channel_permissions_for
        origin_ch.permissions_for.return_value = perms_origin

        interaction.guild = guild
        interaction.user = member
        interaction.response = MagicMock(is_done=MagicMock(return_value=True), defer=AsyncMock())

        bot.voice_service.join = AsyncMock(
            side_effect=DifferentVoiceChannel(
                "Bot sedang berada di voice channel lain. Diperlukan izin Move Members."
            )
        )

        with pytest.raises(DifferentVoiceChannel):
            await cog.join.callback(cog, interaction)  # type: ignore[misc]

        bot.voice_service.join.assert_called_once_with(
            guild_id=100,
            channel_id=777,
            text_channel_id=interaction.channel_id,
            origin_channel_id=555,
            can_move_members=False,
        )

    @pytest.mark.asyncio
    async def test_join_move_members_origin_true_target_true_calls_move_once(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345
        cog = VoiceCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100

        # Bot berada di channel 555
        origin_ch = MagicMock(spec=discord.VoiceChannel)
        origin_ch.id = 555
        bot_voice = MagicMock(spec=discord.VoiceState)
        bot_voice.channel = origin_ch
        guild.me = MagicMock(spec=discord.Member)
        guild.me.voice = bot_voice

        # User berada di target channel 777
        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        target_ch.name = "Target Voice"
        target_ch.user_limit = 0

        member = MagicMock(spec=discord.Member)
        user_voice = MagicMock(spec=discord.VoiceState)
        user_voice.channel = target_ch
        member.voice = user_voice

        # Permission: Target=True, Origin=True
        perms_bot = MagicMock(view_channel=True, connect=True, speak=True)
        perms_origin = MagicMock(move_members=True)
        perms_target = MagicMock(move_members=True)

        def channel_permissions_for(m: object) -> MagicMock:
            if m == guild.me:
                return perms_bot
            return perms_target

        target_ch.permissions_for.side_effect = channel_permissions_for
        origin_ch.permissions_for.return_value = perms_origin

        interaction.guild = guild
        interaction.user = member
        interaction.response = MagicMock(is_done=MagicMock(return_value=True), defer=AsyncMock())
        interaction.edit_original_response = AsyncMock()

        snap = VoiceConnectionSnapshot(guild_id=100, channel_id=777, is_connected=True)
        session = VersionedGuildSession(
            guild_id=100, voice_channel_id=777, state=PlaybackState.IDLE
        )
        bot.voice_service.join = AsyncMock(return_value=(snap, session, True, False))

        await cog.join.callback(cog, interaction)  # type: ignore[misc]

        bot.voice_service.join.assert_called_once_with(
            guild_id=100,
            channel_id=777,
            text_channel_id=interaction.channel_id,
            origin_channel_id=555,
            can_move_members=True,
        )
        assert (
            "[BERHASIL] Memindahkan Nadira"
            in interaction.edit_original_response.call_args[1]["content"]
        )

    @pytest.mark.asyncio
    async def test_join_bot_not_connected_does_not_require_move_members(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345
        cog = VoiceCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100
        guild.me = MagicMock(spec=discord.Member)
        guild.me.voice = None  # Bot belum terhubung

        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        target_ch.name = "Target Voice"
        target_ch.user_limit = 0

        member = MagicMock(spec=discord.Member)
        user_voice = MagicMock(spec=discord.VoiceState)
        user_voice.channel = target_ch
        member.voice = user_voice

        # User TIDAK memiliki Move Members
        perms_bot = MagicMock(view_channel=True, connect=True, speak=True)
        perms_target = MagicMock(move_members=False)

        def channel_permissions_for(m: object) -> MagicMock:
            if m == guild.me:
                return perms_bot
            return perms_target

        target_ch.permissions_for.side_effect = channel_permissions_for

        interaction.guild = guild
        interaction.user = member
        interaction.response = MagicMock(is_done=MagicMock(return_value=True), defer=AsyncMock())
        interaction.edit_original_response = AsyncMock()

        snap = VoiceConnectionSnapshot(guild_id=100, channel_id=777, is_connected=True)
        session = VersionedGuildSession(
            guild_id=100, voice_channel_id=777, state=PlaybackState.IDLE
        )
        bot.voice_service.join = AsyncMock(return_value=(snap, session, False, False))

        await cog.join.callback(cog, interaction)  # type: ignore[misc]

        bot.voice_service.join.assert_called_once()
        assert "[BERHASIL] Terhubung" in interaction.edit_original_response.call_args[1]["content"]

    @pytest.mark.asyncio
    async def test_stop_defers_before_service_call(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345

        cog = VoiceCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100

        member = MagicMock(spec=discord.Member)
        voice_ch = MagicMock(spec=discord.VoiceChannel)
        voice_ch.id = 555
        voice_state = MagicMock(spec=discord.VoiceState)
        voice_state.channel = voice_ch
        member.voice = voice_state

        interaction.guild = guild
        interaction.user = member
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()

        call_order = []

        async def defer_side_effect(*_args: object, **_kwargs: object) -> None:
            call_order.append("DEFER")
            interaction.response.is_done.return_value = True

        interaction.response.defer.side_effect = defer_side_effect

        session = VersionedGuildSession(guild_id=100, state=PlaybackState.DISCONNECTED)

        async def stop_side_effect(
            *_args: object, **_kwargs: object
        ) -> tuple[VersionedGuildSession, bool]:
            call_order.append("SERVICE_STOP")
            return session, True

        bot.voice_service.stop = AsyncMock(side_effect=stop_side_effect)
        interaction.edit_original_response = AsyncMock()

        await cog.stop.callback(cog, interaction)  # type: ignore[misc]

        assert call_order == ["DEFER", "SERVICE_STOP"]
        interaction.response.defer.assert_called_once_with(ephemeral=True, thinking=True)
        assert (
            "[INFO] Pemutaran dihentikan"
            in interaction.edit_original_response.call_args[1]["content"]
        )

    @pytest.mark.asyncio
    async def test_on_voice_state_update_listener_filters_non_bot_members(self) -> None:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 12345
        bot.voice_service.handle_voice_state_update = AsyncMock()

        cog = VoiceCog(bot)

        other_member = MagicMock(spec=discord.Member)
        other_member.id = 99999
        before = MagicMock(spec=discord.VoiceState)
        after = MagicMock(spec=discord.VoiceState)

        await cog.on_voice_state_update(other_member, before, after)
        bot.voice_service.handle_voice_state_update.assert_not_called()

        # Bot member
        bot_member = MagicMock(spec=discord.Member)
        bot_member.id = 12345
        bot_member.guild.id = 100
        before.channel = MagicMock(id=555)
        after.channel = None

        await cog.on_voice_state_update(bot_member, before, after)
        bot.voice_service.handle_voice_state_update.assert_called_once_with(
            guild_id=100,
            old_channel_id=555,
            new_channel_id=None,
            is_stage=False,
        )
