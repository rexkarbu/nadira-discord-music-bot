"""Unit tests untuk VoiceSessionService pada layer aplikasi."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from iwed_bot.application.errors import (
    DifferentVoiceChannel,
    LavalinkUnavailable,
    UserNotInVoice,
    VoiceConnectionFailed,
    VoiceDisconnectFailed,
)
from iwed_bot.application.voice import VoiceSessionService
from iwed_bot.domain.models import (
    PlaybackState,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
)
from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.ports.voice import VoiceConnectionSnapshot, VoiceGateway


class FakeVoiceGateway(VoiceGateway):
    """Fake VoiceGateway untuk pengujian unit independen."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.connections: dict[int, VoiceConnectionSnapshot] = {}
        self.connect_calls: list[tuple[int, int]] = []
        self.move_calls: list[tuple[int, int]] = []
        self.disconnect_calls: list[int] = []
        self.shutdown_called: bool = False
        self.fail_on_connect: Exception | None = None
        self.fail_on_disconnect: Exception | None = None
        self.disconnect_leaves_connected: bool = False

    async def is_available(self) -> bool:
        return self.available

    async def get_connection(self, guild_id: int) -> VoiceConnectionSnapshot | None:
        return self.connections.get(guild_id)

    async def connect(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        _ = timeout
        self.connect_calls.append((guild_id, channel_id))
        if self.fail_on_connect:
            raise self.fail_on_connect
        snap = VoiceConnectionSnapshot(guild_id=guild_id, channel_id=channel_id, is_connected=True)
        self.connections[guild_id] = snap
        return snap

    async def move(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        _ = timeout
        self.move_calls.append((guild_id, channel_id))
        snap = VoiceConnectionSnapshot(guild_id=guild_id, channel_id=channel_id, is_connected=True)
        self.connections[guild_id] = snap
        return snap

    async def disconnect(self, guild_id: int) -> None:
        self.disconnect_calls.append(guild_id)
        if self.fail_on_disconnect:
            raise self.fail_on_disconnect
        if not self.disconnect_leaves_connected:
            self.connections.pop(guild_id, None)

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.connections.clear()


def make_entry(guild_id: int = 100) -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=TrackReference(
            id=uuid.uuid4(),
            source_type=SourceType.YOUTUBE,
            source_id="abc",
            source_uri=None,
            search_hint="artist - song",
            title="Song",
            artists=("Artist",),
            duration_ms=180000,
            thumbnail_url=None,
            canonical_url=None,
        ),
        requested_by_user_id=1,
        requested_in_channel_id=1,
        enqueued_at=datetime.now(UTC),
    )


class TestVoiceSessionServiceJoin:
    @pytest.mark.asyncio
    async def test_join_disconnected_to_connecting_to_idle(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        snap, session, is_move, is_noop = await service.join(
            guild_id=100, channel_id=555, text_channel_id=999
        )

        assert snap.is_connected
        assert snap.channel_id == 555
        assert session.state == PlaybackState.IDLE
        assert session.voice_channel_id == 555
        assert session.text_channel_id == 999
        assert not is_move
        assert not is_noop
        assert len(gateway.connect_calls) == 1

    @pytest.mark.asyncio
    async def test_join_same_channel_is_noop(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(guild_id=100, channel_id=555)

        snap2, _session2, is_move, is_noop = await service.join(guild_id=100, channel_id=555)

        assert is_noop
        assert not is_move
        assert snap2.channel_id == 555
        assert len(gateway.connect_calls) == 1

    @pytest.mark.asyncio
    async def test_move_without_permission_rejected(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(guild_id=100, channel_id=555)

        with pytest.raises(DifferentVoiceChannel, match="Diperlukan izin Move Members"):
            await service.join(
                guild_id=100,
                channel_id=777,
                origin_channel_id=555,
                can_move_members=False,
            )

        conn = await gateway.get_connection(100)
        assert conn is not None
        assert conn.channel_id == 555
        assert len(gateway.move_calls) == 0

    @pytest.mark.asyncio
    async def test_move_with_permission_succeeds(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(guild_id=100, channel_id=555)

        snap, session, is_move, is_noop = await service.join(
            guild_id=100,
            channel_id=777,
            origin_channel_id=555,
            can_move_members=True,
        )

        assert is_move
        assert not is_noop
        assert snap.channel_id == 777
        assert session.voice_channel_id == 777
        assert len(gateway.move_calls) == 1

    @pytest.mark.asyncio
    async def test_move_origin_race_condition_rejected(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        # Bot connect ke 555
        await service.join(guild_id=100, channel_id=555)

        # Bot dipindahkan admin secara paralel ke 888 sebelum user memperoleh lock
        await gateway.move(100, 888)

        # User yang menghitung permission saat bot masih di 555 mengirim origin_channel_id=555
        with pytest.raises(DifferentVoiceChannel, match="Bot telah berpindah voice channel"):
            await service.join(
                guild_id=100,
                channel_id=777,
                origin_channel_id=555,
                can_move_members=True,
            )

    @pytest.mark.asyncio
    async def test_lavalink_offline_does_not_change_state(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway(available=False)
        service = VoiceSessionService(repo, gateway)

        with pytest.raises(LavalinkUnavailable):
            await service.join(guild_id=100, channel_id=555)

        session = await repo.get_session(100)
        assert session.version == 0
        assert session.state == PlaybackState.DISCONNECTED
        assert len(gateway.connect_calls) == 0

    @pytest.mark.asyncio
    async def test_connect_failure_is_compensated(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        gateway.fail_on_connect = RuntimeError("Discord Gateway Error")
        service = VoiceSessionService(repo, gateway)

        with pytest.raises(VoiceConnectionFailed):
            await service.join(guild_id=100, channel_id=555)

        session = await repo.get_session(100)
        assert session.state == PlaybackState.DISCONNECTED
        assert session.voice_channel_id is None

    @pytest.mark.asyncio
    async def test_join_connect_calls_gateway_connect_at_most_once_on_conflict(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        orig_update = repo.update_session_state
        call_count = 0

        async def conflicting_update(guild_id: int, update: Any, expected_version: int) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                s = await repo.get_session(guild_id)
                await orig_update(guild_id, SessionStateUpdate(text_channel_id=12345), s.version)
            return await orig_update(guild_id, update, expected_version)

        repo.update_session_state = conflicting_update  # type: ignore[assignment]

        _snap, session, _is_move, _is_noop = await service.join(guild_id=100, channel_id=555)

        assert len(gateway.connect_calls) == 1
        assert session.state == PlaybackState.IDLE

    @pytest.mark.asyncio
    async def test_join_move_calls_gateway_move_at_most_once_on_conflict(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(guild_id=100, channel_id=555)

        orig_update = repo.update_session_state
        call_count = 0

        async def conflicting_update(guild_id: int, update: Any, expected_version: int) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                s = await repo.get_session(guild_id)
                await orig_update(guild_id, SessionStateUpdate(text_channel_id=99999), s.version)
            return await orig_update(guild_id, update, expected_version)

        repo.update_session_state = conflicting_update  # type: ignore[assignment]

        _snap, session, is_move, _is_noop = await service.join(
            guild_id=100, channel_id=777, origin_channel_id=555, can_move_members=True
        )

        assert is_move
        assert len(gateway.move_calls) == 1
        assert session.voice_channel_id == 777


class TestVoiceSessionServiceStop:
    @pytest.mark.asyncio
    async def test_active_connected_requester_none_raises_user_not_in_voice(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)

        with pytest.raises(UserNotInVoice):
            await service.stop(100, requester_channel_id=None)

        assert len(gateway.disconnect_calls) == 0

    @pytest.mark.asyncio
    async def test_disconnected_clean_requester_none_is_idempotent_noop(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        session, was_active = await service.stop(100, requester_channel_id=None)

        assert not was_active
        assert session.version == 0
        assert session.generation == 0
        assert session.state == PlaybackState.DISCONNECTED
        assert len(gateway.disconnect_calls) == 0

    @pytest.mark.asyncio
    async def test_disconnected_with_upcoming_clears_queue_without_generation_increment(
        self,
    ) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        # Queue terisi tapi belum connect (state DISCONNECTED)
        e1 = make_entry(100)
        await repo.append(100, [e1], expected_version=0)

        session, was_active = await service.stop(100, requester_channel_id=None)

        assert was_active
        assert session.state == PlaybackState.DISCONNECTED
        assert session.upcoming == ()
        assert session.generation == 0  # Tidak naik karena tidak ada current track
        assert len(gateway.disconnect_calls) == 0  # Gateway tidak dipanggil

    @pytest.mark.asyncio
    async def test_stopping_state_with_upcoming_clears_queue(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        e1 = make_entry(100)
        e2 = make_entry(100)
        await repo.append(100, [e1, e2], expected_version=0)
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.CONNECTING, voice_channel_id=555),
            expected_version=1,
        )
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.IDLE), expected_version=2
        )
        await repo.claim_next(100, expected_version=3)  # PLAYING, current=e1, upcoming=(e2,)

        # Sesi dalam keadaan STOPPING dengan upcoming tersisa
        from iwed_bot.domain.models import PlaybackTransition

        await repo.apply_playback_transition(
            100,
            PlaybackTransition(
                next_current_entry=None,
                next_upcoming=(e2,),
                next_state=PlaybackState.STOPPING,
                increment_generation=True,
            ),
            expected_version=4,
        )

        session, was_active = await service.stop(100, requester_channel_id=None)

        assert was_active
        assert session.state == PlaybackState.DISCONNECTED
        assert session.upcoming == ()
        assert session.generation == 2

    @pytest.mark.asyncio
    async def test_active_stop_twice_increments_generation_only_once(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        e1 = make_entry(100)
        await repo.append(100, [e1], expected_version=0)
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.CONNECTING, voice_channel_id=555),
            expected_version=1,
        )
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.IDLE),
            expected_version=2,
        )
        await repo.claim_next(100, expected_version=3)  # PLAYING, gen=1
        await gateway.connect(100, 555)

        # Stop pertama
        s1, was_active1 = await service.stop(100, requester_channel_id=555)
        assert was_active1
        assert s1.generation == 2
        assert s1.state == PlaybackState.DISCONNECTED

        # Stop kedua
        s2, was_active2 = await service.stop(100, requester_channel_id=555)
        assert not was_active2
        assert s2.generation == 2  # Tetap 2, tidak naik lagi

    @pytest.mark.asyncio
    async def test_stop_from_different_channel_rejected(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)

        with pytest.raises(DifferentVoiceChannel):
            await service.stop(100, requester_channel_id=777)

    @pytest.mark.asyncio
    async def test_disconnect_failure_maintains_stopping_and_retries_successfully(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        gateway.fail_on_disconnect = RuntimeError("Voice Disconnect Error")
        gateway.disconnect_leaves_connected = True
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)

        with pytest.raises(VoiceDisconnectFailed):
            await service.stop(100, requester_channel_id=555)

        # State harus STOPPING dan voice_channel_id dipertahankan
        session = await repo.get_session(100)
        assert session.state == PlaybackState.STOPPING
        assert session.voice_channel_id == 555

        # Stop berikutnya setelah gateway pulih berhasil membersihkan
        gateway.fail_on_disconnect = None
        gateway.disconnect_leaves_connected = False

        s_retry, was_active = await service.stop(100, requester_channel_id=555)
        assert was_active
        assert s_retry.state == PlaybackState.DISCONNECTED
        assert s_retry.voice_channel_id is None

    @pytest.mark.asyncio
    async def test_stop_calls_gateway_disconnect_at_most_once_on_conflict(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)

        orig_update = repo.update_session_state
        call_count = 0

        async def conflicting_update(guild_id: int, update: Any, expected_version: int) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                s = await repo.get_session(guild_id)
                await orig_update(guild_id, SessionStateUpdate(text_channel_id=88888), s.version)
            return await orig_update(guild_id, update, expected_version)

        repo.update_session_state = conflicting_update  # type: ignore[assignment]

        session, was_active = await service.stop(100, requester_channel_id=555)

        assert was_active
        assert session.state == PlaybackState.DISCONNECTED
        assert len(gateway.disconnect_calls) == 1


class TestVoiceSessionServiceReconciliation:
    @pytest.mark.asyncio
    async def test_external_kick_reconciliation(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        e1 = make_entry(100)
        await repo.append(100, [e1], expected_version=0)
        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.CONNECTING, voice_channel_id=555),
            expected_version=1,
        )
        await repo.update_session_state(
            100, SessionStateUpdate(state=PlaybackState.IDLE), expected_version=2
        )
        await repo.claim_next(100, expected_version=3)
        await gateway.connect(100, 555)
        await gateway.disconnect(100)

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=None)

        session = await repo.get_session(100)
        assert session.state == PlaybackState.DISCONNECTED
        assert session.current_entry is None
        assert session.upcoming == ()
        assert session.generation == 2

    @pytest.mark.asyncio
    async def test_stale_disconnect_event_after_reconnect_is_ignored(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=777)
        s_before = await repo.get_session(100)

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=None)

        s_after = await repo.get_session(100)
        assert s_after.version == s_before.version
        assert s_after.voice_channel_id == 777
        assert s_after.state == PlaybackState.IDLE

    @pytest.mark.asyncio
    async def test_duplicate_external_disconnect_mutates_only_once(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)
        await gateway.disconnect(100)

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=None)
        s1 = await repo.get_session(100)
        assert s1.state == PlaybackState.DISCONNECTED

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=None)
        s2 = await repo.get_session(100)
        assert s2.version == s1.version

    @pytest.mark.asyncio
    async def test_duplicate_external_move_mutates_only_once(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)
        await gateway.move(100, 777)

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=777)
        s1 = await repo.get_session(100)
        assert s1.voice_channel_id == 777

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=777)
        s2 = await repo.get_session(100)
        assert s2.version == s1.version

    @pytest.mark.asyncio
    async def test_mute_deaf_only_event_ignored(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)
        s_before = await repo.get_session(100)

        await service.handle_voice_state_update(100, old_channel_id=555, new_channel_id=555)
        s_after = await repo.get_session(100)

        assert s_after.version == s_before.version

    @pytest.mark.asyncio
    async def test_own_operation_event_is_noop_after_completion(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)
        s_after_join = await repo.get_session(100)

        await service.handle_voice_state_update(100, old_channel_id=None, new_channel_id=555)
        s_after_event = await repo.get_session(100)

        assert s_after_event.version == s_after_join.version
        assert s_after_event.voice_channel_id == 555

    @pytest.mark.asyncio
    async def test_connecting_session_reconciled_to_idle_on_physical_connection(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        await repo.update_session_state(
            100,
            SessionStateUpdate(state=PlaybackState.CONNECTING, voice_channel_id=555),
            expected_version=0,
        )
        await gateway.connect(100, 555)

        await service.handle_voice_state_update(100, old_channel_id=None, new_channel_id=555)

        session = await repo.get_session(100)
        assert session.state == PlaybackState.IDLE
        assert session.voice_channel_id == 555

    @pytest.mark.asyncio
    async def test_stage_channel_failed_disconnect_does_not_store_fake_disconnected_state(
        self,
    ) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        gateway.fail_on_disconnect = RuntimeError("Cannot disconnect stage")
        gateway.disconnect_leaves_connected = True
        service = VoiceSessionService(repo, gateway)

        await service.join(100, channel_id=555)
        # Move gateway ke stage channel 999
        await gateway.move(100, 999)

        # Event dipindahkan ke stage
        await service.handle_voice_state_update(
            100, old_channel_id=555, new_channel_id=999, is_stage=True
        )

        session = await repo.get_session(100)
        # State TIDAK boleh menjadi DISCONNECTED palsu karena player masih terhubung
        assert session.state != PlaybackState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_exhausted_retry_repaired_by_voice_state_event(self) -> None:
        repo = InMemoryQueueRepository()
        gateway = FakeVoiceGateway()
        service = VoiceSessionService(repo, gateway)

        # Simulasikan connect fisik sukses di gateway, tapi update_session exhausted retry
        await gateway.connect(100, 555)

        # Simulasikan event Discord gateway berikutnya datang membawa update channel 555
        await service.handle_voice_state_update(100, old_channel_id=None, new_channel_id=555)

        session = await repo.get_session(100)
        assert session.voice_channel_id == 555
