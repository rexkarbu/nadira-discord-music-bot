"""Concrete contract tests for InMemoryQueueRepository."""

import pytest

from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.ports.repositories import QueueRepository
from tests.unit.repositories.test_repository_contract import BaseQueueRepositoryContractTests


class TestInMemoryQueueRepository(BaseQueueRepositoryContractTests):
    async def create_repository(self, max_queue_tracks: int = 1000) -> QueueRepository:
        return InMemoryQueueRepository(max_queue_tracks=max_queue_tracks)

    @pytest.mark.asyncio
    async def test_custom_default_volume(self) -> None:
        repo = InMemoryQueueRepository(default_volume=85)
        sess = await repo.get_session(123)
        assert sess.volume == 85

    def test_invalid_default_volume_raises_value_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="default_volume harus integer antara 0 dan 100"):
            InMemoryQueueRepository(default_volume=-1)

        with pytest.raises(ValueError, match="default_volume harus integer antara 0 dan 100"):
            InMemoryQueueRepository(default_volume=101)
