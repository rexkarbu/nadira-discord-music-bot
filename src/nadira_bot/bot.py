"""Implementasi Bot Discord Nadira utama dengan integrasi Wavelink dan lifecycle management."""

import asyncio
import contextlib
import logging
import math
import random
from datetime import UTC, datetime
from typing import Any

import discord
import wavelink
from discord.ext import commands

from nadira_bot.settings import Settings

logger = logging.getLogger(__name__)


class NadiraBot(commands.Bot):
    """Subclass Discord Bot utama (Nadira) yang mengelola lifecycle, commands, dan Lavalink."""

    def __init__(self, settings: Settings) -> None:
        # Konfigurasi intent minimum yang diperlukan: guilds dan voice_states
        # message_content secara eksplisit tidak diaktifkan demi privasi dan efisiensi
        intents = discord.Intents.default()
        intents.message_content = False
        intents.voice_states = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",  # Prefix dummy; bot hanya menggunakan native slash commands
            intents=intents,
            application_id=settings.DISCORD_APPLICATION_ID,
            help_command=None,
        )

        self.settings: Settings = settings
        self.start_time: datetime = datetime.now(UTC)
        self.lavalink_connected: bool = False
        self._lavalink_node: wavelink.Node | None = None
        self._lavalink_ready_event: asyncio.Event = asyncio.Event()
        self._reconnect_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        """Lifecycle hook startup: memuat ekstensi, Lavalink, dan sync slash commands."""
        logger.info("Menjalankan setup hook Nadira bot...")

        # 1. Muat ekstensi / cog slash commands
        await self.load_extension("nadira_bot.commands.health")
        logger.info("Ekstensi slash command 'health' berhasil dimuat.")

        # 2. Inisialisasi koneksi awal ke Lavalink v4
        await self._init_lavalink()

        # 3. Sinkronisasi slash commands (Test Guild vs Global Sync)
        # Catatan: setup_hook hanya dijalankan SEKALI saat startup, mencegah rate limit di on_ready
        if self.settings.DISCORD_TEST_GUILD_ID:
            test_guild = discord.Object(id=self.settings.DISCORD_TEST_GUILD_ID)
            self.tree.copy_global_to(guild=test_guild)
            synced_commands = await self.tree.sync(guild=test_guild)
            logger.info(
                "Slash commands berhasil disinkronkan ke test guild",
                extra={
                    "guild_id": self.settings.DISCORD_TEST_GUILD_ID,
                    "command_count": len(synced_commands),
                    "command_names": [cmd.name for cmd in synced_commands],
                },
            )
        else:
            synced_commands = await self.tree.sync()
            logger.info(
                "Slash commands berhasil disinkronkan secara global",
                extra={
                    "command_count": len(synced_commands),
                    "command_names": [cmd.name for cmd in synced_commands],
                },
            )

    async def _init_lavalink(self) -> None:
        """Mencoba registrasi awal node Wavelink dengan retries=0 untuk degraded startup."""
        node = wavelink.Node(
            uri=self.settings.LAVALINK_URI,
            password=self.settings.LAVALINK_PASSWORD.get_secret_value(),
            inactive_player_timeout=self.settings.IDLE_DISCONNECT_SECONDS,
            retries=0,
        )
        self._lavalink_node = node

        try:
            await wavelink.Pool.connect(nodes=[node], client=self)
            if node.status == wavelink.NodeStatus.CONNECTED:
                self.lavalink_connected = True
                self._lavalink_ready_event.set()
                logger.info(
                    "Inisialisasi pool Wavelink ke node Lavalink berhasil",
                    extra={"lavalink_uri": self.settings.LAVALINK_URI},
                )
            else:
                self.lavalink_connected = False
                self._lavalink_ready_event.clear()
                logger.warning(
                    "Lavalink node belum siap saat startup. Mode degraded aktif.",
                    extra={"lavalink_uri": self.settings.LAVALINK_URI},
                )
                self._start_reconnect_supervisor()
        except Exception as err:
            self.lavalink_connected = False
            self._lavalink_ready_event.clear()
            logger.warning(
                "Lavalink node tidak dapat dijangkau saat startup. Mode degraded aktif.",
                extra={
                    "lavalink_uri": self.settings.LAVALINK_URI,
                    "error_reason": str(err),
                },
            )
            self._start_reconnect_supervisor()

    def _start_reconnect_supervisor(self) -> None:
        """Memulai satu reconnect supervisor task jika belum ada yang berjalan."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_supervisor_loop())

    async def _reconnect_supervisor_loop(self) -> None:
        """Supervisor loop yang memanggil Pool.reconnect() dengan backoff dan jitter."""
        backoff_seconds = 5.0
        max_backoff = 60.0

        while not self._lavalink_ready_event.is_set() and not self.is_closed():
            try:
                jitter = random.uniform(0.5, 2.0)
                delay = backoff_seconds + jitter
                logger.info(
                    "Supervisor menunggu untuk reconnect Wavelink Pool...",
                    extra={"retry_delay_seconds": round(delay, 2)},
                )
                await asyncio.sleep(delay)
                if self._lavalink_ready_event.is_set() or self.is_closed():
                    break

                logger.info("Supervisor mengeksekusi Pool.reconnect()...")
                await wavelink.Pool.reconnect()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.debug(
                    "Supervisor Pool.reconnect() gagal, mencoba kembali nanti",
                    extra={"error": str(err)},
                )
                backoff_seconds = min(backoff_seconds * 2, max_backoff)

    async def on_ready(self) -> None:
        """Event saat koneksi gateway Discord berhasil terjalin."""
        logger.info(
            "Discord Gateway terhubung dan Nadira Bot siap beroperasi",
            extra={
                "username": str(self.user),
                "bot_id": self.user.id if self.user else None,
                "latency_ms": round(self.latency * 1000, 2) if self.latency else None,
                "guild_count": len(self.guilds),
            },
        )

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        """Event handler ketika node Wavelink siap digunakan."""
        self.lavalink_connected = True
        self._lavalink_ready_event.set()
        logger.info(
            "Wavelink node telah siap dan aktif",
            extra={"node_status": "ready", "resumed": payload.resumed},
        )

    async def on_wavelink_node_disconnected(
        self, _payload: wavelink.NodeDisconnectedEventPayload
    ) -> None:
        """Event handler ketika koneksi ke node Wavelink terputus."""
        self.lavalink_connected = False
        self._lavalink_ready_event.clear()
        logger.warning(
            "Koneksi ke Wavelink node terputus",
            extra={"node_status": "disconnected"},
        )
        if not self.is_closed():
            self._start_reconnect_supervisor()

    def get_health_status(self) -> dict[str, Any]:
        """Menghasilkan data terstruktur metrik kesehatan bot untuk monitoring dan /health."""
        now = datetime.now(UTC)
        uptime_delta = now - self.start_time
        total_seconds = int(uptime_delta.total_seconds())

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        uptime_parts = []
        if days > 0:
            uptime_parts.append(f"{days}h")
        if hours > 0 or days > 0:
            uptime_parts.append(f"{hours}j")
        if minutes > 0 or hours > 0 or days > 0:
            uptime_parts.append(f"{minutes}m")
        uptime_parts.append(f"{seconds}d")
        uptime_str = " ".join(uptime_parts)

        # Periksa node Wavelink aktif jika ada
        is_node_alive = False
        try:
            node = wavelink.Pool.get_node()
            if node and node.status == wavelink.NodeStatus.CONNECTED:
                is_node_alive = True
        except Exception:
            is_node_alive = self.lavalink_connected

        latency_ms = None
        if self.latency and not math.isnan(self.latency):
            latency_ms = round(self.latency * 1000, 1)

        startup_mode = "Normal" if is_node_alive else "Degraded (Audio Offline)"

        return {
            "bot_status": "Online" if is_node_alive else "Degraded",
            "uptime_seconds": total_seconds,
            "uptime_str": uptime_str,
            "discord_latency_ms": latency_ms,
            "lavalink_connected": is_node_alive,
            "startup_mode": startup_mode,
            "app_version": "0.1.0 • DAVE-compatible stack",
        }

    async def close(self) -> None:
        """Graceful shutdown: membatalkan reconnect supervisor dan menutup pool Wavelink."""
        logger.info("Memulai proses graceful shutdown Nadira bot...")

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        try:
            await wavelink.Pool.close()
            logger.info("Koneksi Wavelink pool berhasil ditutup.")
        except Exception as err:
            logger.warning(
                "Terjadi error saat menutup Wavelink pool",
                extra={"error": str(err)},
            )

        await super().close()
        logger.info("Proses bot Discord Nadira berhasil dihentikan secara bersih.")
