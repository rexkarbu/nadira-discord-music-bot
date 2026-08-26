"""Base contract test suite untuk QueueRepository.

Test suite ini dirancang untuk dapat dijalankan terhadap sembarang implementasi
QueueRepository (InMemory di Fase 2, Redis di Fase 7).
"""

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import pytest

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
    LoopMode,
    PlaybackState,
    PlaybackTransition,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
)
from iwed_bot.ports.repositories import QueueRepository


def make_track(title: str = "Track", duration_ms: int | None = 180000) -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="abc12345",
        source_uri="https://youtube.com/watch?v=abc12345",
        search_hint=f"Artist - {title}",
        title=title,
        artists=("Artist",),
        duration_ms=duration_ms,
        thumbnail_url=None,
        canonical_url=None,
    )


def make_entry(
    guild_id: int = 100,
    title: str = "Track",
    duration_ms: int | None = 180000,
) -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=make_track(title=title, duration_ms=duration_ms),
        requested_by_user_id=111,
        requested_in_channel_id=222,
        enqueued_at=datetime.now(UTC),
    )


class BaseQueueRepositoryContractTests(ABC):
    """Abstract base test suite yang menegakkan seluruh kontrak dan invariant QueueRepository."""

    @abstractmethod
    async def create_repository(self, max_queue_tracks: int = 1000) -> QueueRepository:
        """Factory method untuk membuat instance repository yang akan diuji."""
        ...

    @pytest.mark.asyncio
    async def test_get_session_creates_default_disconnected_session(self) -> None:
        repo = await self.create_repository()
        session = await repo.get_session(100)

        assert session.guild_id == 100
        assert session.version == 0
        assert session.generation == 0
        assert session.state == PlaybackState.DISCONNECTED
        assert session.current_entry is None
        assert session.upcoming == ()
        assert session.volume == 70
        assert session.loop_mode == LoopMode.OFF
        assert session.is_empty

    @pytest.mark.asyncio
    async def test_append_single_and_batch_entries(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        e2 = make_entry(guild_id=100, title="Song 2")

        s1 = await repo.append(100, [e1], expected_version=0)
        assert s1.version == 1
        assert s1.upcoming == (e1,)

        s2 = await repo.append(100, [e2], expected_version=1)
        assert s2.version == 2
        assert s2.upcoming == (e1, e2)

    @pytest.mark.asyncio
    async def test_append_empty_batch_is_noop_zero_version_increment(self) -> None:
        repo = await self.create_repository()
        s0 = await repo.get_session(100)
        assert s0.version == 0

        s1 = await repo.append(100, [], expected_version=0)
        assert s1.version == 0
        assert s1.upcoming == ()

    @pytest.mark.asyncio
    async def test_append_duplicate_entry_id_in_batch_rejected(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")

        with pytest.raises(DuplicateQueueEntry):
            await repo.append(100, [e1, e1], expected_version=0)

        session = await repo.get_session(100)
        assert session.version == 0
        assert session.upcoming == ()

    @pytest.mark.asyncio
    async def test_append_already_existing_entry_id_rejected(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        await repo.append(100, [e1], expected_version=0)  # version = 1

        with pytest.raises(DuplicateQueueEntry):
            await repo.append(100, [e1], expected_version=1)

        session = await repo.get_session(100)
        assert session.version == 1
        assert session.upcoming == (e1,)

    @pytest.mark.asyncio
    async def test_append_same_track_different_entry_id_accepted(self) -> None:
        repo = await self.create_repository()
        track = make_track(title="Song 1")
        e1 = QueueEntry(
            id=uuid.uuid4(),
            guild_id=100,
            track=track,
            requested_by_user_id=1,
            requested_in_channel_id=1,
            enqueued_at=datetime.now(UTC),
        )
        e2 = QueueEntry(
            id=uuid.uuid4(),
            guild_id=100,
            track=track,
            requested_by_user_id=1,
            requested_in_channel_id=1,
            enqueued_at=datetime.now(UTC),
        )
        s1 = await repo.append(100, [e1, e2], expected_version=0)
        assert s1.version == 1
        assert len(s1.upcoming) == 2

    @pytest.mark.asyncio
    async def test_append_version_conflict(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100)
        with pytest.raises(VersionConflict):
            await repo.append(100, [e1], expected_version=99)

    @pytest.mark.asyncio
    async def test_append_guild_mismatch_rejected(self) -> None:
        repo = await self.create_repository()
        e_wrong = make_entry(guild_id=999)
        with pytest.raises(GuildMismatch):
            await repo.append(100, [e_wrong], expected_version=0)

        # State dan version harus tetap utuh
        session = await repo.get_session(100)
        assert session.version == 0
        assert session.upcoming == ()

    @pytest.mark.asyncio
    async def test_append_capacity_overflow_rejected(self) -> None:
        repo = await self.create_repository(max_queue_tracks=2)
        e1 = make_entry(guild_id=100, title="1")
        e2 = make_entry(guild_id=100, title="2")
        e3 = make_entry(guild_id=100, title="3")

        await repo.append(100, [e1, e2], expected_version=0)

        with pytest.raises(QueueFull):
            await repo.append(100, [e3], expected_version=1)

        # State harus tetap pada 2 lagu, version tetap 1
        session = await repo.get_session(100)
        assert session.version == 1
        assert len(session.upcoming) == 2

    @pytest.mark.asyncio
    async def test_claim_next_strict_preconditions(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        await repo.append(100, [e1], expected_version=0)  # version = 1

        # 1. Gagal jika DISCONNECTED
        with pytest.raises(
            InvalidStateTransition, match="claim_next ditolak: session.state harus IDLE"
        ):
            await repo.claim_next(100, expected_version=1)

        # Hubungkan ke CONNECTING -> IDLE tapi tanpa voice_channel_id
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )  # version = 2
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.IDLE), expected_version=2
        )  # version = 3

        # 2. Gagal jika voice_channel_id is None
        with pytest.raises(InvalidStateTransition, match="voice_channel_id bernilai None"):
            await repo.claim_next(100, expected_version=3)

        # Set voice_channel_id
        await repo.update_session_state(
            100, SessionStateUpdate(voice_channel_id=555444), expected_version=3
        )  # version = 4

        # 3. Sukses mengklaim lagu
        claimed, s5 = await repo.claim_next(100, expected_version=4)
        assert claimed == e1
        assert s5.version == 5
        assert s5.generation == 1
        assert s5.state == PlaybackState.PLAYING
        assert s5.current_entry == e1
        assert s5.upcoming == ()

        # 4. Gagal jika current_entry masih aktif
        with pytest.raises(InvalidStateTransition, match="current_entry masih ada"):
            await repo.claim_next(100, expected_version=5)

    @pytest.mark.asyncio
    async def test_claim_next_empty_queue_is_noop(self) -> None:
        repo = await self.create_repository()
        # Setup IDLE with voice
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=0
        )
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=555),
            expected_version=1,
        )  # version = 2

        claimed, s2 = await repo.claim_next(100, expected_version=2)
        assert claimed is None
        assert s2.version == 2
        assert s2.state == PlaybackState.IDLE
        assert s2.current_entry is None

    @pytest.mark.asyncio
    async def test_remove_entry(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        e2 = make_entry(guild_id=100, title="Song 2")
        e3 = make_entry(guild_id=100, title="Song 3")
        await repo.append(100, [e1, e2, e3], expected_version=0)  # version = 1

        # Out of range
        with pytest.raises(QueuePositionOutOfRange):
            await repo.remove(100, position=0, expected_version=1)
        with pytest.raises(QueuePositionOutOfRange):
            await repo.remove(100, position=4, expected_version=1)

        # Valid remove posisi 2 (Song 2)
        removed, s2 = await repo.remove(100, position=2, expected_version=1)
        assert removed == e2
        assert s2.version == 2
        assert s2.upcoming == (e1, e3)

    @pytest.mark.asyncio
    async def test_move_entry(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        e2 = make_entry(guild_id=100, title="Song 2")
        e3 = make_entry(guild_id=100, title="Song 3")
        await repo.append(100, [e1, e2, e3], expected_version=0)  # version = 1

        # from == to -> no-op
        s1 = await repo.move(100, from_position=2, to_position=2, expected_version=1)
        assert s1.version == 1
        assert s1.upcoming == (e1, e2, e3)

        # Move 1 to 3 (Song 1 moved to end)
        s2 = await repo.move(100, from_position=1, to_position=3, expected_version=1)
        assert s2.version == 2
        assert s2.upcoming == (e2, e3, e1)

    @pytest.mark.asyncio
    async def test_clear_upcoming(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        await repo.append(100, [e1], expected_version=0)  # version = 1

        s2 = await repo.clear(100, expected_version=1)
        assert s2.version == 2
        assert s2.upcoming == ()

        # Clear saat sudah kosong -> no-op
        s3 = await repo.clear(100, expected_version=2)
        assert s3.version == 2

    @pytest.mark.asyncio
    async def test_set_loop_mode(self) -> None:
        repo = await self.create_repository()
        s1 = await repo.set_loop_mode(100, LoopMode.TRACK, expected_version=0)
        assert s1.version == 1
        assert s1.loop_mode == LoopMode.TRACK

        # Mode sama -> no-op
        s2 = await repo.set_loop_mode(100, LoopMode.TRACK, expected_version=1)
        assert s2.version == 1

    @pytest.mark.asyncio
    async def test_set_volume(self) -> None:
        repo = await self.create_repository()
        with pytest.raises(InvalidVolume):
            await repo.set_volume(100, 105, expected_version=0)

        with pytest.raises(InvalidVolume):
            await repo.set_volume(100, -5, expected_version=0)

        s1 = await repo.set_volume(100, 85, expected_version=0)
        assert s1.version == 1
        assert s1.volume == 85

        # Volume sama -> no-op
        s2 = await repo.set_volume(100, 85, expected_version=1)
        assert s2.version == 1

    @pytest.mark.asyncio
    async def test_update_session_state_guard_and_validations(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100)

        # 1. Guard: Dilarang memasang QueueEntry baru via update_session_state
        with pytest.raises(
            InvalidStateTransition, match="tidak boleh menetapkan current_entry ke QueueEntry baru"
        ):
            await repo.update_session_state(
                100, SessionStateUpdate(current_entry=e1), expected_version=0
            )

        # 2. Illegal State Transition (DISCONNECTED -> IDLE)
        with pytest.raises(InvalidStateTransition, match="Transisi status playback tidak sah"):
            await repo.update_session_state(
                100, SessionStateUpdate(state=PlaybackState.IDLE), expected_version=0
            )

        # 3. Invalid Channel ID
        with pytest.raises(ValueError, match="voice_channel_id harus integer positif"):
            await repo.update_session_state(
                100, SessionStateUpdate(voice_channel_id=0), expected_version=0
            )

        # 4. Valid Transition: DISCONNECTED -> CONNECTING
        s1 = await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=0
        )
        assert s1.version == 1
        assert s1.state == PlaybackState.CONNECTING

        # 5. No-Op Update
        s2 = await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        assert s2.version == 1

    @pytest.mark.asyncio
    async def test_state_and_current_entry_consistency_on_update_session_state(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")

        # Inisiasi ke PLAYING
        await repo.append(100, [e1], expected_version=0)  # v=1
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )  # v=2
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )  # v=3
        _, s4 = await repo.claim_next(100, expected_version=3)  # v=4, state=PLAYING, current=e1
        assert s4.version == 4

        # a. Mencoba ubah state ke IDLE tanpa meng-clear current_entry -> ditolak
        with pytest.raises(
            InvalidStateTransition, match="Status 'idle' wajib memiliki current_entry bernilai None"
        ):
            await repo.update_session_state(
                100, SessionStateUpdate(state=PlaybackState.IDLE), expected_version=4
            )

        # b. Valid atomic stop: PLAYING -> IDLE + clear current_entry
        s5 = await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE, current_entry=None),
            expected_version=4,
        )
        assert s5.version == 5
        assert s5.state == PlaybackState.IDLE
        assert s5.current_entry is None

        # c. Valid disconnect: IDLE -> DISCONNECTED + clear voice_channel_id
        s6 = await repo.update_session_state(
            100,
            SessionStateUpdate(
                state=PlaybackState.DISCONNECTED,
                voice_channel_id=None,
                current_entry=None,
            ),
            expected_version=5,
        )
        assert s6.version == 6
        assert s6.state == PlaybackState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_apply_playback_transition(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")
        e2 = make_entry(guild_id=100, title="Song 2")

        # Setup initial state PLAYING via claim_next
        await repo.append(100, [e1, e2], expected_version=0)  # v=1
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )  # v=2
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )  # v=3
        _, s4 = await repo.claim_next(100, expected_version=3)  # v=4, current=e1, upcoming=(e2,)
        assert s4.version == 4
        assert s4.current_entry == e1

        # Transition ke e2
        trans = PlaybackTransition(
            next_current_entry=e2,
            next_upcoming=(),
            next_state=PlaybackState.PLAYING,
            increment_generation=True,
        )
        s5 = await repo.apply_playback_transition(100, trans, expected_version=4)
        assert s5.version == 5
        assert s5.generation == 2
        assert s5.current_entry == e2
        assert s5.upcoming == ()
        assert s5.state == PlaybackState.PLAYING

    @pytest.mark.asyncio
    async def test_apply_playback_transition_cross_guild_rejected(self) -> None:
        repo = await self.create_repository()
        e_cross = make_entry(guild_id=999)
        trans = PlaybackTransition(
            next_current_entry=e_cross,
            next_upcoming=(),
            next_state=PlaybackState.PLAYING,
        )
        with pytest.raises(GuildMismatch):
            await repo.apply_playback_transition(100, trans, expected_version=0)

        session = await repo.get_session(100)
        assert session.version == 0

    @pytest.mark.asyncio
    async def test_apply_playback_transition_consistency_rejection(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100)

        # Transition ke PLAYING tapi current None -> ditolak
        trans_invalid = PlaybackTransition(
            next_current_entry=None,
            next_upcoming=(),
            next_state=PlaybackState.PLAYING,
        )
        with pytest.raises(
            InvalidStateTransition, match="Status 'playing' wajib memiliki current_entry"
        ):
            await repo.apply_playback_transition(100, trans_invalid, expected_version=0)

        # Transition ke IDLE tapi ada current_entry -> ditolak
        trans_idle_invalid = PlaybackTransition(
            next_current_entry=e1,
            next_upcoming=(),
            next_state=PlaybackState.IDLE,
        )
        with pytest.raises(
            InvalidStateTransition, match="Status 'idle' wajib memiliki current_entry bernilai None"
        ):
            await repo.apply_playback_transition(100, trans_idle_invalid, expected_version=0)

    @pytest.mark.asyncio
    async def test_apply_playback_transition_paused_to_idle(self) -> None:
        repo = await self.create_repository()
        e1 = make_entry(guild_id=100, title="Song 1")

        # Setup PLAYING -> PAUSED
        await repo.append(100, [e1], expected_version=0)  # v=1
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )  # v=2
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )  # v=3
        _, _s4 = await repo.claim_next(100, expected_version=3)  # v=4, current=e1, PLAYING
        s5 = await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.PAUSED), expected_version=4
        )  # v=5, PAUSED
        assert s5.state == PlaybackState.PAUSED

        # Transition PAUSED -> IDLE
        trans = PlaybackTransition(
            next_current_entry=None,
            next_upcoming=(),
            next_state=PlaybackState.IDLE,
            increment_generation=True,
        )
        s6 = await repo.apply_playback_transition(100, trans, expected_version=5)
        assert s6.version == 6
        assert s6.state == PlaybackState.IDLE
        assert s6.current_entry is None
        assert s6.generation == 2
