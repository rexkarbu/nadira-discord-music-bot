"""Integration tests yang terhubung ke container Lavalink nyata."""

import asyncio
import os
from typing import TYPE_CHECKING, Any, cast

import pytest
import wavelink

if TYPE_CHECKING:
    import discord


class StubDiscordClient:
    """Stub minimal Discord client untuk menyediakan user.id bagi handshake Wavelink."""

    def __init__(self, bot_id: int = 123456789012345678) -> None:
        self.user = type("User", (), {"id": bot_id})()

    def dispatch(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        """Stub event dispatcher yang mengabaikan event internal."""
        pass


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LAVALINK_INTEGRATION") != "1",
    reason="Pengujian live Lavalink memerlukan $env:RUN_LAVALINK_INTEGRATION='1'",
)
@pytest.mark.asyncio
async def test_live_lavalink_node_connection() -> None:
    """Memverifikasi koneksi nyata ke container Lavalink v4 yang sedang berjalan."""
    lavalink_uri = os.getenv("LAVALINK_URI", "http://localhost:2333")
    lavalink_password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    stub_client = cast("discord.Client", StubDiscordClient())
    node = wavelink.Node(
        uri=lavalink_uri,
        password=lavalink_password,
        retries=3,
        inactive_player_timeout=300,
    )

    try:
        # Batasi waktu tunggu handshake ke node maksimum 10 detik
        async with asyncio.timeout(10.0):
            await wavelink.Pool.connect(nodes=[node], client=stub_client)
            # Tunggu hingga status node terkonfirmasi CONNECTED
            for _ in range(20):
                if node.status == wavelink.NodeStatus.CONNECTED:
                    break
                await asyncio.sleep(0.5)

            assert node.status == wavelink.NodeStatus.CONNECTED
            assert wavelink.Pool.get_node() is not None
    finally:
        # Selalu bersihkan resource koneksi node
        await wavelink.Pool.close()
