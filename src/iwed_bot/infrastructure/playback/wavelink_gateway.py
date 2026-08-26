"""Wavelink PlaybackGateway adapter untuk Iwed Discord Music Bot."""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

import wavelink

from iwed_bot.application.errors import (
    PlaybackFailed,
    PlaylistImportDeferred,
    SourceLoadFailed,
    SourceTimeout,
    TrackNotFound,
    VoiceConnectionFailed,
)
from iwed_bot.domain.models import TrackReference
from iwed_bot.infrastructure.voice.player_helper import get_wavelink_player
from iwed_bot.ports.playback import PlaybackGateway, PlaybackSnapshot, PreparedPlayback

if TYPE_CHECKING:
    import discord

    from iwed_bot.bot import IwedBot

logger = logging.getLogger(__name__)


class WavelinkPlaybackGateway(PlaybackGateway):
    """Adapter yang menghubungkan layer aplikasi ke Wavelink Player untuk operasi audio playback."""

    def __init__(self, bot: "IwedBot | discord.Client | Any", jit_timeout: float = 10.0) -> None:
        self.bot = bot
        self.jit_timeout = jit_timeout
        # Private storage: handle_id -> (guild_id, track_id, playable)
        self._prepared_handles: dict[UUID, tuple[int, UUID, wavelink.Playable]] = {}

    async def is_available(self) -> bool:
        """Memeriksa apakah node Wavelink aktif dan terhubung."""
        try:
            node = wavelink.Pool.get_node()
            return node is not None and node.status == wavelink.NodeStatus.CONNECTED
        except Exception:
            return False

    async def prepare_reference(
        self,
        guild_id: int,
        track: TrackReference,
    ) -> PreparedPlayback:
        """Melakukan JIT network load di luar lock dan menghasilkan PreparedPlayback."""
        # Hierarchy locator: source_uri -> canonical_url -> search_hint
        target_locator = track.source_uri or track.canonical_url
        if not target_locator:
            target_locator = f"ytmsearch:{track.search_hint}"

        try:
            async with asyncio.timeout(self.jit_timeout):
                results: Any = await wavelink.Playable.search(target_locator)

                if isinstance(results, wavelink.Playlist):
                    raise PlaylistImportDeferred()

                if not results and target_locator.startswith("ytmsearch:"):
                    # Fallback ke ytsearch jika ytmsearch gagal
                    results = await wavelink.Playable.search(f"ytsearch:{track.search_hint}")
                    if isinstance(results, wavelink.Playlist):
                        raise PlaylistImportDeferred()

                if not results:
                    raise TrackNotFound("Track tidak ditemukan saat JIT prepare.")

                playable = results[0] if isinstance(results, list) else results
                if not isinstance(playable, wavelink.Playable):
                    raise TrackNotFound("Hasil JIT prepare bukan wavelink.Playable yang valid.")

                handle_id = uuid.uuid4()
                self._prepared_handles[handle_id] = (guild_id, track.id, playable)
                return PreparedPlayback(handle_id=handle_id, track_id=track.id)

        except TimeoutError as err:
            raise SourceTimeout() from err
        except (TrackNotFound, PlaylistImportDeferred):
            raise
        except Exception as err:
            logger.warning(
                "Gagal melakukan JIT prepare_reference",
                extra={
                    "guild_id": guild_id,
                    "track_id": str(track.id),
                    "error_type": type(err).__name__,
                },
            )
            raise SourceLoadFailed("Gagal menyiapkan audio track dari penyedia.") from err

    async def play_prepared(
        self,
        guild_id: int,
        prepared: PreparedPlayback,
        entry_id: UUID,
        generation: int,
        volume: int = 70,
    ) -> PlaybackSnapshot:
        """Mengeksekusi physical play di node Lavalink."""
        stored = self._prepared_handles.pop(prepared.handle_id, None)
        if stored is None:
            raise PlaybackFailed("Handle prepared playback tidak ditemukan atau sudah kadaluarsa.")

        stored_guild_id, stored_track_id, playable = stored
        if stored_guild_id != guild_id or stored_track_id != prepared.track_id:
            raise PlaybackFailed(
                "Handle prepared playback tidak cocok dengan guild atau track target."
            )

        player = get_wavelink_player(self.bot, guild_id)
        if player is None or not player.connected:
            raise VoiceConnectionFailed("Bot tidak terhubung ke voice channel saat memutar audio.")

        try:
            # 1. Nonaktifkan autoplay Wavelink internal
            player.autoplay = wavelink.AutoPlayMode.disabled

            # 2. Sematkan metadata identitas event pada extras
            playable.extras = {
                "entry_id": str(entry_id),
                "generation": generation,
                "guild_id": guild_id,
            }

            # 3. Eksekusi physical play dengan replace=True, add_history=False, populate=False
            await player.play(
                playable,
                replace=True,
                volume=volume,
                add_history=False,
                populate=False,
            )

            # Wavelink 3.5.2 workaround: volume 0 (falsy) requires explicit set_volume
            if volume == 0:
                await player.set_volume(0)

            position_ms = int(player.position) if player.position is not None else None
            return PlaybackSnapshot(
                guild_id=guild_id,
                connected=player.connected,
                is_playing=player.playing,
                is_paused=player.paused,
                position_ms=position_ms,
                active_entry_id=entry_id,
                active_generation=generation,
            )
        except Exception as err:
            logger.error(
                "Kegagalan saat mengeksekusi player.play fisik",
                extra={
                    "guild_id": guild_id,
                    "entry_id": str(entry_id),
                    "generation": generation,
                    "error_type": type(err).__name__,
                },
            )
            raise PlaybackFailed("Gagal memulai pemutaran fisik pada node audio.") from err

    async def discard_prepared(self, prepared: PreparedPlayback | None) -> None:
        """Membersihkan handle prepared yang tidak terpakai atau stale (idempotent)."""
        if prepared is not None and hasattr(prepared, "handle_id"):
            self._prepared_handles.pop(prepared.handle_id, None)

    async def pause(self, guild_id: int, pause: bool) -> PlaybackSnapshot:
        """Menjeda atau melanjutkan pemutaran fisik."""
        player = get_wavelink_player(self.bot, guild_id)
        if player is None or not player.connected:
            raise VoiceConnectionFailed("Bot tidak terhubung ke voice channel.")

        await player.pause(pause)
        snapshot = await self.get_snapshot(guild_id)
        if snapshot is None:
            raise PlaybackFailed("Gagal membaca snapshot setelah pause.")
        return snapshot

    async def stop_current(self, guild_id: int) -> None:
        """Memaksa penghentian fisik lagu saat ini."""
        player = get_wavelink_player(self.bot, guild_id)
        if player is not None and player.connected:
            await player.skip(force=True)

    async def get_snapshot(self, guild_id: int) -> PlaybackSnapshot | None:
        """Mengambil snapshot status pemutaran fisik saat ini."""
        player = get_wavelink_player(self.bot, guild_id)
        if player is None:
            return None

        from iwed_bot.infrastructure.playback.metadata import parse_track_metadata

        _gid, active_entry_id, active_generation = parse_track_metadata(
            player.current, fallback_guild_id=guild_id
        )

        position_ms = int(player.position) if player.position is not None else None
        return PlaybackSnapshot(
            guild_id=guild_id,
            connected=player.connected,
            is_playing=player.playing,
            is_paused=player.paused,
            position_ms=position_ms,
            active_entry_id=active_entry_id,
            active_generation=active_generation,
        )

    def discard_all(self) -> None:
        """Membersihkan seluruh mapping prepared handles saat shutdown."""
        self._prepared_handles.clear()

    async def shutdown(self) -> None:
        """Graceful shutdown gateway untuk membersihkan semua handles."""
        self.discard_all()
