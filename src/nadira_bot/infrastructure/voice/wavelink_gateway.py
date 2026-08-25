"""Wavelink 3.5.2 Voice Gateway adapter untuk Nadira Discord Music Bot.

Mengimplementasikan VoiceGateway port menggunakan Wavelink.Player dan discord.py.
"""

import contextlib
import logging
from typing import TYPE_CHECKING

import discord
import wavelink

from nadira_bot.application.errors import (
    UnexpectedVoiceClient,
    UnsupportedVoiceChannel,
    VoiceConnectionFailed,
    VoiceDisconnectFailed,
    VoiceMoveFailed,
)
from nadira_bot.ports.voice import VoiceConnectionSnapshot, VoiceGateway

if TYPE_CHECKING:
    from nadira_bot.bot import NadiraBot

logger = logging.getLogger(__name__)


class WavelinkVoiceGateway(VoiceGateway):
    """Adapter yang menghubungkan layer aplikasi ke Wavelink 3.5.2 Player API."""

    def __init__(self, bot: "NadiraBot | discord.Client") -> None:
        self.bot = bot

    async def is_available(self) -> bool:
        """Memeriksa apakah ada node Wavelink aktif dan terhubung."""
        try:
            node = wavelink.Pool.get_node()
            return node is not None and node.status == wavelink.NodeStatus.CONNECTED
        except Exception:
            return False

    async def get_connection(self, guild_id: int) -> VoiceConnectionSnapshot | None:
        """Mengambil status koneksi voice bot pada guild."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None

        voice_client = guild.voice_client
        if voice_client is None:
            return None

        if not isinstance(voice_client, wavelink.Player):
            raise UnexpectedVoiceClient(
                f"Voice client pada guild {guild_id} bertipe {type(voice_client)}, "
                "bukan wavelink.Player."
            )

        if voice_client.channel is None or not voice_client.connected:
            return None

        return VoiceConnectionSnapshot(
            guild_id=guild_id,
            channel_id=voice_client.channel.id,
            is_connected=voice_client.connected,
        )

    async def connect(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        """Menghubungkan bot ke voice channel menggunakan Wavelink.Player."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise VoiceConnectionFailed(f"Guild dengan ID {guild_id} tidak ditemukan.")

        channel = guild.get_channel(channel_id)
        if channel is None:
            raise VoiceConnectionFailed(f"Channel dengan ID {channel_id} tidak ditemukan.")

        if isinstance(channel, discord.StageChannel):
            raise UnsupportedVoiceChannel(
                "Nadira saat ini tidak mendukung Stage Channel. Gunakan Voice Channel standar."
            )

        if not isinstance(channel, discord.VoiceChannel):
            raise UnsupportedVoiceChannel(
                f"Channel {channel_id} bukan merupakan discord.VoiceChannel."
            )

        # Periksa jika player sudah terdaftar sebelumnya
        existing_client = guild.voice_client
        if existing_client is not None:
            if not isinstance(existing_client, wavelink.Player):
                raise UnexpectedVoiceClient("Voice client aktif bukan merupakan wavelink.Player.")

            if existing_client.connected:
                if existing_client.channel and existing_client.channel.id == channel_id:
                    return VoiceConnectionSnapshot(
                        guild_id=guild_id,
                        channel_id=channel_id,
                        is_connected=True,
                    )

                # Jika connected di channel lain, gunakan move_to
                try:
                    await existing_client.move_to(channel)
                except Exception as err:
                    raise VoiceMoveFailed("Gagal memindahkan bot ke voice channel baru.") from err

                if (
                    not existing_client.connected
                    or not existing_client.channel
                    or existing_client.channel.id != channel_id
                ):
                    raise VoiceMoveFailed("Koneksi player tidak valid setelah pemindahan channel.")

                return VoiceConnectionSnapshot(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    is_connected=existing_client.connected,
                )
            # Player ada tapi disconnected: bersihkan stale player terlebih dahulu
            with contextlib.suppress(Exception):
                await existing_client.disconnect()

        # Lakukan inisiasi koneksi baru menggunakan Wavelink.Player
        try:
            player: wavelink.Player = await channel.connect(
                cls=wavelink.Player,
                timeout=timeout,
                reconnect=True,
                self_deaf=True,
            )
            if not player.connected:
                raise VoiceConnectionFailed("Player gagal terhubung ke voice gateway.")

            return VoiceConnectionSnapshot(
                guild_id=guild_id,
                channel_id=channel.id,
                is_connected=player.connected,
            )
        except TimeoutError as err:
            raise VoiceConnectionFailed("Koneksi ke voice channel timed out (10s).") from err
        except Exception as err:
            if isinstance(err, (VoiceConnectionFailed, VoiceMoveFailed, UnexpectedVoiceClient)):
                raise
            raise VoiceConnectionFailed(
                "Gagal menghubungkan player Wavelink ke voice channel."
            ) from err

    async def move(
        self, guild_id: int, channel_id: int, timeout: float = 10.0
    ) -> VoiceConnectionSnapshot:
        """Memindahkan bot ke voice channel baru."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise VoiceMoveFailed(f"Guild dengan ID {guild_id} tidak ditemukan.")

        channel = guild.get_channel(channel_id)
        if channel is None:
            raise VoiceMoveFailed(f"Channel dengan ID {channel_id} tidak ditemukan.")

        if isinstance(channel, discord.StageChannel):
            raise UnsupportedVoiceChannel(
                "Nadira saat ini tidak mendukung Stage Channel. Gunakan Voice Channel standar."
            )

        if not isinstance(channel, discord.VoiceChannel):
            raise UnsupportedVoiceChannel(
                f"Channel {channel_id} bukan merupakan discord.VoiceChannel."
            )

        voice_client = guild.voice_client
        if voice_client is None:
            return await self.connect(guild_id, channel_id, timeout=timeout)

        if not isinstance(voice_client, wavelink.Player):
            raise UnexpectedVoiceClient("Voice client aktif bukan merupakan wavelink.Player.")

        if not voice_client.connected:
            # Jangan pernah memanggil move_to pada disconnected player
            with contextlib.suppress(Exception):
                await voice_client.disconnect()
            return await self.connect(guild_id, channel_id, timeout=timeout)

        try:
            await voice_client.move_to(channel)
        except Exception as err:
            raise VoiceMoveFailed("Gagal memindahkan player ke voice channel baru.") from err

        # Verifikasi status aktual player dan channel setelah move
        if (
            not voice_client.connected
            or not voice_client.channel
            or voice_client.channel.id != channel_id
        ):
            raise VoiceMoveFailed("Player tidak terhubung ke channel tujuan setelah dipindahkan.")

        return VoiceConnectionSnapshot(
            guild_id=guild_id,
            channel_id=voice_client.channel.id,
            is_connected=voice_client.connected,
        )

    async def disconnect(self, guild_id: int) -> None:
        """Memutuskan koneksi voice player pada guild."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        voice_client = guild.voice_client
        if voice_client is None:
            return

        if not isinstance(voice_client, wavelink.Player):
            raise UnexpectedVoiceClient(
                f"Voice client pada guild {guild_id} bertipe {type(voice_client)}, "
                "bukan wavelink.Player."
            )

        try:
            # Wavelink 3.5.2: disconnect() sudah membersihkan player dan voice connection
            await voice_client.disconnect()
        except Exception as err:
            logger.warning(
                "Terjadi exception saat memanggil player.disconnect()",
                extra={"guild_id": guild_id, "error_type": type(err).__name__},
            )
            raise VoiceDisconnectFailed("Gagal memutuskan koneksi player.") from err

    async def shutdown(self) -> None:
        """Memutuskan seluruh koneksi voice player aktif secara aman sebelum pool ditutup."""
        for guild in self.bot.guilds:
            voice_client = guild.voice_client
            if isinstance(voice_client, wavelink.Player):
                try:
                    await voice_client.disconnect()
                except Exception as err:
                    logger.warning(
                        "Gagal memutuskan koneksi voice client saat gateway shutdown",
                        extra={"guild_id": guild.id, "error_type": type(err).__name__},
                    )
