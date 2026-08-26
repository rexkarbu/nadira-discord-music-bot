"""Port interface untuk notifikasi status background playback."""

from typing import Protocol
from uuid import UUID


class PlaybackNotifier(Protocol):
    """Kontrak antarmuka pengiriman notifikasi background playback."""

    async def notify_playback_halted(
        self,
        guild_id: int,
        text_channel_id: int | None,
        operation_id: UUID,
        failed_count: int,
    ) -> None:
        """Mengirim notifikasi ke text channel saat playback dihentikan otomatis karena safety cap.

        Args:
            guild_id: ID guild target.
            text_channel_id: ID channel Discord tujuan jika tersedia.
            operation_id: UUID operation/correlation ID unik untuk tracking.
            failed_count: Jumlah track yang gagal berturut-turut sebelum dihentikan.
        """
        ...
