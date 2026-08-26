"""Pure router dan classifier untuk query pencarian dan URL audio."""

import re
import urllib.parse

from iwed_bot.application.errors import InvalidPlayQuery
from iwed_bot.ports.sources import QueryClassification, SourceClassification

_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
_SPOTIFY_HOSTS = frozenset({"open.spotify.com", "spotify.com", "www.spotify.com"})
_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")


class SourceRouter:
    """Classifier murni query pencarian, single track URL, playlist, dan unsupported."""

    @staticmethod
    def classify(raw_query: str) -> QueryClassification:
        """Mengklasifikasikan query input pengguna menjadi QueryClassification DTO.

        Raises:
            InvalidPlayQuery: Jika query kosong, whitespace-only, atau > 500 karakter.
        """
        if not raw_query:
            raise InvalidPlayQuery("Query pencarian tidak boleh kosong.")

        normalized_query = re.sub(r"\s+", " ", raw_query.strip())
        if not normalized_query or len(normalized_query) > 500:
            raise InvalidPlayQuery(
                f"Panjang query harus 1-500 karakter (diberikan: {len(normalized_query)})."
            )

        # 1. Cek jika input menyerupai URL berdasarkan scheme (case-insensitive)
        if _SCHEME_PATTERN.match(normalized_query):
            parsed = urllib.parse.urlsplit(normalized_query)

            # Tolak URL dengan userinfo/credentials tanpa mengekspos credential
            if parsed.username or parsed.password or ("@" in (parsed.netloc or "")):
                return QueryClassification(
                    kind=SourceClassification.UNSUPPORTED_URL,
                    normalized_query="<credential_url_sanitized>",
                    cleaned_url=None,
                )

            scheme = parsed.scheme.lower()
            if scheme not in ("http", "https"):
                return QueryClassification(
                    kind=SourceClassification.UNSUPPORTED_URL,
                    normalized_query=normalized_query,
                    cleaned_url=None,
                )

            hostname = (parsed.hostname or "").lower()

            # 1a. YouTube standard hosts
            if hostname in _YOUTUBE_HOSTS:
                path = parsed.path.rstrip("/")
                query_params = urllib.parse.parse_qs(parsed.query)

                # /watch?v=...
                if path == "/watch" and "v" in query_params:
                    raw_v = query_params["v"][0].strip()
                    if _YOUTUBE_ID_PATTERN.match(raw_v):
                        cleaned = f"https://www.youtube.com/watch?v={raw_v}"
                        return QueryClassification(
                            kind=SourceClassification.YOUTUBE_SINGLE_TRACK,
                            normalized_query=cleaned,
                            cleaned_url=cleaned,
                            target_id=raw_v,
                        )
                    return QueryClassification(
                        kind=SourceClassification.UNSUPPORTED_URL,
                        normalized_query=normalized_query,
                        cleaned_url=None,
                    )

                # /playlist?list=...
                if path == "/playlist" and "list" in query_params:
                    playlist_id = query_params["list"][0].strip()
                    if playlist_id:
                        return QueryClassification(
                            kind=SourceClassification.YOUTUBE_PLAYLIST,
                            normalized_query=normalized_query,
                            cleaned_url=normalized_query,
                            target_id=playlist_id,
                        )

                # /shorts/<video_id>
                if path.startswith("/shorts/"):
                    parts = [p for p in path.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "shorts":
                        video_id = parts[1].strip()
                        if _YOUTUBE_ID_PATTERN.match(video_id):
                            cleaned = f"https://www.youtube.com/watch?v={video_id}"
                            return QueryClassification(
                                kind=SourceClassification.YOUTUBE_SINGLE_TRACK,
                                normalized_query=cleaned,
                                cleaned_url=cleaned,
                                target_id=video_id,
                            )
                    return QueryClassification(
                        kind=SourceClassification.UNSUPPORTED_URL,
                        normalized_query=normalized_query,
                        cleaned_url=None,
                    )

                # /live/<video_id>
                if path.startswith("/live/"):
                    parts = [p for p in path.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "live":
                        video_id = parts[1].strip()
                        if _YOUTUBE_ID_PATTERN.match(video_id):
                            cleaned = f"https://www.youtube.com/watch?v={video_id}"
                            return QueryClassification(
                                kind=SourceClassification.YOUTUBE_SINGLE_TRACK,
                                normalized_query=cleaned,
                                cleaned_url=cleaned,
                                target_id=video_id,
                            )
                    return QueryClassification(
                        kind=SourceClassification.UNSUPPORTED_URL,
                        normalized_query=normalized_query,
                        cleaned_url=None,
                    )

                return QueryClassification(
                    kind=SourceClassification.UNSUPPORTED_URL,
                    normalized_query=normalized_query,
                    cleaned_url=None,
                )

            # 1b. youtu.be short link
            if hostname == "youtu.be":
                path_parts = [p for p in parsed.path.split("/") if p]
                if path_parts:
                    video_id = path_parts[0].strip()
                    if _YOUTUBE_ID_PATTERN.match(video_id):
                        cleaned = f"https://www.youtube.com/watch?v={video_id}"
                        return QueryClassification(
                            kind=SourceClassification.YOUTUBE_SINGLE_TRACK,
                            normalized_query=cleaned,
                            cleaned_url=cleaned,
                            target_id=video_id,
                        )

                return QueryClassification(
                    kind=SourceClassification.UNSUPPORTED_URL,
                    normalized_query=normalized_query,
                    cleaned_url=None,
                )

            # 1c. Spotify hosts
            if hostname in _SPOTIFY_HOSTS:
                path = parsed.path.rstrip("/")
                if path.startswith("/track/"):
                    parts = [p for p in path.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "track":
                        track_id = parts[1].strip()
                        return QueryClassification(
                            kind=SourceClassification.SPOTIFY_TRACK,
                            normalized_query=normalized_query,
                            cleaned_url=normalized_query,
                            target_id=track_id,
                        )
                if path.startswith(("/playlist/", "/album/", "/artist/")):
                    return QueryClassification(
                        kind=SourceClassification.SPOTIFY_PLAYLIST_OR_ALBUM,
                        normalized_query=normalized_query,
                        cleaned_url=normalized_query,
                    )

                return QueryClassification(
                    kind=SourceClassification.UNSUPPORTED_URL,
                    normalized_query=normalized_query,
                    cleaned_url=None,
                )

            # Other domains
            return QueryClassification(
                kind=SourceClassification.UNSUPPORTED_URL,
                normalized_query=normalized_query,
                cleaned_url=None,
            )

        # 2. Bukan URL -> Search text
        return QueryClassification(
            kind=SourceClassification.SEARCH_TEXT,
            normalized_query=normalized_query,
        )
