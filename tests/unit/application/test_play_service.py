"""Unit tests untuk PlayRequestService, cancellation discipline, dan honest outcomes."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.errors import (
    DifferentVoiceChannel,
    EntrySuperseded,
    PlaylistImportDeferred,
    SpotifySourceDeferred,
    UnsupportedSource,
)
from iwed_bot.application.play_service import PlayRequestService
from iwed_bot.application.playback_coordinator import (
    PlaybackCoordinator,
    RunnerOutcome,
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
from iwed_bot.ports.playback import PlaybackSnapshot
from iwed_bot.ports.sources import SourceClassification, TrackSource


def make_test_track(title: str = "Test Track") -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="123",
        source_uri="https://www.youtube.com/watch?v=123",
        search_hint="artist - test",
        title=title,
        artists=("Artist",),
        duration_ms=180000,
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


class TestPlayRequestService:
    @pytest.fixture
    def setup_service(
        self,
    ) -> tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock]:
        repo = InMemoryQueueRepository()
        source = MagicMock(spec=TrackSource)
        voice_service = MagicMock()

        async def fake_join(
            guild_id: int, channel_id: int, text_channel_id: int | None = None
        ) -> tuple[MagicMock, Any, bool, bool]:
            _ = text_channel_id
            s = await repo.get_session(guild_id)
            if s.state == PlaybackState.DISCONNECTED:
                await repo.update_session_state(
                    guild_id,
                    SessionStateUpdate(state=PlaybackState.CONNECTING),
                    expected_version=s.version,
                )
                await repo.update_session_state(
                    guild_id,
                    SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=channel_id),
                    expected_version=s.version + 1,
                )
            return MagicMock(), await repo.get_session(guild_id), False, False

        voice_service.join = AsyncMock(side_effect=fake_join)

        coordinator = MagicMock(spec=PlaybackCoordinator)
        lock_registry = GuildOperationLockRegistry()

        service = PlayRequestService(
            track_source=source,
            queue_repository=repo,
            voice_service=voice_service,
            coordinator=coordinator,
            operation_locks=lock_registry,
        )
        return service, repo, source, coordinator

    @pytest.mark.asyncio
    async def test_resolve_input_search_text(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, _repo, source, _coord = setup_service
        track = make_test_track()
        source.search = AsyncMock(return_value=(track,))

        kind, res_track = await service.resolve_input("linkin park numb")
        assert kind == SourceClassification.SEARCH_TEXT
        assert res_track == track

    @pytest.mark.asyncio
    async def test_resolve_input_youtube_url(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, _repo, source, _coord = setup_service
        track = make_test_track()
        source.resolve_single_url = AsyncMock(return_value=track)

        kind, res_track = await service.resolve_input("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res_track == track

    @pytest.mark.asyncio
    async def test_resolve_input_playlist_raises_deferred(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, _repo, _source, _coord = setup_service
        with pytest.raises(PlaylistImportDeferred):
            await service.resolve_input("https://www.youtube.com/playlist?list=PL123")

    @pytest.mark.asyncio
    async def test_resolve_input_spotify_raises_deferred(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, _repo, _source, _coord = setup_service
        with pytest.raises(SpotifySourceDeferred):
            await service.resolve_input("https://open.spotify.com/track/123")

    @pytest.mark.asyncio
    async def test_resolve_input_unsupported_raises_unsupported_source(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, _repo, _source, _coord = setup_service
        with pytest.raises(UnsupportedSource):
            await service.resolve_input("https://soundcloud.com/artist/track")

    @pytest.mark.asyncio
    async def test_enqueue_and_start_started_outcome(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        track = make_test_track()

        async def mock_ensure_running(_g_id: int) -> asyncio.Task[RunnerOutcome]:
            async def get_outcome() -> RunnerOutcome:
                sess = await repo.get_session(guild_id)
                claimed, new_sess = await repo.claim_next(guild_id, expected_version=sess.version)
                snap = PlaybackSnapshot(
                    guild_id=guild_id,
                    connected=True,
                    is_playing=True,
                    is_paused=False,
                    position_ms=0,
                    active_entry_id=claimed.id if claimed else None,
                    active_generation=new_sess.generation,
                )
                return RunnerOutcome(
                    status=RunnerStatus.STARTED,
                    started_entry_id=claimed.id if claimed else None,
                    generation=new_sess.generation,
                    snapshot=snap,
                )

            return asyncio.create_task(get_outcome())

        coordinator.ensure_running = mock_ensure_running

        status, entry, snapshot = await service.enqueue_and_start(
            guild_id=guild_id,
            track=track,
            user_id=1,
            channel_id=555,
            text_channel_id=999,
        )

        assert status == "STARTED"
        assert entry.track == track
        assert snapshot is not None
        assert snapshot.is_playing is True

    @pytest.mark.asyncio
    async def test_two_concurrent_plays_from_idle_produce_started_and_queued(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        t1 = make_test_track(title="Song 1")
        t2 = make_test_track(title="Song 2")

        runner_created = False

        async def mock_ensure_running(_g_id: int) -> asyncio.Task[RunnerOutcome]:
            nonlocal runner_created
            if not runner_created:
                runner_created = True

                async def get_outcome() -> RunnerOutcome:
                    await asyncio.sleep(0.02)
                    sess = await repo.get_session(guild_id)
                    claimed, new_sess = await repo.claim_next(
                        guild_id, expected_version=sess.version
                    )
                    snap = PlaybackSnapshot(
                        guild_id=guild_id,
                        connected=True,
                        is_playing=True,
                        is_paused=False,
                        position_ms=0,
                        active_entry_id=claimed.id if claimed else None,
                        active_generation=new_sess.generation,
                    )
                    return RunnerOutcome(
                        status=RunnerStatus.STARTED,
                        started_entry_id=claimed.id if claimed else None,
                        generation=new_sess.generation,
                        snapshot=snap,
                    )

                return asyncio.create_task(get_outcome())

            async def get_existing() -> RunnerOutcome:
                await asyncio.sleep(0.02)
                sess = await repo.get_session(guild_id)
                return RunnerOutcome(
                    status=RunnerStatus.ALREADY_ACTIVE,
                    started_entry_id=sess.current_entry.id if sess.current_entry else None,
                    generation=sess.generation,
                )

            return asyncio.create_task(get_existing())

        coordinator.ensure_running = mock_ensure_running

        res1, res2 = await asyncio.gather(
            service.enqueue_and_start(guild_id, t1, user_id=1, channel_id=555),
            service.enqueue_and_start(guild_id, t2, user_id=2, channel_id=555),
        )

        statuses = {res1[0], res2[0]}
        assert statuses == {"STARTED", "QUEUED"}

    @pytest.mark.asyncio
    async def test_second_play_request_while_active_does_not_restart_first_track(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        e1 = make_test_entry(guild_id=guild_id, title="Song 1")
        t2 = make_test_track(title="Song 2")

        # First track already playing
        await repo.update_session_state(
            guild_id, SessionStateUpdate(state=PlaybackState.CONNECTING), expected_version=0
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=555),
            expected_version=1,
        )
        await repo.append(guild_id, [e1], expected_version=2)
        _, s_active = await repo.claim_next(guild_id, expected_version=3)

        coordinator.ensure_running = AsyncMock(
            return_value=asyncio.create_task(
                asyncio.sleep(
                    0,
                    result=RunnerOutcome(
                        status=RunnerStatus.ALREADY_ACTIVE,
                        started_entry_id=s_active.current_entry.id
                        if s_active.current_entry
                        else None,
                        generation=s_active.generation,
                    ),
                )
            )
        )

        status, entry, _snap = await service.enqueue_and_start(
            guild_id=guild_id,
            track=t2,
            user_id=1,
            channel_id=555,
        )

        assert status == "QUEUED"
        sess = await repo.get_session(guild_id)
        assert sess.current_entry == s_active.current_entry
        assert any(up.id == entry.id for up in sess.upcoming)

    @pytest.mark.asyncio
    async def test_play_request_skipped_while_waiting_does_not_report_false_success_or_queued(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        t1 = make_test_track(title="Song 1")

        # Coordinator cancels runner due to skip
        async def mock_runner() -> RunnerOutcome:
            # Clear upcoming
            s = await repo.get_session(guild_id)
            repo._sessions[guild_id] = repo._sessions[guild_id].__class__(
                guild_id=guild_id,
                version=s.version + 1,
                upcoming=(),
                state=PlaybackState.IDLE,
                current_entry=None,
                voice_channel_id=555,
            )
            raise asyncio.CancelledError()

        coordinator.ensure_running = AsyncMock(return_value=asyncio.create_task(mock_runner()))

        with pytest.raises(EntrySuperseded):
            await service.enqueue_and_start(guild_id, t1, user_id=1, channel_id=555)

    @pytest.mark.asyncio
    async def test_play_request_cleared_by_stop_does_not_hang(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        t1 = make_test_track(title="Song 1")

        async def mock_runner() -> RunnerOutcome:
            s = await repo.get_session(guild_id)
            repo._sessions[guild_id] = repo._sessions[guild_id].__class__(
                guild_id=guild_id,
                version=s.version + 1,
                upcoming=(),
                state=PlaybackState.IDLE,
                current_entry=None,
                voice_channel_id=555,
            )
            return RunnerOutcome(status=RunnerStatus.EMPTY, started_entry_id=None, generation=1)

        coordinator.ensure_running = AsyncMock(return_value=asyncio.create_task(mock_runner()))

        with pytest.raises(EntrySuperseded):
            await service.enqueue_and_start(guild_id, t1, user_id=1, channel_id=555)

    @pytest.mark.asyncio
    async def test_backlog_safety_halt_plus_new_request_reports_honest_position(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        t1 = make_test_track(title="New Song")

        coordinator.ensure_running = AsyncMock(
            return_value=asyncio.create_task(
                asyncio.sleep(
                    0,
                    result=RunnerOutcome(
                        status=RunnerStatus.HALTED,
                        started_entry_id=None,
                        generation=1,
                    ),
                )
            )
        )

        status, entry, _snap = await service.enqueue_and_start(
            guild_id=guild_id,
            track=t1,
            user_id=1,
            channel_id=555,
        )

        assert status == "QUEUED"
        sess = await repo.get_session(guild_id)
        assert any(up.id == entry.id for up in sess.upcoming)

    @pytest.mark.asyncio
    async def test_cancellation_of_one_waiter_does_not_cancel_shared_runner_or_other_waiters(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, coordinator = setup_service
        guild_id = 123
        t1 = make_test_track(title="Song 1")

        runner_finished = asyncio.Event()

        async def slow_runner() -> RunnerOutcome:
            await asyncio.sleep(0.05)
            s = await repo.get_session(guild_id)
            claimed, new_s = await repo.claim_next(guild_id, expected_version=s.version)
            runner_finished.set()
            return RunnerOutcome(
                status=RunnerStatus.STARTED,
                started_entry_id=claimed.id if claimed else None,
                generation=new_s.generation,
            )

        shared_task = asyncio.create_task(slow_runner())
        coordinator.ensure_running = AsyncMock(return_value=shared_task)

        # Waiter 1 and Waiter 2
        async def waiter_1() -> tuple[str, Any, Any]:
            return await service.enqueue_and_start(guild_id, t1, user_id=1, channel_id=555)

        async def waiter_2() -> tuple[str, Any, Any]:
            return await service.enqueue_and_start(guild_id, t1, user_id=2, channel_id=555)

        w1 = asyncio.create_task(waiter_1())
        w2 = asyncio.create_task(waiter_2())

        await asyncio.sleep(0.01)
        w1.cancel()

        with pytest.raises(asyncio.CancelledError):
            await w1

        res2 = await w2
        assert res2[0] in ("STARTED", "QUEUED")
        assert not shared_task.cancelled()

    @pytest.mark.asyncio
    async def test_enqueue_and_start_different_voice_channel_raises(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        service, repo, _source, _coordinator = setup_service
        guild_id = 123
        track = make_test_track()

        # Set session on different channel (777)
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.CONNECTING),
            expected_version=0,
        )
        await repo.update_session_state(
            guild_id,
            SessionStateUpdate(state=PlaybackState.IDLE, voice_channel_id=777),
            expected_version=1,
        )

        with pytest.raises(DifferentVoiceChannel):
            await service.enqueue_and_start(
                guild_id=guild_id,
                track=track,
                user_id=1,
                channel_id=555,  # user in 555 but bot in 777
            )

    @pytest.mark.asyncio
    async def test_enqueue_and_start_disconnected_session_raises_entry_superseded(
        self,
        setup_service: tuple[PlayRequestService, InMemoryQueueRepository, MagicMock, MagicMock],
    ) -> None:
        from unittest.mock import patch

        service, repo, _source, _coordinator = setup_service
        guild_id = 123
        track = make_test_track()

        sess = await repo.get_session(guild_id)
        assert sess.state == PlaybackState.DISCONNECTED

        with (
            patch.object(
                service._voice_service,
                "join",
                AsyncMock(return_value=(MagicMock(), sess, False, False)),
            ),
            pytest.raises(EntrySuperseded, match="Koneksi voice telah terputus"),
        ):
            await service.enqueue_and_start(
                guild_id=guild_id,
                track=track,
                user_id=1,
                channel_id=555,
            )
