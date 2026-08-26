"""Application service untuk kontrol antrean, skip, jeda/lanjut, dan status playback."""

import logging
import math
from typing import TYPE_CHECKING

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.errors import (
    AlreadyPaused,
    DifferentVoiceChannel,
    NothingPlaying,
    NotPaused,
    PlaybackReconciliationFailed,
    QueuePageOutOfRange,
)
from iwed_bot.domain.errors import VersionConflict
from iwed_bot.domain.models import (
    PlaybackState,
    QueueEntry,
    SessionStateUpdate,
    VersionedGuildSession,
)
from iwed_bot.domain.transitions import compute_manual_skip_transition
from iwed_bot.ports.playback import PlaybackGateway, PlaybackSnapshot
from iwed_bot.ports.repositories import QueueRepository

if TYPE_CHECKING:
    from iwed_bot.application.playback_coordinator import PlaybackCoordinator

logger = logging.getLogger(__name__)


class QueueControlService:
    """Service yang mengorkestrasi operasi /skip, /pause, /resume, /queue, dan /nowplaying."""

    def __init__(
        self,
        queue_repository: QueueRepository,
        playback_gateway: PlaybackGateway,
        coordinator: "PlaybackCoordinator",
        operation_locks: GuildOperationLockRegistry | None = None,
    ) -> None:
        self._repository = queue_repository
        self._gateway = playback_gateway
        self._coordinator = coordinator
        self._operation_locks = operation_locks or GuildOperationLockRegistry()

    async def skip(
        self, guild_id: int, count: int = 1, requester_channel_id: int | None = None
    ) -> tuple[int, QueueEntry | None]:
        """Melakukan manual skip sebanyak count lagu dengan jaminan stop fisik at-most-once."""
        if count < 1:
            raise ValueError(f"Jumlah skip harus minimal 1, diberikan: {count}.")

        trans = None
        skipped_count = 0
        new_session: VersionedGuildSession | None = None

        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            if (
                requester_channel_id is not None
                and session.voice_channel_id is not None
                and session.voice_channel_id != requester_channel_id
            ):
                raise DifferentVoiceChannel(
                    "Anda harus berada di voice channel yang sama dengan bot."
                )

            if session.current_entry is None or session.state == PlaybackState.IDLE:
                raise NothingPlaying("Tidak ada lagu yang sedang diputar untuk dilewati.")

            trans = compute_manual_skip_transition(session, count=count)
            skipped_count = 1 + min(count - 1, len(session.upcoming))

            # 1. Panggil physical stop tepat satu kali
            await self._gateway.stop_current(guild_id)

            # 2. Commit transisi domain dengan repository-only retry jika terjadi VersionConflict
            target_version = session.version
            committed = False
            for attempt in range(3):
                try:
                    new_session = await self._repository.apply_playback_transition(
                        guild_id, trans, expected_version=target_version
                    )
                    committed = True
                    break
                except VersionConflict as err:
                    # Jangan panggil physical stop lagi; re-read session dan rekonsiliasi
                    current_session = await self._repository.get_session(guild_id)
                    # Jika skip sudah diterapkan oleh aktor lain
                    if current_session.generation > session.generation:
                        new_session = current_session
                        committed = True
                        break

                    # Jika target dan generation masih sama (misal ada concurrent append)
                    if (
                        current_session.generation == session.generation
                        and current_session.current_entry == session.current_entry
                    ):
                        trans = compute_manual_skip_transition(current_session, count=count)
                        target_version = current_session.version
                        if attempt == 2:
                            raise PlaybackReconciliationFailed(
                                "Gagal merekonsiliasi status antrean setelah operasi skip."
                            ) from err
                        continue

                    if attempt == 2:
                        snapshot = await self._gateway.get_snapshot(guild_id)
                        logger.error(
                            "Gagal merekonsiliasi domain setelah physical stop",
                            extra={
                                "guild_id": guild_id,
                                "snapshot_playing": snapshot.is_playing if snapshot else None,
                            },
                        )
                        raise PlaybackReconciliationFailed(
                            "Gagal merekonsiliasi status antrean setelah operasi skip."
                        ) from err

            if not committed or new_session is None:
                raise PlaybackReconciliationFailed(
                    "Gagal merekonsiliasi status antrean setelah operasi skip."
                )

        # 3. Panggil coordinator untuk target berikutnya jika ada (di luar lock)
        if (
            new_session is not None
            and new_session.current_entry is not None
            and new_session.state == PlaybackState.PLAYING
        ):
            await self._coordinator.ensure_running(
                guild_id,
                expected_entry_id=new_session.current_entry.id,
                expected_generation=new_session.generation,
            )

        return skipped_count, new_session.current_entry if new_session else None

    async def pause(
        self, guild_id: int, requester_channel_id: int | None = None
    ) -> PlaybackSnapshot:
        """Menjeda pemutaran musik."""
        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            if (
                requester_channel_id is not None
                and session.voice_channel_id is not None
                and session.voice_channel_id != requester_channel_id
            ):
                raise DifferentVoiceChannel(
                    "Anda harus berada di voice channel yang sama dengan bot."
                )

            if session.state == PlaybackState.PAUSED:
                snap = await self._gateway.get_snapshot(guild_id)
                if snap is not None:
                    return snap
                raise AlreadyPaused()

            if session.state != PlaybackState.PLAYING:
                raise NothingPlaying("Tidak ada musik yang sedang diputar untuk dijeda.")

            # Physical call at-most-once
            snap = await self._gateway.pause(guild_id, True)

            # Update repository state dengan retry repository-only
            target_version = session.version
            committed = False
            for attempt in range(3):
                try:
                    await self._repository.update_session_state(
                        guild_id,
                        SessionStateUpdate(state=PlaybackState.PAUSED),
                        expected_version=target_version,
                    )
                    committed = True
                    break
                except VersionConflict as err:
                    cur = await self._repository.get_session(guild_id)
                    if (
                        cur.current_entry != session.current_entry
                        or cur.generation != session.generation
                    ):
                        raise PlaybackReconciliationFailed(
                            "Target track telah berubah saat rekonsiliasi pause."
                        ) from err
                    if cur.state == PlaybackState.PAUSED:
                        committed = True
                        break
                    target_version = cur.version
                    if attempt == 2:
                        raise PlaybackReconciliationFailed(
                            "Gagal merekonsiliasi status domain PAUSED setelah 3 percobaan."
                        ) from err

            if not committed:
                raise PlaybackReconciliationFailed("Gagal merekonsiliasi status domain PAUSED.")
            return snap

    async def resume(
        self, guild_id: int, requester_channel_id: int | None = None
    ) -> PlaybackSnapshot:
        """Melanjutkan pemutaran musik yang dijeda."""
        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            if (
                requester_channel_id is not None
                and session.voice_channel_id is not None
                and session.voice_channel_id != requester_channel_id
            ):
                raise DifferentVoiceChannel(
                    "Anda harus berada di voice channel yang sama dengan bot."
                )

            if session.state == PlaybackState.PLAYING:
                snap = await self._gateway.get_snapshot(guild_id)
                if snap is not None:
                    return snap
                raise NotPaused()

            if session.state != PlaybackState.PAUSED:
                raise NotPaused("Pemutaran musik tidak dalam keadaan dijeda.")

            # Physical call at-most-once
            snap = await self._gateway.pause(guild_id, False)

            # Update repository state dengan retry repository-only
            target_version = session.version
            committed = False
            for attempt in range(3):
                try:
                    await self._repository.update_session_state(
                        guild_id,
                        SessionStateUpdate(state=PlaybackState.PLAYING),
                        expected_version=target_version,
                    )
                    committed = True
                    break
                except VersionConflict as err:
                    cur = await self._repository.get_session(guild_id)
                    if (
                        cur.current_entry != session.current_entry
                        or cur.generation != session.generation
                    ):
                        raise PlaybackReconciliationFailed(
                            "Target track telah berubah saat rekonsiliasi resume."
                        ) from err
                    if cur.state == PlaybackState.PLAYING:
                        committed = True
                        break
                    target_version = cur.version
                    if attempt == 2:
                        raise PlaybackReconciliationFailed(
                            "Gagal merekonsiliasi status domain PLAYING setelah 3 percobaan."
                        ) from err

            if not committed:
                raise PlaybackReconciliationFailed("Gagal merekonsiliasi status domain PLAYING.")
            return snap

    async def get_queue_page(
        self, guild_id: int, page: int = 1, per_page: int = 10
    ) -> tuple[QueueEntry | None, tuple[QueueEntry, ...], int, int, int, int, int]:
        """Mengambil snapshot halaman antrean (1-based index).

        Returns:
            Tuple (current_entry, page_items, page, total_pages,
                   total_tracks, total_duration_ms, stream_count)
        """
        if page < 1:
            raise QueuePageOutOfRange(max_page=1)

        session = await self._repository.get_session(guild_id)
        upcoming = session.upcoming
        total_tracks = len(upcoming)

        if total_tracks == 0:
            if page > 1:
                raise QueuePageOutOfRange(max_page=1)
            total_duration_ms = 0
            stream_count = 0
            if session.current_entry and session.current_entry.track:
                if session.current_entry.track.duration_ms:
                    total_duration_ms += session.current_entry.track.duration_ms
                if session.current_entry.track.is_stream:
                    stream_count += 1
            return session.current_entry, (), 1, 1, 0, total_duration_ms, stream_count

        total_pages = max(1, math.ceil(total_tracks / per_page))
        if page > total_pages:
            raise QueuePageOutOfRange(max_page=total_pages)

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_items = upcoming[start_idx:end_idx]

        total_duration_ms = 0
        stream_count = 0
        if session.current_entry and session.current_entry.track:
            if session.current_entry.track.duration_ms:
                total_duration_ms += session.current_entry.track.duration_ms
            if session.current_entry.track.is_stream:
                stream_count += 1

        for entry in upcoming:
            if entry.track.duration_ms:
                total_duration_ms += entry.track.duration_ms
            if entry.track.is_stream:
                stream_count += 1

        return (
            session.current_entry,
            page_items,
            page,
            total_pages,
            total_tracks,
            total_duration_ms,
            stream_count,
        )

    async def get_now_playing(
        self, guild_id: int
    ) -> tuple[VersionedGuildSession, PlaybackSnapshot | None]:
        """Mengambil snapshot sesi dan status fisik untuk /nowplaying."""
        session = await self._repository.get_session(guild_id)
        snapshot = await self._gateway.get_snapshot(guild_id)
        return session, snapshot
