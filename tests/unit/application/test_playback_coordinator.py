"""Unit tests untuk PlaybackCoordinator, runner registry, failure counter, dan handle lifecycle."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.playback_coordinator import (
    PlaybackCoordinator,
    RunnerStatus,
)
from iwed_bot.domain.models import (
    PlaybackState,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
)
from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.ports.notifications import PlaybackNotifier
from iwed_bot.ports.playback import PlaybackGateway, PlaybackSnapshot, PreparedPlayback


def make_test_track(title: str = "Test Track") -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="id123",
        source_uri="https://www.youtube.com/watch?v=id123",
        search_hint="artist - test",
        title=title,
        artists=("Artist",),
        duration_ms=120000,
        thumbnail_url=None,
        canonical_url=None,
    )


def make_test_entry(guild_id: int = 123, title: str = "Test Entry") -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=make_test_track(title=title),
        requested_by_user_id=1,
        requested_in_channel_id=10,
        enqueued_at=datetime.now(UTC),
    )


class TestPlaybackCoordinator:
    @pytest.fixture
    def setup_coordinator(
        self,
    ) -> tuple[PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock]:
        repo = InMemoryQueueRepository()
        gateway = MagicMock(spec=PlaybackGateway)
        gateway.is_available = AsyncMock(return_value=True)

        async def mock_prepare(guild_id: int, track: TrackReference) -> PreparedPlayback:
            _ = guild_id
            return PreparedPlayback(handle_id=uuid.uuid4(), track_id=track.id)

        async def mock_play(
            guild_id: int,
            prepared: PreparedPlayback,
            entry_id: uuid.UUID,
            generation: int,
            volume: int = 70,
        ) -> PlaybackSnapshot:
            _ = (prepared, volume)
            return PlaybackSnapshot(
                guild_id=guild_id,
                connected=True,
                is_playing=True,
                is_paused=False,
                position_ms=0,
                active_entry_id=entry_id,
                active_generation=generation,
            )

        gateway.prepare_reference = AsyncMock(side_effect=mock_prepare)
        gateway.play_prepared = AsyncMock(side_effect=mock_play)
        gateway.get_snapshot = AsyncMock(return_value=None)
        gateway.discard_prepared = AsyncMock()

        notifier = MagicMock(spec=PlaybackNotifier)
        notifier.notify_playback_halted = AsyncMock()
        lock_registry = GuildOperationLockRegistry()

        coord = PlaybackCoordinator(
            queue_repository=repo,
            playback_gateway=gateway,
            operation_locks=lock_registry,
            notifier=notifier,
        )
        return coord, repo, gateway, notifier

    @pytest.mark.asyncio
    async def test_idle_session_claims_and_plays(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")

        # Setup session IDLE with voice and 1 upcoming song
        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        task = await coord.ensure_running(guild_id)
        outcome = await task

        assert outcome.status == RunnerStatus.STARTED
        assert outcome.started_entry_id == e1.id
        assert outcome.generation == 1

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.PLAYING
        assert sess.current_entry == e1
        assert sess.upcoming == ()
        gateway.play_prepared.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_target_deduplication(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
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

        task1 = await coord.ensure_running(guild_id)
        task2 = await coord.ensure_running(guild_id)

        assert task1 is task2
        outcome = await task1
        assert outcome.status == RunnerStatus.STARTED

    # --- ITEM 2: Target-aware runner registry tests ---

    @pytest.mark.asyncio
    async def test_two_concurrent_replacement_callers_produce_single_successor_runner(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        # Slow prepare for e1
        prepare_barrier = asyncio.Event()

        async def slow_prepare(g_id: int, track: TrackReference) -> PreparedPlayback:
            _ = g_id
            if track.title == "Song 1":
                await prepare_barrier.wait()
            return PreparedPlayback(handle_id=uuid.uuid4(), track_id=track.id)

        gateway.prepare_reference = AsyncMock(side_effect=slow_prepare)

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        # Start initial runner for e1
        _task_e1 = await coord.ensure_running(guild_id)
        await asyncio.sleep(0.01)

        # Skip to e2
        async with coord._operation_locks.get_lock(guild_id):
            s = await repo.get_session(guild_id)
            from iwed_bot.domain.transitions import compute_manual_skip_transition

            trans = compute_manual_skip_transition(s, count=1)
            s_new = await repo.apply_playback_transition(guild_id, trans, s.version)

        # Two callers concurrently invoke ensure_running for e2
        t1, t2 = await asyncio.gather(
            coord.ensure_running(
                guild_id, expected_entry_id=e2.id, expected_generation=s_new.generation
            ),
            coord.ensure_running(
                guild_id, expected_entry_id=e2.id, expected_generation=s_new.generation
            ),
        )

        assert t1 is t2
        prepare_barrier.set()
        outcome = await t1
        assert outcome.status == RunnerStatus.STARTED
        assert outcome.started_entry_id == e2.id

    @pytest.mark.asyncio
    async def test_stale_target_does_not_play_other_current_entry(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
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
        # Session already claimed e1
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        # Runner with expected_entry_id = e2 (which is in upcoming, not current) must be SUPERSEDED
        task = await coord.ensure_running(guild_id, expected_entry_id=e2.id, expected_generation=1)
        outcome = await task

        assert outcome.status == RunnerStatus.SUPERSEDED

    @pytest.mark.asyncio
    async def test_old_task_completion_does_not_remove_successor_slot(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        prepare_event_e1 = asyncio.Event()
        prepare_event_e2 = asyncio.Event()

        async def slow_prepare(g_id: int, track: TrackReference) -> PreparedPlayback:
            _ = g_id
            if track.title == "Song 1":
                await prepare_event_e1.wait()
            elif track.title == "Song 2":
                await prepare_event_e2.wait()
            return PreparedPlayback(handle_id=uuid.uuid4(), track_id=track.id)

        gateway.prepare_reference = AsyncMock(side_effect=slow_prepare)

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        _task_e1 = await coord.ensure_running(guild_id)
        await asyncio.sleep(0.01)

        # Transition to e2
        async with coord._operation_locks.get_lock(guild_id):
            s = await repo.get_session(guild_id)
            from iwed_bot.domain.transitions import compute_manual_skip_transition

            trans = compute_manual_skip_transition(s, count=1)
            s_new = await repo.apply_playback_transition(guild_id, trans, s.version)

        # Launch successor task (which waits on prepare_event_e2)
        task_e2 = await coord.ensure_running(
            guild_id, expected_entry_id=e2.id, expected_generation=s_new.generation
        )

        # Trigger completion of old task
        prepare_event_e1.set()
        await asyncio.sleep(0.01)

        # The slot in _guild_runners MUST still hold task_e2 while task_e2 is running
        async with coord._registry_lock:
            slot = coord._guild_runners.get(guild_id)
            assert slot is not None
            assert slot.task is task_e2

        prepare_event_e2.set()
        await task_e2

    @pytest.mark.asyncio
    async def test_internal_supersession_distinguished_from_shutdown_cancellation(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
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

        await coord.shutdown()
        task = await coord.ensure_running(guild_id)
        outcome = await task
        assert outcome.status == RunnerStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_no_await_old_task_while_holding_registry_lock(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        e2 = make_test_entry(guild_id=guild_id, title="Song 2")

        cancel_observed = asyncio.Event()

        async def blocking_prepare(g_id: int, track: TrackReference) -> PreparedPlayback:
            _ = g_id
            if track.title == "Song 1":
                try:
                    await asyncio.sleep(10.0)
                except asyncio.CancelledError:
                    cancel_observed.set()
                    raise
            return PreparedPlayback(handle_id=uuid.uuid4(), track_id=track.id)

        gateway.prepare_reference = AsyncMock(side_effect=blocking_prepare)

        await repo.append(guild_id, [e1, e2], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        await coord.ensure_running(guild_id)
        await asyncio.sleep(0.01)

        # Trigger supersession
        task_e2 = await coord.ensure_running(
            guild_id, expected_entry_id=e2.id, expected_generation=2
        )
        assert not coord._registry_lock.locked()
        await task_e2

    # --- ITEM 3: Prepared handle lifecycle try/finally tests ---

    @pytest.mark.asyncio
    async def test_runner_cancellation_after_prepare_discards_handle(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)
        created_handle = PreparedPlayback(handle_id=uuid.uuid4(), track_id=e1.track.id)

        gateway.prepare_reference = AsyncMock(return_value=created_handle)

        play_entered = asyncio.Event()
        block_play = asyncio.Event()

        async def slow_play(*_args: Any, **_kwargs: Any) -> PlaybackSnapshot:
            play_entered.set()
            await block_play.wait()
            return PlaybackSnapshot(
                guild_id=guild_id,
                connected=True,
                is_playing=True,
                is_paused=False,
                position_ms=0,
                active_entry_id=e1.id,
                active_generation=1,
            )

        gateway.play_prepared = AsyncMock(side_effect=slow_play)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        runner_task = await coord.ensure_running(guild_id)
        await play_entered.wait()

        # Cancel runner task while handle is prepared and play is waiting
        runner_task.cancel()
        block_play.set()

        with pytest.raises(asyncio.CancelledError):
            await runner_task

        # Assert discard_prepared was called with the handle
        gateway.discard_prepared.assert_awaited_with(created_handle)

    @pytest.mark.asyncio
    async def test_stale_target_discards_prepared_handle(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)
        created_handle = PreparedPlayback(handle_id=uuid.uuid4(), track_id=e1.track.id)
        gateway.prepare_reference = AsyncMock(return_value=created_handle)

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, s_claimed = await repo.claim_next(guild_id, expected_version=3)

        # Mutate session generation during prepare
        orig_prepare = gateway.prepare_reference

        async def prepare_and_bump_generation(*args: Any, **kwargs: Any) -> PreparedPlayback:
            res = await orig_prepare(*args, **kwargs)
            async with coord._operation_locks.get_lock(guild_id):
                s = await repo.get_session(guild_id)
                from iwed_bot.domain.transitions import compute_manual_skip_transition

                trans = compute_manual_skip_transition(s, count=1)
                await repo.apply_playback_transition(guild_id, trans, expected_version=s.version)
            return res

        gateway.prepare_reference = AsyncMock(side_effect=prepare_and_bump_generation)

        # Target expected generation is s_claimed.generation (1)
        task = await coord.ensure_running(
            guild_id, expected_entry_id=e1.id, expected_generation=s_claimed.generation
        )
        outcome = await task

        assert outcome.status == RunnerStatus.SUPERSEDED
        gateway.discard_prepared.assert_awaited_with(created_handle)

    @pytest.mark.asyncio
    async def test_prepare_success_then_revalidation_exception_discards_handle(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id)
        created_handle = PreparedPlayback(handle_id=uuid.uuid4(), track_id=e1.track.id)
        gateway.prepare_reference = AsyncMock(return_value=created_handle)
        gateway.play_prepared = AsyncMock(side_effect=RuntimeError("Physical play socket error"))

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        task = await coord.ensure_running(guild_id)
        await task

        gateway.discard_prepared.assert_awaited_with(created_handle)

    @pytest.mark.asyncio
    async def test_shutdown_leaves_zero_prepared_handles(self) -> None:
        mock_bot = MagicMock()
        from iwed_bot.infrastructure.playback.wavelink_gateway import WavelinkPlaybackGateway

        gw = WavelinkPlaybackGateway(mock_bot)
        h1 = uuid.uuid4()
        h2 = uuid.uuid4()
        gw._prepared_handles[h1] = (123, uuid.uuid4(), MagicMock())
        gw._prepared_handles[h2] = (456, uuid.uuid4(), MagicMock())

        await gw.shutdown()
        assert len(gw._prepared_handles) == 0

    # --- ITEM 5: Duplicate failure events & safety counter tests ---

    @pytest.mark.asyncio
    async def test_duplicate_exception_then_load_failed_counts_once(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, _s = await repo.claim_next(guild_id, expected_version=3)  # current=e1, generation=1

        # 1. First event: TrackExceptionEvent
        await coord.handle_track_exception(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            _exception=RuntimeError("Decoder error"),
        )
        assert coord._consecutive_failures.get(guild_id, 0) == 1

        # 2. Second event for SAME entry/gen: TrackEndEvent(reason="loadFailed")
        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            reason="loadFailed",
        )
        # Must still be 1 (not 2)
        assert coord._consecutive_failures.get(guild_id, 0) == 1

    @pytest.mark.asyncio
    async def test_stale_failure_does_not_increment_counter(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
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
        _, _s = await repo.claim_next(guild_id, expected_version=3)  # generation=1

        # Failure event with old generation 0
        await coord.handle_track_exception(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=0,
            _exception=RuntimeError("Old error"),
        )
        assert coord._consecutive_failures.get(guild_id, 0) == 0

    @pytest.mark.asyncio
    async def test_three_distinct_failures_halt_once(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, notifier = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Fail 1")
        e2 = make_test_entry(guild_id=guild_id, title="Fail 2")
        e3 = make_test_entry(guild_id=guild_id, title="Fail 3")

        gateway.prepare_reference = AsyncMock(side_effect=RuntimeError("Prepare failed"))

        await repo.append(guild_id, [e1, e2, e3], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        task = await coord.ensure_running(guild_id)
        outcome = await task

        assert outcome.status == RunnerStatus.HALTED
        assert len(outcome.failed_entry_ids) == 3
        notifier.notify_playback_halted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_counter_resets_only_on_finished_event(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
        guild_id = 123
        coord._consecutive_failures[guild_id] = 2  # 2 previous failures

        e1 = make_test_entry(guild_id=guild_id, title="Good Song")
        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )

        task = await coord.ensure_running(guild_id)
        outcome = await task

        assert outcome.status == RunnerStatus.STARTED
        # Failure counter is NOT reset on play() success alone
        assert coord._consecutive_failures.get(guild_id) == 2

        # Failure counter resets on natural track completion
        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=outcome.generation,
            reason="finished",
        )
        assert coord._consecutive_failures.get(guild_id) == 0

    @pytest.mark.asyncio
    async def test_duplicate_failure_event_is_deduplicated(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
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
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        # First failure event
        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            reason="loadFailed",
        )
        assert coord._consecutive_failures.get(guild_id) == 1

        # Duplicate failure event for same entry and generation
        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            reason="loadFailed",
        )
        # Should remain 1 (deduplicated)
        assert coord._consecutive_failures.get(guild_id) == 1

    @pytest.mark.asyncio
    async def test_cleanup_reason_transitions_to_idle_preserving_upcoming(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, gateway, _notif = setup_coordinator
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
        _, s = await repo.claim_next(guild_id, expected_version=3)

        gateway.get_snapshot.return_value = PlaybackSnapshot(
            guild_id=guild_id,
            connected=False,
            is_playing=False,
            is_paused=False,
            position_ms=None,
            active_entry_id=None,
            active_generation=None,
        )

        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=s.generation,
            reason="cleanup",
        )

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.IDLE
        assert sess.current_entry is None
        assert len(sess.upcoming) == 1
        assert sess.upcoming[0].id == e2.id

    @pytest.mark.asyncio
    async def test_event_failure_halt_notifies_domain_channel(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, notifier = setup_coordinator
        guild_id = 123
        coord._consecutive_failures[guild_id] = 2  # At threshold - 1

        e1 = make_test_entry(guild_id=guild_id)
        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999, text_channel_id=888),
            expected_version=2,
        )
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            reason="loadFailed",
        )

        notifier.notify_playback_halted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notifier_failure_does_not_corrupt_state(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
    ) -> None:
        coord, repo, _gateway, notifier = setup_coordinator
        guild_id = 123
        coord._consecutive_failures[guild_id] = 2

        notifier.notify_playback_halted = AsyncMock(side_effect=RuntimeError("Discord API 500"))

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
        _, _s = await repo.claim_next(guild_id, expected_version=3)

        # Should not raise exception
        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=1,
            reason="loadFailed",
        )

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.IDLE

    # --- ITEM 6: Exact raw reason parameterized tests ---

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw_reason", "expected_state"),
        [
            ("finished", PlaybackState.IDLE),
            ("loadFailed", PlaybackState.IDLE),
            ("stopped", PlaybackState.PLAYING),  # Stale stopped does not advance
            ("replaced", PlaybackState.PLAYING),  # Stale replaced does not advance
            ("cleanup", PlaybackState.PLAYING),
            ("UNKNOWN_RAW_REASON", PlaybackState.PLAYING),  # Safe no-op
        ],
    )
    async def test_exact_raw_reasons_contract(
        self,
        setup_coordinator: tuple[
            PlaybackCoordinator, InMemoryQueueRepository, MagicMock, MagicMock
        ],
        raw_reason: str,
        expected_state: PlaybackState,
    ) -> None:
        coord, repo, _gateway, _notif = setup_coordinator
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")

        await repo.append(guild_id, [e1], expected_version=0)
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=1
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=999),
            expected_version=2,
        )
        _, _s = await repo.claim_next(guild_id, expected_version=3)  # current=e1, generation=1

        # If testing stopped/replaced/cleanup with stale generation
        event_gen = 1 if raw_reason in ("finished", "loadFailed") else 0

        await coord.handle_track_end(
            guild_id=guild_id,
            entry_id=e1.id,
            generation=event_gen,
            reason=raw_reason,
        )

        sess = await repo.get_session(guild_id)
        assert sess.state == expected_state
