"""Manajemen concurrency dan lock per-guild untuk Iwed Discord Music Bot.

Modul ini menyediakan GuildLockRegistry untuk memastikan seluruh mutasi state sesi
pada guild yang sama diserialisasi secara aman tanpa memblokir guild lain.
Registry ini didesain untuk digunakan dalam satu asyncio event loop.
"""

import asyncio


class GuildLockRegistry:
    """Registry penyimpan asyncio.Lock per guild ID sepanjang lifetime proses.

    Didesain untuk beroperasi dalam single event loop.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        """Mengambil atau membuat asyncio.Lock untuk guild_id tertentu secara idempotent.

        Args:
            guild_id: ID unik guild Discord.

        Returns:
            Instance asyncio.Lock yang unik dan konsisten untuk guild tersebut.
        """
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock
