"""Port dan kontrak interface repository untuk Iwed Discord Music Bot.

Modul ini mendefinisikan QueueRepository Protocol sebagai kontrak penyimpanan
sesi guild dan antrean musik yang bersifat async.
"""

from collections.abc import Sequence
from typing import Protocol

from iwed_bot.domain.models import (
    LoopMode,
    PlaybackTransition,
    QueueEntry,
    SessionStateUpdate,
    VersionedGuildSession,
)


class QueueRepository(Protocol):
    """Kontrak abstract untuk repository antrean dan session state per guild."""

    async def get_session(self, guild_id: int) -> VersionedGuildSession:
        """Mengembalikan snapshot sesi guild saat ini.

        Jika belum pernah ada sesi untuk guild_id ini, buat dan kembalikan sesi default baru
        dengan state=PlaybackState.DISCONNECTED, version=0, generation=0.

        Args:
            guild_id: ID unik server/guild Discord.

        Returns:
            VersionedGuildSession snapshot.
        """
        ...

    async def append(
        self, guild_id: int, entries: Sequence[QueueEntry], expected_version: int
    ) -> VersionedGuildSession:
        """Menambahkan satu atau lebih QueueEntry ke akhir antrean upcoming.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Setiap entry wajib memiliki entry.guild_id == guild_id -> raise GuildMismatch.
        - Jika len(entries) == 0 -> no-op, kembalikan snapshot saat ini tanpa menaikkan version.
        - Jika len(upcoming) + len(entries) > max_queue_tracks -> raise QueueFull (tanpa mutasi).
        - Jika mutasi berhasil -> version bertambah +1.

        Args:
            guild_id: ID guild target.
            entries: Daftar QueueEntry yang akan ditambahkan.
            expected_version: Versi sesi yang diharapkan untuk optimistic lock.

        Returns:
            Snapshot VersionedGuildSession baru setelah penambahan.
        """
        ...

    async def claim_next(
        self, guild_id: int, expected_version: int
    ) -> tuple[QueueEntry | None, VersionedGuildSession]:
        """Secara atomik mengklaim item pertama dari antrean upcoming untuk menjadi current_entry.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Wajib memenuhi:
          1. session.state == PlaybackState.IDLE
          2. session.current_entry is None
          3. session.voice_channel_id is not None
          Jika salah satu tidak terpenuhi -> raise InvalidStateTransition.
        - Jika antrean upcoming kosong -> no-op, kembalikan (None, session) tanpa kenaikan version.
        - Jika upcoming tidak kosong:
          - upcoming[0] dipindahkan menjadi current_entry.
          - upcoming = upcoming[1:].
          - state menjadi PlaybackState.PLAYING.
          - generation bertambah +1.
          - version bertambah +1.
          - kembalikan (claimed_entry, new_session).

        Args:
            guild_id: ID guild target.
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Tuple berupa (QueueEntry yang diklaim atau None, snapshot sesi terbaru).
        """
        ...

    async def remove(
        self, guild_id: int, position: int, expected_version: int
    ) -> tuple[QueueEntry, VersionedGuildSession]:
        """Menghapus entri pada posisi 1-based index (1..N) dari antrean upcoming.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Jika position < 1 atau position > len(upcoming) -> raise QueuePositionOutOfRange.
        - Entri pada posisi tersebut dihapus, version bertambah +1.

        Args:
            guild_id: ID guild target.
            position: Posisi lagu dalam antrean (1-based).
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Tuple berupa (QueueEntry yang dihapus, snapshot sesi terbaru).
        """
        ...

    async def move(
        self, guild_id: int, from_position: int, to_position: int, expected_version: int
    ) -> VersionedGuildSession:
        """Memindahkan entri antrean dari from_position ke to_position (1-based index).

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Jika from_position < 1 atau from_position > len(upcoming) -> raise OutOfRange.
        - Jika to_position < 1 atau to_position > len(upcoming) -> raise OutOfRange.
        - Jika from_position == to_position -> no-op, snapshot sama tanpa kenaikan version.
        - Jika posisi berpindah -> item dipindahkan, version bertambah +1.

        Args:
            guild_id: ID guild target.
            from_position: Posisi asal (1-based).
            to_position: Posisi tujuan (1-based).
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru.
        """
        ...

    async def clear(self, guild_id: int, expected_version: int) -> VersionedGuildSession:
        """Mengosongkan seluruh antrean upcoming. current_entry tidak terpengaruh.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Jika upcoming sudah kosong -> no-op, kembalikan snapshot saat ini tanpa menaikkan version.
        - Jika ada item yang dihapus -> version bertambah +1.

        Args:
            guild_id: ID guild target.
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru dengan upcoming kosong.
        """
        ...

    async def set_loop_mode(
        self, guild_id: int, mode: LoopMode, expected_version: int
    ) -> VersionedGuildSession:
        """Mengubah konfigurasi mode perulangan pemutaran.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Jika mode == session.loop_mode -> no-op, version tidak bertambah.
        - Jika mode berbeda -> mode diperbarui, version bertambah +1.

        Args:
            guild_id: ID guild target.
            mode: Mode perulangan baru.
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru.
        """
        ...

    async def set_volume(
        self, guild_id: int, volume: int, expected_version: int
    ) -> VersionedGuildSession:
        """Mengubah level volume pemutaran musik (0 - 100).

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Jika volume < 0 atau volume > 100 -> raise InvalidVolume.
        - Jika volume == session.volume -> no-op, version tidak bertambah.
        - Jika volume berbeda -> volume diperbarui, version bertambah +1.

        Args:
            guild_id: ID guild target.
            volume: Level volume (0 - 100).
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru.
        """
        ...

    async def update_session_state(
        self, guild_id: int, update: SessionStateUpdate, expected_version: int
    ) -> VersionedGuildSession:
        """Memperbarui metadata sesi secara eksplisit.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - Implementation guard: update.current_entry TIDAK BOLEH berupa QueueEntry baru
          (hanya boleh UNSET atau None). Menetapkan QueueEntry baru -> InvalidStateTransition.
        - Jika state diperbarui (bukan UNSET dan berbeda):
          - Harus mematuhi ALLOWED_STATE_TRANSITIONS -> raise InvalidStateTransition.
        - voice_channel_id dan text_channel_id jika bukan UNSET dan bukan None harus > 0.
        - Jika tidak ada satupun field yang mengalami perubahan nyata -> no-op.
        - Jika minimal satu field berubah valid -> version bertambah +1.

        Args:
            guild_id: ID guild target.
            update: Objek mutasi status sesi.
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru.
        """
        ...

    async def apply_playback_transition(
        self, guild_id: int, transition: PlaybackTransition, expected_version: int
    ) -> VersionedGuildSession:
        """Menerapkan PlaybackTransition secara atomik dalam satu mutasi.

        Preconditions & Invariants:
        - Jika expected_version != active_session.version -> raise VersionConflict.
        - next_current_entry.guild_id == guild_id jika tidak None -> raise GuildMismatch.
        - Setiap entry di next_upcoming wajib memiliki guild_id == guild_id -> raise GuildMismatch.
        - len(next_upcoming) <= max_queue_tracks -> raise QueueFull jika melebihi.
        - Transisi state diperiksa: PLAYING -> PLAYING diizinkan untuk loop TRACK/QUEUE.
        - Jika transisi menghasilkan state yang persis sama dan gen tidak bertambah -> no-op.
        - Jika mutasi diterapkan -> current_entry, upcoming, state, generation,
          dan version (+1) diperbarui secara atomik.

        Args:
            guild_id: ID guild target.
            transition: Objek PlaybackTransition target.
            expected_version: Versi sesi yang diharapkan.

        Returns:
            Snapshot VersionedGuildSession terbaru.
        """
        ...
