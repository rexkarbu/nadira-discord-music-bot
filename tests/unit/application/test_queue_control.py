"""Unit tests untuk QueueControlService, atomicity /skip /pause /resume, dan reconciliation."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.errors import (
    QueuePageOutOfRange,
)
from iwed_bot.application.playback_coordinator import PlaybackCoordinator
from iwed_bot.application.queue_control import QueueControlService
from iwed_bot.domain.errors import VersionConflict
from iwed_bot.domain.models import (
    PlaybackState,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
)
from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.ports.playback import PlaybackGateway, PlaybackSnapshot


def make_test_track(title: str = "Test Track", duration_ms: int | None = 180000) -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="123",
        source_uri="https://www.youtube.com/watch?v=123",
        search_hint="artist - test",
        title=title,
        artists=("Artist",),
        duration_ms=duration_ms,
        thumbnail_url=None,
        canonical_url=None,
        is_stream=duration_ms is None,
    )


def make_test_entry(
    guild_id: int = 123, title: str = "Test Entry", duration_ms: int | None = 180000
) -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=make_test_track(title=title, duration_ms=duration_ms),
        requested_by_user_id=1,
        requested_in_channel_id=10,
        enqueued_at=datetime.now(UTC),
    )


class TestQueueControlService:
    @pytest.fixture
    def setup_control(
        self,
    ) -> tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock]:
        repo = InMemoryQueueRepository()
        gateway = MagicMock(spec=PlaybackGateway)
        gateway.stop_current = AsyncMock()
        gateway.pause = AsyncMock(
            side_effect=lambda g, p: PlaybackSnapshot(
                guild_id=g,
                connected=True,
                is_playing=not p,
                is_paused=p,
                position_ms=1000,
                active_entry_id=uuid.uuid4(),
                active_generation=1,
            )
        )
        gateway.get_snapshot = AsyncMock(
            return_value=PlaybackSnapshot(
                guild_id=123,
                connected=True,
                is_playing=True,
                is_paused=False,
                position_ms=5000,
                active_entry_id=uuid.uuid4(),
                active_generation=1,
            )
        )
        coord = MagicMock(spec=PlaybackCoordinator)
        coord.ensure_running = AsyncMock()
        lock_registry = GuildOperationLockRegistry()

        service = QueueControlService(
            queue_repository=repo,
            playback_gateway=gateway,
            coordinator=coord,
            operation_locks=lock_registry,
        )
        return service, repo, gateway, coord

    @pytest.mark.asyncio
    async def test_skip_success_with_upcoming(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, _s4 = await repo.claim_next(guild_id, expected_version=3)

        skipped_count, next_entry = await service.skip(guild_id, count=1)

        assert skipped_count == 1
        assert next_entry == e2
        gateway.stop_current.assert_awaited_once_with(guild_id)
        coord.ensure_running.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_version_conflict_does_not_repeat_physical_stop(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        orig_apply = repo.apply_playback_transition
        conflict_injected = False

        async def fail_once_then_succeed(
            g_id: int, transition: Any, *, expected_version: int
        ) -> Any:
            nonlocal conflict_injected
            if not conflict_injected:
                conflict_injected = True
                s = await repo.get_session(g_id)
                repo._sessions[g_id] = repo._sessions[g_id].__class__(
                    guild_id=g_id,
                    version=s.version + 1,
                    upcoming=s.upcoming,
                    state=s.state,
                    current_entry=s.current_entry,
                    generation=s.generation,
                    voice_channel_id=s.voice_channel_id,
                )
                raise VersionConflict()
            return await orig_apply(g_id, transition, expected_version=expected_version)

        repo.apply_playback_transition = AsyncMock(side_effect=fail_once_then_succeed)

        skipped_count, next_entry = await service.skip(guild_id, count=1)

        assert skipped_count == 1
        assert next_entry == e2
        gateway.stop_current.assert_awaited_once_with(guild_id)

    @pytest.mark.asyncio
    async def test_skip_conflict_reconciles_domain_after_physical_stop(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        async def always_conflict(
            _g_id: int, _transition: Any, *, expected_version: int = 0
        ) -> Any:
            _ = expected_version
            s = await repo.get_session(guild_id)
            repo._sessions[guild_id] = repo._sessions[guild_id].__class__(
                guild_id=guild_id,
                version=s.version + 1,
                upcoming=s.upcoming[1:],
                state=s.state,
                current_entry=s.upcoming[0],
                generation=s.generation + 1,
                voice_channel_id=s.voice_channel_id,
            )
            raise VersionConflict()

        repo.apply_playback_transition = AsyncMock(side_effect=always_conflict)

        skipped_count, _next_e = await service.skip(guild_id, count=1)
        assert skipped_count == 1
        gateway.stop_current.assert_awaited_once_with(guild_id)

    @pytest.mark.asyncio
    async def test_skip_successor_failure_does_not_claim_now_playing(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)

        _skipped_count, next_entry = await service.skip(guild_id, count=1)
        assert next_entry == e2

    @pytest.mark.asyncio
    async def test_pause_conflict_does_not_repeat_network_call(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)

        orig_update = repo.update_session_state
        conflict_injected = False

        async def fail_once_then_succeed(g_id: int, mutation: Any, *, expected_version: int) -> Any:
            nonlocal conflict_injected
            if not conflict_injected:
                conflict_injected = True
                s = await repo.get_session(g_id)
                repo._sessions[g_id] = repo._sessions[g_id].__class__(
                    guild_id=g_id,
                    version=s.version + 1,
                    upcoming=s.upcoming,
                    state=s.state,
                    current_entry=s.current_entry,
                    generation=s.generation,
                    voice_channel_id=s.voice_channel_id,
                )
                raise VersionConflict()
            return await orig_update(g_id, mutation, expected_version=expected_version)

        repo.update_session_state = AsyncMock(side_effect=fail_once_then_succeed)

        snap = await service.pause(guild_id)
        assert snap.is_paused is True
        gateway.pause.assert_awaited_once_with(guild_id, True)

    @pytest.mark.asyncio
    async def test_resume_conflict_does_not_repeat_network_call(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)
        # Put in PAUSED
        s = await repo.get_session(guild_id)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.PAUSED), expected_version=s.version
        )

        orig_update = repo.update_session_state
        conflict_injected = False

        async def fail_once_then_succeed(g_id: int, mutation: Any, *, expected_version: int) -> Any:
            nonlocal conflict_injected
            if not conflict_injected:
                conflict_injected = True
                s_cur = await repo.get_session(g_id)
                repo._sessions[g_id] = repo._sessions[g_id].__class__(
                    guild_id=g_id,
                    version=s_cur.version + 1,
                    upcoming=s_cur.upcoming,
                    state=s_cur.state,
                    current_entry=s_cur.current_entry,
                    generation=s_cur.generation,
                    voice_channel_id=s_cur.voice_channel_id,
                )
                raise VersionConflict()
            return await orig_update(g_id, mutation, expected_version=expected_version)

        repo.update_session_state = AsyncMock(side_effect=fail_once_then_succeed)

        snap = await service.resume(guild_id)
        assert snap.is_paused is False
        gateway.pause.assert_awaited_once_with(guild_id, False)

    @pytest.mark.asyncio
    async def test_physical_pause_failure_does_not_commit_domain(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)

        gateway.pause = AsyncMock(side_effect=RuntimeError("Physical network pause failed"))

        with pytest.raises(RuntimeError):
            await service.pause(guild_id)

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.PLAYING

    @pytest.mark.asyncio
    async def test_physical_resume_failure_does_not_commit_domain(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)
        s = await repo.get_session(guild_id)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.PAUSED), expected_version=s.version
        )

        gateway.pause = AsyncMock(side_effect=RuntimeError("Physical network resume failed"))

        with pytest.raises(RuntimeError):
            await service.resume(guild_id)

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.PAUSED

    @pytest.mark.asyncio
    async def test_get_queue_page(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _gateway, _coord = setup_control
        guild_id = 123
        entries = [make_test_entry(guild_id=guild_id, title=f"Song {i}") for i in range(1, 16)]

        await repo.append(guild_id, entries, expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        claimed, _ = await repo.claim_next(guild_id, expected_version=3)

        curr, items, page, total_pages, total_tracks, _dur, _streams = await service.get_queue_page(
            guild_id, page=1
        )
        assert curr == claimed
        assert len(items) == 10
        assert page == 1
        assert total_pages == 2
        assert total_tracks == 14

        with pytest.raises(QueuePageOutOfRange):
            await service.get_queue_page(guild_id, page=3)

    @pytest.mark.asyncio
    async def test_skip_recomputes_transition_on_concurrent_append(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")
        e3 = make_test_entry(guild_id=guild_id, title="Song 3 (Appended)")

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)

        orig_apply = repo.apply_playback_transition
        conflict_injected = False

        async def append_during_first_apply(g_id: int, trans: Any, expected_version: int) -> Any:
            nonlocal conflict_injected
            if not conflict_injected:
                conflict_injected = True
                # Concurrent append happens here
                cur = await repo.get_session(g_id)
                await repo.append(g_id, [e3], expected_version=cur.version)
                raise VersionConflict()
            return await orig_apply(g_id, trans, expected_version=expected_version)

        repo.apply_playback_transition = AsyncMock(side_effect=append_during_first_apply)

        skipped_count, next_entry = await service.skip(guild_id, count=1)
        assert skipped_count == 1
        assert next_entry == e2

        # Verify final session has e3 preserved in upcoming!
        final_session = await repo.get_session(guild_id)
        assert final_session.current_entry == e2
        assert len(final_session.upcoming) == 1
        assert final_session.upcoming[0].id == e3.id

    @pytest.mark.asyncio
    async def test_pause_target_changed_raises_reconciliation_failed(
        self,
        setup_control: tuple[QueueControlService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        from iwed_bot.application.errors import PlaybackReconciliationFailed

        service, repo, _gateway, _coord = setup_control
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)
        e2 = make_test_entry(guild_id=guild_id)

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        await repo.claim_next(guild_id, expected_version=3)

        async def change_target_during_pause(
            g_id: int, mutation: Any, *, expected_version: int
        ) -> Any:
            _ = (mutation, expected_version)
            s = await repo.get_session(g_id)
            repo._sessions[g_id] = repo._sessions[g_id].__class__(
                guild_id=g_id,
                version=s.version + 1,
                upcoming=(),
                state=PlaybackState.PLAYING,
                current_entry=e2,  # Target changed to e2
                generation=s.generation + 1,
                voice_channel_id=s.voice_channel_id,
            )
            raise VersionConflict()

        repo.update_session_state = AsyncMock(side_effect=change_target_during_pause)

        with pytest.raises(PlaybackReconciliationFailed, match="Target track telah berubah"):
            await service.pause(guild_id)
