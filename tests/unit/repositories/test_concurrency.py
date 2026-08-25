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
        repo = InMemoryQueueRepository()
        g1 = 100
        g2 = 200
        e1 = make_entry(g1, "Track G1")
        e2 = make_entry(g2, "Track G2")

        start_gate = asyncio.Event()

        async def worker(guild: int, entry: QueueEntry) -> bool:
            await start_gate.wait()
            session = await repo.append(guild, [entry], expected_version=0)
            return session.version == 1

        t1 = asyncio.create_task(worker(g1, e1))
        t2 = asyncio.create_task(worker(g2, e2))

        start_gate.set()
        res1, res2 = await asyncio.gather(t1, t2)

        assert res1 is True
        assert res2 is True

        s1 = await repo.get_session(g1)
        s2 = await repo.get_session(g2)
        assert s1.version == 1
        assert s1.upcoming == (e1,)
        assert s2.version == 1
        assert s2.upcoming == (e2,)
