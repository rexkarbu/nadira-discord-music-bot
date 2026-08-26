"""Unit tests untuk WavelinkPlaybackGateway adapter."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from iwed_bot.application.errors import PlaybackFailed
from iwed_bot.domain.models import SourceType, TrackReference
from iwed_bot.infrastructure.playback.wavelink_gateway import WavelinkPlaybackGateway
from iwed_bot.ports.playback import PreparedPlayback


def make_test_track(title: str = "Test") -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="id123",
        source_uri="https://www.youtube.com/watch?v=id123",
        search_hint="artist - test",
        title=title,
        artists=("Artist",),
        duration_ms=200000,
        thumbnail_url=None,
        canonical_url=None,
    )


class TestWavelinkPlaybackGateway:
    @pytest.mark.asyncio
    async def test_prepare_reference_success(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot, jit_timeout=5.0)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_playable]
            prepared = await gateway.prepare_reference(guild_id=123, track=track)

            assert isinstance(prepared, PreparedPlayback)
            assert prepared.track_id == track.id
            assert prepared.handle_id in gateway._prepared_handles

    @pytest.mark.asyncio
    async def test_play_prepared_success_and_history_disabled(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot, jit_timeout=5.0)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)
        mock_playable.extras = {}

        # Mock player
        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.playing = True
        mock_player.paused = False
        mock_player.position = 0
        mock_player.play = AsyncMock()

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, track.id, mock_playable)
        prepared = PreparedPlayback(handle_id=handle_id, track_id=track.id)

        entry_id = uuid.uuid4()
        snapshot = await gateway.play_prepared(
            guild_id=123,
            prepared=prepared,
            entry_id=entry_id,
            generation=1,
            volume=80,
        )

        assert snapshot.connected is True
        assert snapshot.active_entry_id == entry_id
        assert snapshot.active_generation == 1
        # Handle should be consumed and removed
        assert handle_id not in gateway._prepared_handles

        # Assert wavelink.Player.play was called with exact required parameters
        mock_player.play.assert_awaited_once_with(
            mock_playable,
            replace=True,
            volume=80,
            add_history=False,
            populate=False,
        )
        assert mock_player.autoplay == wavelink.AutoPlayMode.disabled
        assert mock_playable.extras["entry_id"] == str(entry_id)
        assert mock_playable.extras["generation"] == 1

    @pytest.mark.asyncio
    async def test_play_prepared_expired_handle_raises_playback_failed(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        prepared = PreparedPlayback(handle_id=uuid.uuid4(), track_id=uuid.uuid4())

        with pytest.raises(PlaybackFailed, match="Handle prepared playback tidak ditemukan"):
            await gateway.play_prepared(123, prepared, uuid.uuid4(), 1)

    @pytest.mark.asyncio
    async def test_play_prepared_handle_is_single_use(self) -> None:
        """Memverifikasi handle yang sudah dikonsumsi play_prepared tidak dapat digunakan ulang."""
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)
        mock_playable.extras = {}

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.playing = True
        mock_player.paused = False
        mock_player.position = 0
        mock_player.play = AsyncMock()

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, track.id, mock_playable)
        prepared = PreparedPlayback(handle_id=handle_id, track_id=track.id)

        # First use succeeds
        await gateway.play_prepared(123, prepared, uuid.uuid4(), 1)

        # Second use with same handle fails
        with pytest.raises(PlaybackFailed, match="Handle prepared playback tidak ditemukan"):
            await gateway.play_prepared(123, prepared, uuid.uuid4(), 1)

    @pytest.mark.asyncio
    async def test_play_prepared_cross_guild_rejected(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)

        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, track.id, mock_playable)
        prepared = PreparedPlayback(handle_id=handle_id, track_id=track.id)

        with pytest.raises(PlaybackFailed, match="Handle prepared playback tidak cocok"):
            # Call with guild_id=999 instead of 123
            await gateway.play_prepared(999, prepared, uuid.uuid4(), 1)

    @pytest.mark.asyncio
    async def test_cross_guild_handle_is_rejected(self) -> None:
        """Alias test untuk memverifikasi cross guild handle preparation ditolak."""
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)

        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, track.id, mock_playable)
        prepared = PreparedPlayback(handle_id=handle_id, track_id=track.id)

        with pytest.raises(PlaybackFailed, match="Handle prepared playback tidak cocok"):
            await gateway.play_prepared(999, prepared, uuid.uuid4(), 1)

    @pytest.mark.asyncio
    async def test_discard_prepared_removes_handle(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, uuid.uuid4(), MagicMock())

        prepared = PreparedPlayback(handle_id=handle_id, track_id=uuid.uuid4())
        await gateway.discard_prepared(prepared)
        assert handle_id not in gateway._prepared_handles

    @pytest.mark.asyncio
    async def test_stop_current_calls_skip_force(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.skip = AsyncMock()

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        await gateway.stop_current(123)
        mock_player.skip.assert_awaited_once_with(force=True)

    @pytest.mark.asyncio
    async def test_pause_and_resume(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.playing = True
        mock_player.paused = True
        mock_player.position = 5000
        mock_player.current = None
        mock_player.pause = AsyncMock()

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        snap = await gateway.pause(123, True)
        mock_player.pause.assert_awaited_once_with(True)
        assert snap.is_paused is True
        assert snap.position_ms == 5000

    @pytest.mark.asyncio
    async def test_play_prepared_with_volume_zero_calls_set_volume(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        track = make_test_track()
        mock_playable = MagicMock(spec=wavelink.Playable)
        mock_playable.extras = {}

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.playing = True
        mock_player.paused = False
        mock_player.position = 0
        mock_player.play = AsyncMock()
        mock_player.set_volume = AsyncMock()

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        handle_id = uuid.uuid4()
        gateway._prepared_handles[handle_id] = (123, track.id, mock_playable)
        prepared = PreparedPlayback(handle_id=handle_id, track_id=track.id)

        entry_id = uuid.uuid4()
        await gateway.play_prepared(
            guild_id=123,
            prepared=prepared,
            entry_id=entry_id,
            generation=1,
            volume=0,
        )

        mock_player.play.assert_awaited_once()
        mock_player.set_volume.assert_awaited_once_with(0)

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_handles(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)
        handle_id1 = uuid.uuid4()
        handle_id2 = uuid.uuid4()
        gateway._prepared_handles[handle_id1] = (123, uuid.uuid4(), MagicMock())
        gateway._prepared_handles[handle_id2] = (456, uuid.uuid4(), MagicMock())

        assert len(gateway._prepared_handles) == 2
        await gateway.shutdown()
        assert len(gateway._prepared_handles) == 0
