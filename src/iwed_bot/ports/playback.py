"""Port interfaces dan DTO untuk gateway playback audio Lavalink."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from iwed_bot.domain.models import TrackReference


@dataclass(frozen=True, slots=True)
class PreparedPlayback:
    """Opaque handle hasil resolusi JIT audio di infrastructure."""

    handle_id: UUID
    track_id: UUID


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    """Snapshot status pemutaran fisik Lavalink."""

    guild_id: int
    connected: bool
    is_playing: bool
    is_paused: bool
    position_ms: int | None
    active_entry_id: UUID | None
    active_generation: int | None


class PlaybackGateway(Protocol):
    """Kontrak antarmuka kontrol pemutaran audio Lavalink tanpa dependensi vendor."""

    async def is_available(self) -> bool:
        """Memeriksa kesiapan node audio Lavalink."""
        ...

    async def prepare_reference(
        self,
        guild_id: int,
        track: TrackReference,
    ) -> PreparedPlayback:
        """Melakukan JIT network load di luar lock dan menghasilkan PreparedPlayback.

        Args:
            guild_id: ID guild Discord target.
            track: TrackReference yang akan disiapkan untuk pemutaran.

        Returns:
            PreparedPlayback berisi handle ID opaque.

        Raises:
            SourceTimeout: Jika batas waktu JIT load terlampaui.
            SourceLoadFailed: Jika terjadi kesalahan saat memuat playable.
        """
        ...

    async def play_prepared(
        self,
        guild_id: int,
        prepared: PreparedPlayback,
        entry_id: UUID,
        generation: int,
        volume: int = 70,
    ) -> PlaybackSnapshot:
        """Mengeksekusi physical play di node Lavalink dalam serialized section pendek.

        Args:
            guild_id: ID guild Discord target.
            prepared: Opaque handle yang sudah disiapkan.
            entry_id: UUID QueueEntry yang sedang diputar.
            generation: Token generasi sesi aktif.
            volume: Volume pemutaran (0-100).

        Returns:
            PlaybackSnapshot setelah physical play berhasil dieksekusi.

        Raises:
            PlaybackFailed: Jika terjadi kegagalan memulai audio fisik.
        """
        ...

    async def discard_prepared(self, prepared: PreparedPlayback) -> None:
        """Membersihkan handle prepared yang tidak terpakai atau stale."""
        ...

    async def pause(self, guild_id: int, pause: bool) -> PlaybackSnapshot:
        """Menjeda atau melanjutkan pemutaran fisik.

        Args:
            guild_id: ID guild Discord target.
            pause: True untuk jeda, False untuk lanjut.

        Returns:
            PlaybackSnapshot status terbaru.
        """
        ...

    async def stop_current(self, guild_id: int) -> None:
        """Memaksa penghentian/skip fisik lagu saat ini (player.skip(force=True))."""
        ...

    async def get_snapshot(self, guild_id: int) -> PlaybackSnapshot | None:
        """Mengambil snapshot status pemutaran fisik saat ini."""
        ...

    async def shutdown(self) -> None:
        """Membersihkan seluruh handle dan resource gateway saat bot berhenti."""
        ...
