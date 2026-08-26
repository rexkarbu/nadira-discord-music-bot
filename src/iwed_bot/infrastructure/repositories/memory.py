"""Implementasi in-memory QueueRepository untuk Iwed Discord Music Bot.

Menyediakan penyimpanan session state dan antrean musik berbasis memori lokal
dengan dukungan locking per-guild dan optimistic concurrency control.
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from iwed_bot.domain.errors import (
    DuplicateQueueEntry,
    GuildMismatch,
    InvalidStateTransition,
    InvalidVolume,
    QueueFull,
    QueuePositionOutOfRange,
    VersionConflict,
)
from iwed_bot.domain.models import (
    UNSET,
    LoopMode,
    PlaybackState,
    PlaybackTransition,
    QueueEntry,
    SessionStateUpdate,
    UnsetType,
    VersionedGuildSession,
)
from iwed_bot.domain.transitions import validate_state_transition
from iwed_bot.infrastructure.concurrency import GuildLockRegistry
from iwed_bot.ports.repositories import QueueRepository


class InMemoryQueueRepository(QueueRepository):
    """Repository antrean dan sesi in-memory yang thread/async-safe per guild."""

    def __init__(
        self,
        max_queue_tracks: int = 1000,
        default_volume: int = 70,
        lock_registry: GuildLockRegistry | None = None,
    ) -> None:
        if type(max_queue_tracks) is not int or max_queue_tracks <= 0:
            raise ValueError("max_queue_tracks harus integer positif (> 0).")
        if type(default_volume) is not int or not (0 <= default_volume <= 100):
            raise ValueError("default_volume harus integer antara 0 dan 100.")
        self._max_queue_tracks = max_queue_tracks
        self._default_volume = default_volume
        self._lock_registry = lock_registry or GuildLockRegistry()
        self._sessions: dict[int, VersionedGuildSession] = {}

    async def get_session(self, guild_id: int) -> VersionedGuildSession:
        """Mengembalikan snapshot sesi guild saat ini (atau default jika belum ada)."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._sessions.get(guild_id)
            if session is None:
                session = VersionedGuildSession(
                    guild_id=guild_id,
                    version=0,
                    volume=self._default_volume,
                    state=PlaybackState.DISCONNECTED,
                )
                self._sessions[guild_id] = session
            return session

    async def append(
        self, guild_id: int, entries: Sequence[QueueEntry], expected_version: int
    ) -> VersionedGuildSession:
        """Menambahkan entri ke antrean upcoming secara atomik."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if not entries:
                return session

            # Cek duplicate ID di dalam batch itu sendiri
            batch_ids = [entry.id for entry in entries]
            if len(batch_ids) != len(set(batch_ids)):
                msg = "Terdapat QueueEntry.id duplikat di dalam batch append."
                raise DuplicateQueueEntry(msg)

            # Cek duplicate ID dengan antrean/current yang sudah ada di session
            existing_ids = {e.id for e in session.upcoming}
            if session.current_entry is not None:
                existing_ids.add(session.current_entry.id)

            for entry in entries:
                if entry.guild_id != guild_id:
                    msg = (
                        f"QueueEntry {entry.id} memiliki guild_id {entry.guild_id}, "
                        f"tidak cocok dengan target guild {guild_id}."
                    )
                    raise GuildMismatch(msg)
                if entry.id in existing_ids:
                    msg = (
                        f"QueueEntry dengan ID {entry.id} sudah ada dalam antrean "
                        f"sesi guild {guild_id}."
                    )
                    raise DuplicateQueueEntry(msg)

            if len(session.upcoming) + len(entries) > self._max_queue_tracks:
                msg = (
                    f"Antrean melebihi batas kapasitas {self._max_queue_tracks}: "
                    f"saat ini {len(session.upcoming)}, mencoba menambah {len(entries)}."
                )
                raise QueueFull(msg)

            new_upcoming = session.upcoming + tuple(entries)
            new_session = replace(
                session,
                upcoming=new_upcoming,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def claim_next(
        self, guild_id: int, expected_version: int
    ) -> tuple[QueueEntry | None, VersionedGuildSession]:
        """Mengklaim lagu berikutnya dari antrean upcoming untuk dijadikan current_entry."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            # --- Strict Preconditions Enforcement ---
            if session.current_entry is not None:
                msg = (
                    "claim_next ditolak: current_entry masih ada. "
                    "claim_next tidak boleh menimpa current track yang masih aktif."
                )
                raise InvalidStateTransition(msg)

            if session.state != PlaybackState.IDLE:
                msg = (
                    f"claim_next ditolak: session.state harus IDLE, "
                    f"status saat ini '{session.state.value}'."
                )
                raise InvalidStateTransition(msg)

            if session.voice_channel_id is None:
                msg = "claim_next ditolak: voice_channel_id bernilai None."
                raise InvalidStateTransition(msg)

            # Jika antrean kosong -> no-op tanpa menaikkan version
            if not session.upcoming:
                return None, session

            claimed_entry = session.upcoming[0]
            new_upcoming = session.upcoming[1:]
            new_session = replace(
                session,
                current_entry=claimed_entry,
                upcoming=new_upcoming,
                state=PlaybackState.PLAYING,
                generation=session.generation + 1,
                version=session.version + 1,
                idle_deadline=None,
            )
            self._sessions[guild_id] = new_session
            return claimed_entry, new_session

    async def remove(
        self, guild_id: int, position: int, expected_version: int
    ) -> tuple[QueueEntry, VersionedGuildSession]:
        """Menghapus entri pada posisi 1-based index (1..N)."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if position < 1 or position > len(session.upcoming):
                msg = (
                    f"Posisi {position} berada di luar rentang upcoming queue "
                    f"(1..{len(session.upcoming)})."
                )
                raise QueuePositionOutOfRange(msg)

            idx = position - 1
            removed_entry = session.upcoming[idx]
            new_upcoming = session.upcoming[:idx] + session.upcoming[idx + 1 :]
            new_session = replace(
                session,
                upcoming=new_upcoming,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return removed_entry, new_session

    async def move(
        self, guild_id: int, from_position: int, to_position: int, expected_version: int
    ) -> VersionedGuildSession:
        """Memindahkan entri antrean dari from_position ke to_position (1-based index)."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            upcoming_len = len(session.upcoming)
            if from_position < 1 or from_position > upcoming_len:
                msg = (
                    f"from_position {from_position} di luar rentang upcoming queue "
                    f"(1..{upcoming_len})."
                )
                raise QueuePositionOutOfRange(msg)

            if to_position < 1 or to_position > upcoming_len:
                msg = (
                    f"to_position {to_position} di luar rentang upcoming queue (1..{upcoming_len})."
                )
                raise QueuePositionOutOfRange(msg)

            if from_position == to_position:
                return session

            items = list(session.upcoming)
            entry = items.pop(from_position - 1)
            items.insert(to_position - 1, entry)

            new_session = replace(
                session,
                upcoming=tuple(items),
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def clear(self, guild_id: int, expected_version: int) -> VersionedGuildSession:
        """Mengosongkan seluruh antrean upcoming."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if not session.upcoming:
                return session

            new_session = replace(
                session,
                upcoming=(),
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def set_loop_mode(
        self, guild_id: int, mode: LoopMode, expected_version: int
    ) -> VersionedGuildSession:
        """Mengubah loop mode sesi."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if session.loop_mode == mode:
                return session

            new_session = replace(
                session,
                loop_mode=mode,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def set_volume(
        self, guild_id: int, volume: int, expected_version: int
    ) -> VersionedGuildSession:
        """Mengubah volume audio sesi (0..100)."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if type(volume) is not int or not (0 <= volume <= 100):
                msg = f"Volume {volume} berada di luar rentang sah (0 - 100)."
                raise InvalidVolume(msg)

            if session.volume == volume:
                return session

            new_session = replace(
                session,
                volume=volume,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def update_session_state(
        self, guild_id: int, update: SessionStateUpdate, expected_version: int
    ) -> VersionedGuildSession:
        """Memperbarui metadata sesi secara eksplisit."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            # Implementation guard: update_session_state tidak boleh menetapkan QueueEntry baru
            if update.current_entry is not UNSET and update.current_entry is not None:
                msg = (
                    "update_session_state tidak boleh menetapkan current_entry ke QueueEntry baru. "
                    "Gunakan claim_next untuk inisiasi lagu baru atau apply_playback_transition "
                    "untuk perpindahan track."
                )
                raise InvalidStateTransition(msg)

            target_state: PlaybackState = (
                session.state if isinstance(update.state, UnsetType) else update.state
            )
            target_voice: int | None = (
                session.voice_channel_id
                if isinstance(update.voice_channel_id, UnsetType)
                else update.voice_channel_id
            )
            target_text: int | None = (
                session.text_channel_id
                if isinstance(update.text_channel_id, UnsetType)
                else update.text_channel_id
            )
            target_current: QueueEntry | None = (
                session.current_entry
                if isinstance(update.current_entry, UnsetType)
                else update.current_entry
            )
            target_idle_deadline: datetime | None = (
                session.idle_deadline
                if isinstance(update.idle_deadline, UnsetType)
                else update.idle_deadline
            )

            # Validasi Channel IDs
            if target_voice is not None and (type(target_voice) is not int or target_voice <= 0):
                raise ValueError("voice_channel_id harus integer positif (> 0).")
            if target_text is not None and (type(target_text) is not int or target_text <= 0):
                raise ValueError("text_channel_id harus integer positif (> 0).")

            # State and current_entry consistency
            if (
                target_state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
                and target_current is None
            ):
                msg = f"Status '{target_state.value}' wajib memiliki current_entry."
                raise InvalidStateTransition(msg)
            if (
                target_state
                in (PlaybackState.IDLE, PlaybackState.DISCONNECTED, PlaybackState.CONNECTING)
                and target_current is not None
            ):
                msg = f"Status '{target_state.value}' wajib memiliki current_entry bernilai None."
                raise InvalidStateTransition(msg)

            # Validasi Transisi State jika state berubah
            if target_state != session.state:
                validate_state_transition(
                    session.state, target_state, allow_same_state_playing=False
                )

            # No-Op Check: jika seluruh target value identik dengan current state
            if (
                target_state == session.state
                and target_voice == session.voice_channel_id
                and target_text == session.text_channel_id
                and target_current == session.current_entry
                and target_idle_deadline == session.idle_deadline
            ):
                return session

            new_session = replace(
                session,
                state=target_state,
                voice_channel_id=target_voice,
                text_channel_id=target_text,
                current_entry=target_current,
                idle_deadline=target_idle_deadline,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    async def apply_playback_transition(
        self, guild_id: int, transition: PlaybackTransition, expected_version: int
    ) -> VersionedGuildSession:
        """Menerapkan PlaybackTransition secara atomik dalam satu mutasi."""
        async with self._lock_registry.get_lock(guild_id):
            session = self._get_or_create_session_unlocked(guild_id)

            if expected_version != session.version:
                msg = (
                    f"Version conflict untuk guild {guild_id}: "
                    f"expected={expected_version}, active={session.version}."
                )
                raise VersionConflict(msg)

            if (
                transition.next_current_entry is not None
                and transition.next_current_entry.guild_id != guild_id
            ):
                entry_guild = transition.next_current_entry.guild_id
                msg = (
                    f"next_current_entry memiliki guild_id {entry_guild}, "
                    f"tidak cocok dengan target guild {guild_id}."
                )
                raise GuildMismatch(msg)

            for entry in transition.next_upcoming:
                if entry.guild_id != guild_id:
                    msg = (
                        f"next_upcoming entry {entry.id} memiliki guild_id {entry.guild_id}, "
                        f"tidak cocok dengan target guild {guild_id}."
                    )
                    raise GuildMismatch(msg)

            # Uniqueness check on next_upcoming and next_current_entry
            next_upcoming_ids = [e.id for e in transition.next_upcoming]
            if len(next_upcoming_ids) != len(set(next_upcoming_ids)):
                msg = "Terdapat QueueEntry.id duplikat dalam next_upcoming."
                raise DuplicateQueueEntry(msg)
            if (
                transition.next_current_entry is not None
                and transition.next_current_entry.id in set(next_upcoming_ids)
            ):
                msg = "next_current_entry.id muncul juga di dalam next_upcoming."
                raise DuplicateQueueEntry(msg)

            # State and current_entry consistency
            if (
                transition.next_state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
                and transition.next_current_entry is None
            ):
                msg = f"Status '{transition.next_state.value}' wajib memiliki current_entry."
                raise InvalidStateTransition(msg)
            if (
                transition.next_state
                in (PlaybackState.IDLE, PlaybackState.DISCONNECTED, PlaybackState.CONNECTING)
                and transition.next_current_entry is not None
            ):
                msg = (
                    f"Status '{transition.next_state.value}' wajib memiliki "
                    "current_entry bernilai None."
                )
                raise InvalidStateTransition(msg)

            if len(transition.next_upcoming) > self._max_queue_tracks:
                msg = (
                    f"Antrean melebihi kapasitas {self._max_queue_tracks} "
                    f"setelah transisi: {len(transition.next_upcoming)}."
                )
                raise QueueFull(msg)

            if transition.next_state != session.state:
                validate_state_transition(
                    session.state,
                    transition.next_state,
                    allow_same_state_playing=True,
                )

            # No-Op Check: jika tidak ada perubahan sama sekali dan generation tidak bertambah
            if (
                transition.next_current_entry == session.current_entry
                and transition.next_upcoming == session.upcoming
                and transition.next_state == session.state
                and not transition.increment_generation
            ):
                return session

            next_gen = (
                session.generation + 1 if transition.increment_generation else session.generation
            )
            new_idle = (
                None if transition.next_state == PlaybackState.PLAYING else session.idle_deadline
            )

            new_session = replace(
                session,
                current_entry=transition.next_current_entry,
                upcoming=transition.next_upcoming,
                state=transition.next_state,
                generation=next_gen,
                idle_deadline=new_idle,
                version=session.version + 1,
            )
            self._sessions[guild_id] = new_session
            return new_session

    def _get_or_create_session_unlocked(self, guild_id: int) -> VersionedGuildSession:
        session = self._sessions.get(guild_id)
        if session is None:
            session = VersionedGuildSession(
                guild_id=guild_id,
                version=0,
                volume=self._default_volume,
                state=PlaybackState.DISCONNECTED,
            )
            self._sessions[guild_id] = session
        return session
