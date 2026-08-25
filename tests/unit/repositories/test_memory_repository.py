"""Concrete contract tests for InMemoryQueueRepository."""

from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.ports.repositories import QueueRepository
from tests.unit.repositories.test_repository_contract import BaseQueueRepositoryContractTests


class TestInMemoryQueueRepository(BaseQueueRepositoryContractTests):
    async def create_repository(self, max_queue_tracks: int = 1000) -> QueueRepository:
        return InMemoryQueueRepository(max_queue_tracks=max_queue_tracks)
