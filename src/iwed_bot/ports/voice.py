"""Port interfaces dan value objects untuk koneksi voice dan gateway audio."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VoiceConnectionSnapshot:
    """Snapshot immutable dari koneksi voice gateway audio saat ini."""

    guild_id: int
    channel_id: int
    is_connected: bool


class VoiceGateway(Protocol):
    """Kontrak antarmuka gateway voice tanpa dependensi ke framework eksternal."""

    async def is_available(self) -> bool:
        """Memeriksa apakah gateway audio siap melayani koneksi baru."""
        ...

    async def get_connection(self, guild_id: int) -> VoiceConnectionSnapshot | None:
        """Mengambil snapshot koneksi voice saat ini untuk guild tertentu jika ada."""
        ...

    async def connect(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        """Menghubungkan bot ke voice channel tertentu."""
        ...

    async def move(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        """Memindahkan bot ke voice channel baru."""
        ...

    async def disconnect(self, guild_id: int) -> None:
        """Memutuskan koneksi voice bot pada guild tertentu."""
        ...

    async def shutdown(self) -> None:
        """Menutup dan memutuskan seluruh koneksi voice aktif."""
        ...
