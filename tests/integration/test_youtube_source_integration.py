"""Integration tests untuk live YouTube resolution via Lavalink dan Wavelink 3.5.2."""

import asyncio
import json
import os
import urllib.request
from typing import TYPE_CHECKING, Any, cast

import pytest
import wavelink

from iwed_bot.infrastructure.sources.prototype.wavelink_youtube import WavelinkYouTubeSource

if TYPE_CHECKING:
    import discord


class StubDiscordClient:
    """Stub minimal Discord client untuk menyediakan user.id bagi handshake Wavelink."""

    def __init__(self, bot_id: int = 123456789012345678) -> None:
        self.user = type("User", (), {"id": bot_id})()

    def dispatch(self, _event_name: str, *_args: Any, **_kwargs: Any) -> None:
        """Stub event dispatcher yang mengabaikan event internal."""
        pass


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LAVALINK_INTEGRATION") != "1"
    or os.getenv("RUN_YOUTUBE_LIVE_INTEGRATION") != "1",
    reason="Live YouTube integration memerlukan kedua environment variable aktif",
)
@pytest.mark.asyncio
async def test_live_youtube_plugin_and_resolution() -> None:
    """Memverifikasi plugin YouTube 1.18.2 terpasang dan live search YouTube berhasil."""
    lavalink_uri = os.getenv("LAVALINK_URI", "http://localhost:2333")
    lavalink_password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    # 1. Verifikasi /v4/info via HTTP GET berotentikasi di thread terpisah
    req = urllib.request.Request(
        f"{lavalink_uri}/v4/info",
        headers={"Authorization": lavalink_password},
    )

    def _fetch_info() -> dict[str, Any]:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            assert resp.status == 200
            return json.loads(resp.read().decode("utf-8"))

    info_data = await asyncio.to_thread(_fetch_info)

    plugins = info_data.get("plugins", [])
    youtube_plugin = next(
        (p for p in plugins if p.get("name") == "youtube-plugin"),
        None,
    )
    assert youtube_plugin is not None, "youtube-plugin tidak ditemukan di endpoint /v4/info"
    assert youtube_plugin.get("version") == "1.18.2", (
        f"Versi youtube-plugin bukan 1.18.2, ditemukan: {youtube_plugin.get('version')}"
    )

    # 2. Connect ke Wavelink Pool
    stub_client = cast("discord.Client", StubDiscordClient())
    node = wavelink.Node(
        uri=lavalink_uri,
        password=lavalink_password,
        retries=3,
        inactive_player_timeout=300,
    )

    try:
        async with asyncio.timeout(15.0):
            await wavelink.Pool.connect(nodes=[node], client=stub_client)
            for _ in range(30):
                if node.status == wavelink.NodeStatus.CONNECTED:
                    break
                await asyncio.sleep(0.5)

            assert node.status == wavelink.NodeStatus.CONNECTED

            # 3. Eksekusi search dan resolve via WavelinkYouTubeSource adapter
            yt_source = WavelinkYouTubeSource(search_timeout=15.0)

            # 3a. Search text
            search_results = await yt_source.search("Never Gonna Give You Up", limit=3)
            assert len(search_results) > 0
            first_track = search_results[0]
            assert first_track.title is not None
            assert len(first_track.title) > 0
            assert first_track.duration_ms is not None
            assert first_track.duration_ms > 0
            assert first_track.canonical_url is not None

            # 3b. Resolve single video URL
            resolved = await yt_source.resolve_single_url(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )
            assert resolved.title is not None
            assert len(resolved.title) > 0
            assert resolved.duration_ms is not None
            assert resolved.duration_ms > 0
    finally:
        await wavelink.Pool.close()
