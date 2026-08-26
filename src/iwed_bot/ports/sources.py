"""Port interfaces dan DTO untuk resolusi dan pencarian track audio."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from iwed_bot.domain.models import TrackReference


class SourceClassification(StrEnum):
    """Klasifikasi tipe input query atau URL yang diterima bot."""

    SEARCH_TEXT = "search_text"
    YOUTUBE_SINGLE_TRACK = "youtube_single_track"
    YOUTUBE_PLAYLIST = "youtube_playlist"
    SPOTIFY_TRACK = "spotify_track"
    SPOTIFY_PLAYLIST_OR_ALBUM = "spotify_playlist_or_album"
    UNSUPPORTED_URL = "unsupported_url"


@dataclass(frozen=True, slots=True)
class QueryClassification:
    """Hasil klasifikasi query input atau URL."""

    kind: SourceClassification
    normalized_query: str
    cleaned_url: str | None = None
    target_id: str | None = None


class TrackSource(Protocol):
    """Kontrak antarmuka penyedia metadata track audio."""

    async def search(self, query: str, limit: int = 5) -> tuple[TrackReference, ...]:
        """Mencari track berdasarkan query teks.

        Args:
            query: Query teks yang sudah dinormalisasi.
            limit: Jumlah maksimal kandidat yang dikembalikan (default 5).

        Returns:
            Tuple of TrackReference kandidat hasil pencarian.

        Raises:
            TrackNotFound: Jika tidak ada hasil pencarian yang playable.
            SourceTimeout: Jika batas waktu pencarian terlampaui.
            SourceLoadFailed: Jika terjadi kesalahan vendor saat memuat hasil pencarian.
        """
        ...

    async def resolve_single_url(self, url: str) -> TrackReference:
        """Me-resolve direct URL video tunggal menjadi TrackReference.

        Args:
            url: URL video tunggal YouTube yang sudah dibersihkan.

        Returns:
            TrackReference dari track yang di-resolve.

        Raises:
            TrackNotFound: Jika video tidak ditemukan / tidak tersedia.
            PlaylistImportDeferred: Jika URL mengarah ke playlist.
            SourceTimeout: Jika batas waktu resolusi terlampaui.
            SourceLoadFailed: Jika terjadi kesalahan vendor saat me-resolve video.
        """
        ...
