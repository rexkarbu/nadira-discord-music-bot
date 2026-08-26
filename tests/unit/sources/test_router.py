"""Unit tests untuk SourceRouter URL & search query classifier."""

import pytest

from iwed_bot.application.errors import InvalidPlayQuery
from iwed_bot.application.source_router import SourceRouter
from iwed_bot.ports.sources import SourceClassification


class TestSourceRouter:
    def test_empty_or_whitespace_query_raises_invalid_play_query(self) -> None:
        with pytest.raises(InvalidPlayQuery):
            SourceRouter.classify("")
        with pytest.raises(InvalidPlayQuery):
            SourceRouter.classify("   \n\t  ")

    def test_query_too_long_raises_invalid_play_query(self) -> None:
        long_text = "a" * 501
        with pytest.raises(InvalidPlayQuery):
            SourceRouter.classify(long_text)

    def test_search_text_normal_and_unicode(self) -> None:
        res1 = SourceRouter.classify("  numb   linkin   park  ")
        assert res1.kind == SourceClassification.SEARCH_TEXT
        assert res1.normalized_query == "numb linkin park"

        # Unicode (Japanese, Arabic, Cyrillic)
        res2 = SourceRouter.classify("YOASOBI アイドル")
        assert res2.kind == SourceClassification.SEARCH_TEXT
        assert res2.normalized_query == "YOASOBI アイドル"

        res3 = SourceRouter.classify("فيروز كيفك انت")
        assert res3.kind == SourceClassification.SEARCH_TEXT
        assert res3.normalized_query == "فيروز كيفك انت"

    def test_youtube_watch_single_track(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.cleaned_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert res.target_id == "dQw4w9WgXcQ"

    def test_youtube_watch_with_list_cleans_playlist_param(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&index=1"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.cleaned_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert res.target_id == "dQw4w9WgXcQ"

    def test_youtube_shorts_single_track(self) -> None:
        url = "https://youtube.com/shorts/abc123xyz01"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.cleaned_url == "https://www.youtube.com/watch?v=abc123xyz01"
        assert res.target_id == "abc123xyz01"

    def test_youtube_live_single_track(self) -> None:
        url = "https://www.youtube.com/live/live1234567"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.cleaned_url == "https://www.youtube.com/watch?v=live1234567"
        assert res.target_id == "live1234567"

    def test_youtu_be_short_url(self) -> None:
        url = "https://youtu.be/dQw4w9WgXcQ?t=43"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.cleaned_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert res.target_id == "dQw4w9WgXcQ"

    def test_youtube_playlist_classified_as_playlist(self) -> None:
        url = "https://www.youtube.com/playlist?list=PL1234567890ABCDEF"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_PLAYLIST
        assert res.target_id == "PL1234567890ABCDEF"

    def test_spotify_track(self) -> None:
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.SPOTIFY_TRACK
        assert res.target_id == "4cOdK2wGLETKBW3PvgPWqT"

    def test_spotify_playlist_and_album(self) -> None:
        res1 = SourceRouter.classify("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        assert res1.kind == SourceClassification.SPOTIFY_PLAYLIST_OR_ALBUM

        res2 = SourceRouter.classify("https://open.spotify.com/album/4eLPsYPBmXABThSJ821sqY")
        assert res2.kind == SourceClassification.SPOTIFY_PLAYLIST_OR_ALBUM

    def test_case_insensitive_scheme_handled(self) -> None:
        url = "HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ"
        res = SourceRouter.classify(url)
        assert res.kind == SourceClassification.YOUTUBE_SINGLE_TRACK
        assert res.target_id == "dQw4w9WgXcQ"

    def test_invalid_video_id_rejected(self) -> None:
        # Less than 11 chars or containing illegal chars
        res1 = SourceRouter.classify("https://www.youtube.com/watch?v=short")
        assert res1.kind == SourceClassification.UNSUPPORTED_URL

        res2 = SourceRouter.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ<script>")
        assert res2.kind == SourceClassification.UNSUPPORTED_URL

    def test_hostname_spoofing_rejected(self) -> None:
        res1 = SourceRouter.classify("https://youtube.com.attacker.example/watch?v=dQw4w9WgXcQ")
        assert res1.kind == SourceClassification.UNSUPPORTED_URL

        res2 = SourceRouter.classify("https://open.spotify.com.attacker.example/track/123")
        assert res2.kind == SourceClassification.UNSUPPORTED_URL

    def test_userinfo_url_rejected_and_sanitized(self) -> None:
        res = SourceRouter.classify(
            "https://user:super_secret_password@www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert res.kind == SourceClassification.UNSUPPORTED_URL
        assert "super_secret_password" not in res.normalized_query

    def test_unsupported_domains(self) -> None:
        res1 = SourceRouter.classify("https://soundcloud.com/artist/track")
        assert res1.kind == SourceClassification.UNSUPPORTED_URL

        res2 = SourceRouter.classify("ftp://files.example.com/audio.mp3")
        assert res2.kind == SourceClassification.UNSUPPORTED_URL
