"""Concurrency, lock isolation, and optimistic concurrency race tests."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from nadira_bot.domain.errors import VersionConflict
from nadira_bot.domain.models import QueueEntry, SourceType, TrackReference
from nadira_bot.infrastructure.concurrency import GuildLockRegistry
from nadira_bot.infrastructure.repositories.memory import InMemoryQueueRepository


def make_entry(guild_id: int, title: str) -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=TrackReference(
            id=uuid.uuid4(),
            source_type=SourceType.YOUTUBE,
            source_id="test",
            source_uri=None,
            search_hint=f"Artist - {title}",
            title=title,
            artists=("Artist",),
            duration_ms=120000,
            thumbnail_url=None,
            canonical_url=None,
        ),
        requested_by_user_id=1,
        requested_in_channel_id=1,
        enqueued_at=datetime.now(UTC),
    )


class TestConcurrencyAndIsolation:
    def test_lock_registry_returns_same_instance_for_guild(self) -> None:
        registry = GuildLockRegistry()
        lock1 = registry.get_lock(100)
        lock2 = registry.get_lock(100)
        lock_other = registry.get_lock(200)

        assert lock1 is lock2
        assert lock1 is not lock_other

    def test_invalid_max_queue_tracks_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_queue_tracks harus integer positif"):
            InMemoryQueueRepository(max_queue_tracks=0)
        with pytest.raises(ValueError, match="max_queue_tracks harus integer positif"):
            InMemoryQueueRepository(max_queue_tracks=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="max_queue_tracks harus integer positif"):
            InMemoryQueueRepository(max_queue_tracks=-10)

    @pytest.mark.asyncio
    async def test_concurrent_append_race_condition(self) -> None:
        repo = InMemoryQueueRepository()
        guild_id = 100
        e1 = make_entry(guild_id, "Track 1")
        e2 = make_entry(guild_id, "Track 2")

        start_gate = asyncio.Event()

        async def worker(entry: QueueEntry) -> str:
            await start_gate.wait()
            try:
                await repo.append(guild_id, [entry], expected_version=0)
                return "SUCCESS"
            except VersionConflict:
                return "CONFLICT"

        task1 = asyncio.create_task(worker(e1))
        task2 = asyncio.create_task(worker(e2))

        # Lepaskan kedua worker secara bersamaan
        start_gate.set()
        results = await asyncio.gather(task1, task2)

        # Harus ada tepat satu yang SUCCESS dan satu yang CONFLICT
        assert results.count("SUCCESS") == 1
        assert results.count("CONFLICT") == 1

        # Final state harus konsisten (version = 1, queue length = 1)
        final_session = await repo.get_session(guild_id)
        assert final_session.version == 1
        assert len(final_session.upcoming) == 1

    @pytest.mark.asyncio
    async def test_guild_isolation_concurrent_mutations(self) -> None:
        registry = GuildLockRegistry()
        repo = InMemoryQueueRepository(lock_registry=registry)
        g_a = 100
        g_b = 200
        e_a = make_entry(g_a, "Track A")
        e_b = make_entry(g_b, "Track B")

        lock_a_held = asyncio.Event()
        release_lock_a = asyncio.Event()
        guild_b_completed = asyncio.Event()

        async def hold_guild_a_lock() -> None:
            async with registry.get_lock(g_a):
                lock_a_held.set()
                # Tahan lock Guild A sampai Guild B selesai
                await release_lock_a.wait()
            # Setelah lock g_a lepas, jalankan append untuk Guild A
            await repo.append(g_a, [e_a], expected_version=0)

        async def mutate_guild_b() -> None:
            # Tunggu sampai lock Guild A benar-benar sedang ditahan
            await lock_a_held.wait()
            # Buktikan Guild B dapat melakukan mutasi tanpa terhalang lock Guild A
            session_b = await repo.append(g_b, [e_b], expected_version=0)
            assert session_b.version == 1
            assert session_b.upcoming == (e_b,)
            guild_b_completed.set()
            # Sekarang lepaskan lock Guild A
            release_lock_a.set()

        task_a = asyncio.create_task(hold_guild_a_lock())
        task_b = asyncio.create_task(mutate_guild_b())

        await asyncio.gather(task_a, task_b)

        assert guild_b_completed.is_set()
        session_a = await repo.get_session(g_a)
        assert session_a.version == 1
        assert session_a.upcoming == (e_a,)
