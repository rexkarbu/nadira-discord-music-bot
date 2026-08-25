"""Manajemen concurrency tingkat aplikasi untuk Iwed Discord Music Bot.

Modul ini menyediakan GuildOperationLockRegistry untuk menserialisasi operasi
aplikasi (seperti /join, /stop, voice-state reconciliation, dan playback orchestration)
per guild secara terisolasi tanpa memblokir guild lain.
Lock aplikasi ini diizinkan melingkupi network I/O.
"""

import asyncio


class GuildOperationLockRegistry:
    """Registry penyimpan asyncio.Lock per guild ID untuk operasi tingkat aplikasi.

    Didesain untuk beroperasi sepanjang lifetime proses dalam single asyncio event loop.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        """Mengambil atau membuat asyncio.Lock aplikasi untuk guild_id tertentu secara idempotent.

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
