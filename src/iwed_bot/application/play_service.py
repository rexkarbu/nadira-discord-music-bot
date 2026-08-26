"""Application service untuk orkestrasi perintah /play dan penambahan lagu ke antrean."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.errors import (
    DifferentVoiceChannel,
    EntrySuperseded,
    InvalidPlayQuery,
    PlaybackFailed,
    PlaylistImportDeferred,
    SpotifySourceDeferred,
    UnsupportedSource,
)
from iwed_bot.application.playback_coordinator import (
    PlaybackCoordinator,
    RunnerOutcome,
)
from iwed_bot.application.source_router import SourceRouter
from iwed_bot.domain.models import PlaybackState, QueueEntry, TrackReference
from iwed_bot.ports.playback import PlaybackSnapshot
from iwed_bot.ports.repositories import QueueRepository
from iwed_bot.ports.sources import SourceClassification, TrackSource

if TYPE_CHECKING:
    from iwed_bot.application.voice import VoiceSessionService

logger = logging.getLogger(__name__)


class PlayRequestService:
    """Service yang mengorkestrasi alur pencarian, validasi, enqueue, dan pemicu playback."""

    def __init__(
        self,
        track_source: TrackSource,
        queue_repository: QueueRepository,
        voice_service: "VoiceSessionService",
        coordinator: PlaybackCoordinator,
        operation_locks: GuildOperationLockRegistry | None = None,
    ) -> None:
        self._source = track_source
        self._repository = queue_repository
        self._voice_service = voice_service
        self._coordinator = coordinator
        self._operation_locks = operation_locks or GuildOperationLockRegistry()

    async def resolve_input(self, raw_query: str) -> tuple[SourceClassification, TrackReference]:
        """Me-resolve query pencarian atau URL menjadi TrackReference tanpa dependensi Discord."""
        classification = SourceRouter.classify(raw_query)

        match classification.kind:
            case SourceClassification.SEARCH_TEXT:
                candidates = await self._source.search(classification.normalized_query, limit=5)
                if not candidates:
                    raise InvalidPlayQuery("Tidak ada hasil pencarian yang ditemukan.")
                return SourceClassification.SEARCH_TEXT, candidates[0]

            case SourceClassification.YOUTUBE_SINGLE_TRACK:
                url = classification.cleaned_url or classification.normalized_query
                track = await self._source.resolve_single_url(url)
                return SourceClassification.YOUTUBE_SINGLE_TRACK, track

            case SourceClassification.YOUTUBE_PLAYLIST:
                raise PlaylistImportDeferred()

            case (
                SourceClassification.SPOTIFY_TRACK | SourceClassification.SPOTIFY_PLAYLIST_OR_ALBUM
            ):
                raise SpotifySourceDeferred()

            case SourceClassification.UNSUPPORTED_URL:
                raise UnsupportedSource()

    async def enqueue_and_start(
        self,
        guild_id: int,
        track: TrackReference,
        user_id: int,
        channel_id: int,
        text_channel_id: int | None = None,
    ) -> tuple[str, QueueEntry, PlaybackSnapshot | None]:
        """Memastikan koneksi voice, append ke antrean, dan rekonsiliasi hasil."""
        # 1. Voice lifecycle connection di luar operation lock
        _conn, _session, _is_move, _is_noop = await self._voice_service.join(
            guild_id=guild_id,
            channel_id=channel_id,
            text_channel_id=text_channel_id,
        )

        # 2. Buat QueueEntry baru
        entry_id = uuid.uuid4()
        entry = QueueEntry(
            id=entry_id,
            guild_id=guild_id,
            track=track,
            requested_by_user_id=user_id,
            requested_in_channel_id=text_channel_id or channel_id,
            enqueued_at=datetime.now(UTC),
        )

        # 3. Append ke repository di dalam lock singkat dengan validasi state aktif
        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            if session.state in (PlaybackState.DISCONNECTED, PlaybackState.CONNECTING):
                raise EntrySuperseded(
                    "Koneksi voice telah terputus sebelum lagu dapat dimasukkan ke antrean."
                )
            if session.voice_channel_id is None or session.voice_channel_id != channel_id:
                raise DifferentVoiceChannel(
                    "Bot berada di voice channel yang berbeda atau tidak terhubung. "
                    "Gunakan /join untuk memindahkan bot."
                )
            await self._repository.append(guild_id, [entry], expected_version=session.version)

        # 4. Panggil one-shot coordinator runner di luar lock
        task = await self._coordinator.ensure_running(guild_id)
        outcome: RunnerOutcome | None = None
        try:
            outcome = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Bedakan apakah caller dibatalkan atau runner internal yang di-supersede
            cur_task = asyncio.current_task()
            if cur_task is not None and cur_task.cancelling() > 0:
                raise
            outcome = None

        # 5. Rekonsiliasi hasil berdasarkan keberadaan aktual enqueued_entry_id di session
        async with self._operation_locks.get_lock(guild_id):
            current_session = await self._repository.get_session(guild_id)

        # 5a. Apakah entry request saat ini berhasil diputar (PLAYING)?
        if (
            current_session.current_entry is not None
            and current_session.current_entry.id == entry.id
            and current_session.state == PlaybackState.PLAYING
        ):
            snap = outcome.snapshot if (outcome and outcome.snapshot) else None
            return "STARTED", entry, snap

        # 5b. Apakah entry request tersimpan di antrean upcoming (termasuk saat safety halt)?
        if any(up.id == entry.id for up in current_session.upcoming):
            snap = outcome.snapshot if outcome else None
            return "QUEUED", entry, snap

        # 5c. Apakah entry mengalami kegagalan langsung saat advance?
        if outcome is not None and entry.id in outcome.failed_entry_ids:
            raise PlaybackFailed("Gagal memulai pemutaran lagu di node audio.")

        # 5d. Entry tidak ada di current/upcoming (di-skip atau di-clear sebelum diputar)
        raise EntrySuperseded("Lagu telah dilewati atau dibatalkan dari antrean.")
