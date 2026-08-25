"""Unit tests untuk WavelinkVoiceGateway adapter."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import wavelink

from nadira_bot.application.errors import (
    UnexpectedVoiceClient,
    UnsupportedVoiceChannel,
    VoiceMoveFailed,
)
from nadira_bot.infrastructure.voice.wavelink_gateway import WavelinkVoiceGateway


class TestWavelinkVoiceGateway:
    @pytest.mark.asyncio
    async def test_connect_uses_wavelink_player_and_self_deaf(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        guild.voice_client = None

        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = 555

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.channel = channel
        channel.connect = AsyncMock(return_value=mock_player)

        guild.get_channel.return_value = channel
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        snap = await gateway.connect(guild_id=100, channel_id=555)

        channel.connect.assert_called_once_with(
            cls=wavelink.Player,
            timeout=10.0,
            reconnect=True,
            self_deaf=True,
        )
        assert snap.is_connected
        assert snap.channel_id == 555

    @pytest.mark.asyncio
    async def test_connect_rejects_stage_channel(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        stage_ch = MagicMock(spec=discord.StageChannel)
        guild.get_channel.return_value = stage_ch
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        with pytest.raises(UnsupportedVoiceChannel, match="tidak mendukung Stage Channel"):
            await gateway.connect(guild_id=100, channel_id=555)

    @pytest.mark.asyncio
    async def test_move_calls_player_move_to(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        new_channel = MagicMock(spec=discord.VoiceChannel)
        new_channel.id = 777

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.channel = new_channel
        mock_player.move_to = AsyncMock()
        guild.voice_client = mock_player

        guild.get_channel.return_value = new_channel
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        snap = await gateway.move(guild_id=100, channel_id=777)

        mock_player.move_to.assert_called_once_with(new_channel)
        assert snap.channel_id == 777

    @pytest.mark.asyncio
    async def test_disconnected_player_move_to_await_count_zero_and_connects_fresh(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)

        stale_player = MagicMock(spec=wavelink.Player)
        stale_player.connected = False
        stale_player.move_to = AsyncMock()
        stale_player.disconnect = AsyncMock()
        guild.voice_client = stale_player

        new_channel = MagicMock(spec=discord.VoiceChannel)
        new_channel.id = 777
        fresh_player = MagicMock(spec=wavelink.Player)
        fresh_player.connected = True
        fresh_player.channel = new_channel
        new_channel.connect = AsyncMock(return_value=fresh_player)

        guild.get_channel.return_value = new_channel
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        snap = await gateway.move(guild_id=100, channel_id=777)

        # move_to TIDAK boleh dipanggil pada disconnected player
        stale_player.move_to.assert_not_called()
        # Fresh connect harus dipanggil
        new_channel.connect.assert_called_once()
        assert snap.channel_id == 777
        assert snap.is_connected

    @pytest.mark.asyncio
    async def test_move_result_not_connected_raises_voice_move_failed(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        guild.voice_client = mock_player

        async def fake_move_to(_ch: object) -> None:
            # Player disconnected during move
            mock_player.connected = False

        mock_player.move_to = AsyncMock(side_effect=fake_move_to)

        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        guild.get_channel.return_value = target_ch
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        with pytest.raises(VoiceMoveFailed):
            await gateway.move(guild_id=100, channel_id=777)

    @pytest.mark.asyncio
    async def test_unexpected_voice_client_raises_unexpected_voice_client(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        # Non-wavelink voice client
        foreign_client = MagicMock(spec=discord.VoiceProtocol)
        guild.voice_client = foreign_client

        target_ch = MagicMock(spec=discord.VoiceChannel)
        target_ch.id = 777
        guild.get_channel.return_value = target_ch
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        with pytest.raises(UnexpectedVoiceClient):
            await gateway.move(guild_id=100, channel_id=777)

        with pytest.raises(UnexpectedVoiceClient):
            await gateway.connect(guild_id=100, channel_id=777)

        with pytest.raises(UnexpectedVoiceClient):
            await gateway.disconnect(guild_id=100)

        with pytest.raises(UnexpectedVoiceClient):
            await gateway.get_connection(guild_id=100)

    @pytest.mark.asyncio
    async def test_disconnect_without_player_is_noop(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        guild.voice_client = None
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        await gateway.disconnect(guild_id=100)

    @pytest.mark.asyncio
    async def test_disconnect_calls_player_disconnect(self) -> None:
        bot = MagicMock(spec=discord.Client)
        guild = MagicMock(spec=discord.Guild)
        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.disconnect = AsyncMock()
        guild.voice_client = mock_player
        bot.get_guild.return_value = guild

        gateway = WavelinkVoiceGateway(bot)
        await gateway.disconnect(guild_id=100)

        mock_player.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_all_guilds_and_continues_on_failure(self) -> None:
        bot = MagicMock(spec=discord.Client)

        g1 = MagicMock(spec=discord.Guild)
        g1.id = 101
        p1 = MagicMock(spec=wavelink.Player)
        p1.disconnect = AsyncMock()
        g1.voice_client = p1

        g2 = MagicMock(spec=discord.Guild)
        g2.id = 102
        p2 = MagicMock(spec=wavelink.Player)
        p2.disconnect = AsyncMock(side_effect=RuntimeError("Disconnect failed on g2"))
        g2.voice_client = p2

        g3 = MagicMock(spec=discord.Guild)
        g3.id = 103
        p3 = MagicMock(spec=wavelink.Player)
        p3.disconnect = AsyncMock()
        g3.voice_client = p3

        bot.guilds = [g1, g2, g3]

        gateway = WavelinkVoiceGateway(bot)
        await gateway.shutdown()

        p1.disconnect.assert_called_once()
        p2.disconnect.assert_called_once()
        p3.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_connections_is_noop(self) -> None:
        bot = MagicMock(spec=discord.Client)
        g1 = MagicMock(spec=discord.Guild)
        g1.voice_client = None
        bot.guilds = [g1]

        gateway = WavelinkVoiceGateway(bot)
        await gateway.shutdown()
