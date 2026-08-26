"""Wavelink YouTube TrackSource prototype adapter."""

import asyncio
import logging
import uuid
from typing import Any

import wavelink

from iwed_bot.application.errors import (
    PlaylistImportDeferred,
    SourceLoadFailed,
    SourceTimeout,
    TrackNotFound,
)
from iwed_bot.domain.models import SourceType, TrackReference
from iwed_bot.ports.sources import TrackSource

logger = logging.getLogger(__name__)


class WavelinkYouTubeSource(TrackSource):
    """Prototype adapter TrackSource menggunakan Wavelink 3.5.2 dan YouTube plugin."""

    def __init__(self, search_timeout: float = 10.0) -> None:
        self.search_timeout = search_timeout

    def _normalize_playable(self, playable: wavelink.Playable) -> TrackReference:
        """Mengonversi wavelink.Playable menjadi TrackReference domain immutable."""
        author = getattr(playable, "author", "") or ""
        title = getattr(playable, "title", "Unknown Title") or "Unknown Title"
        artists = (author,) if author else ()
        search_hint = f"{author} - {title}" if author else title
        is_stream = bool(getattr(playable, "is_stream", False))
        length = getattr(playable, "length", 0)
        duration_ms = None if is_stream else max(0, int(length))

        return TrackReference(
            id=uuid.uuid4(),
            source_type=SourceType.YOUTUBE,
            source_id=getattr(playable, "identifier", None) or None,
            source_uri=getattr(playable, "uri", None) or None,
            search_hint=search_hint,
            title=title,
            artists=artists,
            duration_ms=duration_ms,
            thumbnail_url=getattr(playable, "artwork", None) or None,
            canonical_url=getattr(playable, "uri", None) or None,
            is_stream=is_stream,
        )

    async def search(self, query: str, limit: int = 5) -> tuple[TrackReference, ...]:
        """Mencari track via Wavelink ytmsearch: / ytsearch:."""
        try:
            async with asyncio.timeout(self.search_timeout):
                # Prioritaskan YouTube Music search (ytmsearch), fallback ytsearch jika kosong
                search_query = (
                    f"ytmsearch:{query}"
                    if not query.startswith(("ytmsearch:", "ytsearch:"))
                    else query
                )
                results: Any = await wavelink.Playable.search(search_query)

                if isinstance(results, wavelink.Playlist):
                    raise PlaylistImportDeferred()

                if not results and search_query.startswith("ytmsearch:"):
                    # Fallback ke ytsearch standar jika ytmsearch tidak menghasilkan apa pun
                    results = await wavelink.Playable.search(f"ytsearch:{query}")
                    if isinstance(results, wavelink.Playlist):
                        raise PlaylistImportDeferred()

                if not results:
                    raise TrackNotFound()

                playables = results[:limit] if isinstance(results, list) else [results]
                return tuple(
                    self._normalize_playable(p)
                    for p in playables
                    if isinstance(p, wavelink.Playable)
                )

        except TimeoutError as err:
            raise SourceTimeout() from err
        except (TrackNotFound, PlaylistImportDeferred):
            raise
        except Exception as err:
            logger.warning(
                "Error saat mencari track via Wavelink",
                extra={"error_type": type(err).__name__},
            )
            raise SourceLoadFailed("Gagal memuat hasil pencarian dari penyedia audio.") from err

    async def resolve_single_url(self, url: str) -> TrackReference:
        """Me-resolve single track YouTube URL menjadi TrackReference."""
        try:
            async with asyncio.timeout(self.search_timeout):
                results: Any = await wavelink.Playable.search(url)

                if isinstance(results, wavelink.Playlist):
                    raise PlaylistImportDeferred()

                if not results:
                    raise TrackNotFound()

                first_playable = results[0] if isinstance(results, list) else results
                if not isinstance(first_playable, wavelink.Playable):
                    raise TrackNotFound()

                return self._normalize_playable(first_playable)

        except TimeoutError as err:
            raise SourceTimeout() from err
        except (TrackNotFound, PlaylistImportDeferred):
            raise
        except Exception as err:
            logger.warning(
                "Error saat me-resolve URL via Wavelink",
                extra={"error_type": type(err).__name__},
            )
            raise SourceLoadFailed("Gagal me-resolve tautan dari penyedia audio.") from err
