"""Implementasi Bot Discord Iwed utama dengan integrasi Wavelink dan lifecycle management."""

import asyncio
import contextlib
import logging
import math
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord
import wavelink
from discord.ext import commands

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.application.play_service import PlayRequestService
from iwed_bot.application.playback_coordinator import PlaybackCoordinator
from iwed_bot.application.queue_control import QueueControlService
from iwed_bot.application.voice import VoiceSessionService
from iwed_bot.infrastructure.playback.wavelink_gateway import WavelinkPlaybackGateway
from iwed_bot.infrastructure.repositories.memory import InMemoryQueueRepository
from iwed_bot.infrastructure.sources import (
    CompliantSourceUnavailableAdapter,
    WavelinkYouTubeSource,
)
from iwed_bot.infrastructure.voice.wavelink_gateway import WavelinkVoiceGateway
from iwed_bot.presentation.command_tree import IwedCommandTree
from iwed_bot.presentation.discord_notifier import DiscordPlaybackNotifier
from iwed_bot.settings import Settings

if TYPE_CHECKING:
    from iwed_bot.ports.repositories import QueueRepository
    from iwed_bot.ports.sources import TrackSource

logger = logging.getLogger(__name__)


class IwedBot(commands.Bot):
    """Subclass Discord Bot utama (Iwed) yang mengelola lifecycle, commands, dan Lavalink."""

    def __init__(self, settings: Settings) -> None:
        if settings.QUEUE_BACKEND == "redis":
            raise RuntimeError(
                "QUEUE_BACKEND=redis belum didukung pada Fase ini (tersedia di Fase 7). "
                "Gunakan QUEUE_BACKEND=memory."
            )

        # Konfigurasi intent minimum yang diperlukan: guilds dan voice_states
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.message_content = False

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            application_id=settings.DISCORD_APPLICATION_ID,
            tree_cls=IwedCommandTree,
        )

        self.settings: Settings = settings
        self.start_time: datetime = datetime.now(UTC)
        self.lavalink_connected: bool = False
        self._lavalink_node: wavelink.Node | None = None
        self._lavalink_ready_event: asyncio.Event = asyncio.Event()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._current_backoff_seconds: float = 5.0

        # Wiring Application & Infrastructure Components
        self.queue_repository: QueueRepository = InMemoryQueueRepository(
            max_queue_tracks=settings.QUEUE_MAX_TRACKS,
            default_volume=settings.DEFAULT_VOLUME,
        )
        self.operation_lock_registry = GuildOperationLockRegistry()
        self.voice_gateway = WavelinkVoiceGateway(bot=self)
        self.voice_service = VoiceSessionService(
            queue_repository=self.queue_repository,
            voice_gateway=self.voice_gateway,
            operation_locks=self.operation_lock_registry,
        )

        # Phase 4 Playback Infrastructure & Application Wiring
        self.playback_gateway = WavelinkPlaybackGateway(bot=self)
        self.notifier = DiscordPlaybackNotifier(bot=self)
        self.playback_coordinator = PlaybackCoordinator(
            queue_repository=self.queue_repository,
            playback_gateway=self.playback_gateway,
            operation_locks=self.operation_lock_registry,
            notifier=self.notifier,
        )

        # SOURCE_POLICY_MODE resolution
        if settings.SOURCE_POLICY_MODE == "prototype":
            logger.warning(
                "Bot berjalan dalam mode SOURCE_POLICY_MODE='prototype' "
                "(YouTube Prototype Adapter aktif)."
            )
            self.track_source: TrackSource = WavelinkYouTubeSource()
        else:
            logger.info("Bot berjalan dalam mode SOURCE_POLICY_MODE='compliance-first'.")
            self.track_source: TrackSource = CompliantSourceUnavailableAdapter()

        self.play_service = PlayRequestService(
            track_source=self.track_source,
            queue_repository=self.queue_repository,
            voice_service=self.voice_service,
            coordinator=self.playback_coordinator,
            operation_locks=self.operation_lock_registry,
        )
        self.queue_control = QueueControlService(
            queue_repository=self.queue_repository,
            playback_gateway=self.playback_gateway,
            coordinator=self.playback_coordinator,
            operation_locks=self.operation_lock_registry,
        )

    async def setup_hook(self) -> None:
        """Lifecycle hook startup: memuat ekstensi, Lavalink, dan sync slash commands."""
        logger.info("Menjalankan setup hook Iwed bot...")

        # 1. Muat ekstensi / cog slash commands
        await self.load_extension("iwed_bot.commands.health")
        logger.info("Ekstensi slash command 'health' berhasil dimuat.")

        await self.load_extension("iwed_bot.commands.voice")
        logger.info("Ekstensi slash command 'voice' berhasil dimuat.")

        await self.load_extension("iwed_bot.commands.music")
        logger.info("Ekstensi slash command 'music' berhasil dimuat.")

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
                    "error_type": type(err).__name__,
                },
            )
            self._start_reconnect_supervisor()

    def _start_reconnect_supervisor(self) -> None:
        """Memulai satu reconnect supervisor task jika belum ada yang berjalan."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_supervisor_loop())

    async def _reconnect_supervisor_loop(self) -> None:
        """Supervisor loop yang memanggil Pool.reconnect() dengan exponential backoff dan jitter."""
        self._current_backoff_seconds = 5.0
        max_backoff = 60.0

        while not self._lavalink_ready_event.is_set() and not self.is_closed():
            try:
                jitter = random.uniform(0.5, 2.0)
                delay = self._current_backoff_seconds + jitter
                logger.info(
                    "Supervisor menunggu untuk reconnect Wavelink Pool...",
                    extra={"retry_delay_seconds": round(delay, 2)},
                )
                await asyncio.sleep(delay)
                if self._lavalink_ready_event.is_set() or self.is_closed():
                    break

                logger.info("Supervisor mengeksekusi Pool.reconnect()...")
                await wavelink.Pool.reconnect()

                # Periksa status node setelah percobaan reconnect
                if (
                    self._lavalink_node
                    and self._lavalink_node.status == wavelink.NodeStatus.CONNECTED
                ):
                    self.lavalink_connected = True
                    self._lavalink_ready_event.set()
                    self._current_backoff_seconds = 5.0
                    logger.info("Supervisor mendeteksi node CONNECTED, supervisor selesai.")
                    break

                # Jika masih DISCONNECTED, naikkan backoff
                self._current_backoff_seconds = min(self._current_backoff_seconds * 2, max_backoff)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.debug(
                    "Supervisor Pool.reconnect() gagal, mencoba kembali nanti",
                    extra={"error_type": type(err).__name__},
                )
                self._current_backoff_seconds = min(self._current_backoff_seconds * 2, max_backoff)

    async def on_ready(self) -> None:
        """Event saat koneksi gateway Discord berhasil terjalin."""
        logger.info(
            "Discord Gateway terhubung dan Iwed Bot siap beroperasi",
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
        self._current_backoff_seconds = 5.0
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

    def _extract_track_metadata(self, payload: Any) -> tuple[int | None, UUID | None, int | None]:
        """Mengekstrak guild_id, entry_id, dan generation dari Wavelink event payload."""
        from iwed_bot.infrastructure.playback.metadata import parse_track_metadata

        return parse_track_metadata(payload)

    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        """Event handler ketika track selesai atau berhenti di Wavelink."""
        guild_id, entry_id, generation = self._extract_track_metadata(payload)
        if guild_id is None:
            return

        reason = str(payload.reason)
        logger.debug(
            "Event wavelink_track_end diterima",
            extra={
                "guild_id": guild_id,
                "entry_id": str(entry_id) if entry_id else None,
                "generation": generation,
                "reason": reason,
            },
        )
        await self.playback_coordinator.handle_track_end(
            guild_id=guild_id,
            entry_id=entry_id,
            generation=generation,
            reason=reason,
        )

    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        """Event handler ketika terjadi exception saat memutar track di Wavelink."""
        guild_id, entry_id, generation = self._extract_track_metadata(payload)
        if guild_id is None:
            return

        logger.warning(
            "Event wavelink_track_exception diterima",
            extra={
                "guild_id": guild_id,
                "entry_id": str(entry_id) if entry_id else None,
                "generation": generation,
                "exception_type": type(payload.exception).__name__,
            },
        )
        await self.playback_coordinator.handle_track_exception(
            guild_id=guild_id,
            entry_id=entry_id,
            generation=generation,
            _exception=payload.exception,
        )

    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        """Event handler ketika track stuck di Wavelink."""
        guild_id, entry_id, generation = self._extract_track_metadata(payload)
        if guild_id is None:
            return

        logger.warning(
            "Event wavelink_track_stuck diterima",
            extra={
                "guild_id": guild_id,
                "entry_id": str(entry_id) if entry_id else None,
                "generation": generation,
                "threshold_ms": payload.threshold,
            },
        )
        await self.playback_coordinator.handle_track_stuck(
            guild_id=guild_id,
            entry_id=entry_id,
            generation=generation,
            _threshold_ms=payload.threshold,
        )

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
        """Graceful shutdown: menutup coordinator, voice service, dan pool Wavelink."""
        logger.info("Memulai proses graceful shutdown Iwed bot...")

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        # 1. Shutdown coordinator runners
        try:
            await self.playback_coordinator.shutdown()
        except Exception as err:
            logger.warning(
                "Terjadi error saat shutdown playback_coordinator",
                extra={"error_type": type(err).__name__},
            )

        # 1b. Shutdown playback gateway handles
        try:
            await self.playback_gateway.shutdown()
        except Exception as err:
            logger.warning(
                "Terjadi error saat shutdown playback_gateway",
                extra={"error_type": type(err).__name__},
            )

        # 2. Shutdown voice service
        try:
            await self.voice_service.shutdown()
        except Exception as err:
            logger.warning(
                "Terjadi error saat shutdown voice_service",
                extra={"error_type": type(err).__name__},
            )

        # 3. Close Wavelink pool
        try:
            await wavelink.Pool.close()
            logger.info("Koneksi Wavelink pool berhasil ditutup.")
        except Exception as err:
            logger.warning(
                "Terjadi error saat menutup Wavelink pool",
                extra={"error_type": type(err).__name__},
            )

        # 4. Close Discord connection
        await super().close()
        if hasattr(self, "http") and hasattr(self.http, "close"):
            with contextlib.suppress(Exception):
                await self.http.close()
        logger.info("Proses bot Discord Iwed berhasil dihentikan secara bersih.")
