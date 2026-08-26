"""Unit tests untuk WavelinkYouTubeSource adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from iwed_bot.application.errors import (
    PlaylistImportDeferred,
    SourceLoadFailed,
    SourceTimeout,
    TrackNotFound,
)
from iwed_bot.domain.models import SourceType
from iwed_bot.infrastructure.sources.wavelink_youtube import WavelinkYouTubeSource


def make_mock_playable(
    title: str = "Numb",
    author: str = "Linkin Park",
    length: int = 185000,
    uri: str = "https://www.youtube.com/watch?v=kXYiU_JCYtU",
    identifier: str = "kXYiU_JCYtU",
    artwork: str = "https://img.youtube.com/vi/kXYiU_JCYtU/hqdefault.jpg",
    is_stream: bool = False,
) -> MagicMock:
    p = MagicMock(spec=wavelink.Playable)
    p.title = title
    p.author = author
    p.length = length
    p.uri = uri
    p.identifier = identifier
    p.artwork = artwork
    p.is_stream = is_stream
    return p


class TestWavelinkYouTubeSource:
    @pytest.mark.asyncio
    async def test_search_success_mapping(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)
        mock_p1 = make_mock_playable(title="Song 1", author="Artist 1")
        mock_p2 = make_mock_playable(title="Song 2", author="Artist 2")

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_p1, mock_p2]
            results = await source.search("song", limit=5)

            assert len(results) == 2
            assert results[0].title == "Song 1"
            assert results[0].artists == ("Artist 1",)
            assert results[0].source_type == SourceType.YOUTUBE
            assert results[0].duration_ms == 185000
            assert results[0].canonical_url == "https://www.youtube.com/watch?v=kXYiU_JCYtU"
            assert results[0].search_hint == "Artist 1 - Song 1"

    @pytest.mark.asyncio
    async def test_search_stream_duration_is_none(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)
        mock_live = make_mock_playable(title="Live Stream", is_stream=True, length=0)

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_live]
            results = await source.search("live stream", limit=1)

            assert len(results) == 1
            assert results[0].is_stream is True
            assert results[0].duration_ms is None

    @pytest.mark.asyncio
    async def test_search_zero_results_raises_track_not_found(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            with pytest.raises(TrackNotFound):
                await source.search("nonexistent song xyz")

    @pytest.mark.asyncio
    async def test_search_playlist_returns_deferred_error(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)
        mock_playlist = MagicMock(spec=wavelink.Playlist)

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_playlist
            with pytest.raises(PlaylistImportDeferred):
                await source.search("some query that returned playlist")

    @pytest.mark.asyncio
    async def test_search_timeout_raises_source_timeout(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=0.01)

        async def slow_search(*_args: object, **_kwargs: object) -> list[object]:
            import asyncio

            await asyncio.sleep(0.1)
            return []

        with (
            patch("wavelink.Playable.search", side_effect=slow_search),
            pytest.raises(SourceTimeout),
        ):
            await source.search("slow song")

    @pytest.mark.asyncio
    async def test_search_exception_raises_source_load_failed(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)

        with (
            patch("wavelink.Playable.search", side_effect=RuntimeError("Lavalink error")),
            pytest.raises(SourceLoadFailed),
        ):
            await source.search("error query")

    @pytest.mark.asyncio
    async def test_resolve_single_url_success(self) -> None:
        source = WavelinkYouTubeSource(search_timeout=5.0)
        mock_p = make_mock_playable(title="Direct Video", author="Channel")

        with patch("wavelink.Playable.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [mock_p]
            track = await source.resolve_single_url("https://www.youtube.com/watch?v=abc")
            assert track.title == "Direct Video"
            assert track.artists == ("Channel",)
            assert track.source_type == SourceType.YOUTUBE


class TestCompliantSourceUnavailableAdapter:
    @pytest.mark.asyncio
    async def test_compliant_adapter_raises_compliant_source_unavailable(self) -> None:
        from iwed_bot.application.errors import CompliantSourceUnavailable
        from iwed_bot.infrastructure.sources import CompliantSourceUnavailableAdapter

        adapter = CompliantSourceUnavailableAdapter()
        with pytest.raises(CompliantSourceUnavailable):
            await adapter.search("any query")

        with pytest.raises(CompliantSourceUnavailable):
            await adapter.resolve_single_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
