"""Ports layer Nadira Discord Music Bot.

Mendefinisikan abstract protocol dan kontrak interface antar komponen.
"""

from nadira_bot.ports.repositories import QueueRepository

__all__ = [
    "QueueRepository",
]
