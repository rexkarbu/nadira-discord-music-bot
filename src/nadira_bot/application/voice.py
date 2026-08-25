"""Application service yang mengorkestrasi voice session dan command lifecycle.

Layanan ini menjembatani presentation layer, QueueRepository, dan VoiceGateway
dengan penegakan business rules, transisi state yang aman, serta per-guild operation locking.
Bebas dari import discord atau wavelink.
"""

import asyncio
import contextlib
import logging

from nadira_bot.application.concurrency import GuildOperationLockRegistry
from nadira_bot.application.errors import (
    DifferentVoiceChannel,
    LavalinkUnavailable,
    NadiraApplicationError,
    UserNotInVoice,
    VoiceConnectionFailed,
    VoiceDisconnectFailed,
    VoiceMoveFailed,
)
from nadira_bot.domain.errors import VersionConflict
from nadira_bot.domain.models import (
    PlaybackState,
    PlaybackTransition,
    SessionStateUpdate,
    VersionedGuildSession,
)
from nadira_bot.ports.repositories import QueueRepository
from nadira_bot.ports.voice import VoiceConnectionSnapshot, VoiceGateway

logger = logging.getLogger(__name__)


class VoiceSessionService:
    """Service pengelola lifecycle koneksi voice dan state session per-guild."""

    def __init__(
        self,
        queue_repository: QueueRepository,
        voice_gateway: VoiceGateway,
        operation_locks: GuildOperationLockRegistry | None = None,
    ) -> None:
        self._repository = queue_repository
        self._gateway = voice_gateway
        self._operation_locks = operation_locks or GuildOperationLockRegistry()

    async def join(
        self,
        guild_id: int,
        channel_id: int,
        text_channel_id: int | None = None,
        origin_channel_id: int | None = None,
        can_move_members: bool = False,
    ) -> tuple[VoiceConnectionSnapshot, VersionedGuildSession, bool, bool]:
        """Menghubungkan bot ke voice channel atau memindahkan jika diizinkan.

        Returns:
            Tuple (conn_snapshot, session, is_move, is_noop).
        """
        async with self._operation_locks.get_lock(guild_id):
            if not await self._gateway.is_available():
                raise LavalinkUnavailable()

            session = await self._repository.get_session(guild_id)
            conn = await self._gateway.get_connection(guild_id)

            # Skenario 1: Bot sudah terhubung ke voice channel
            if conn is not None and conn.is_connected:
                # 1a. Bot sudah di channel yang sama -> No-Op sukses / rekonsiliasi metadata
                if conn.channel_id == channel_id:
                    if (
                        session.state == PlaybackState.DISCONNECTED
                        or session.voice_channel_id != channel_id
                        or (text_channel_id and session.text_channel_id != text_channel_id)
                    ):
                        target_state = (
                            PlaybackState.IDLE
                            if session.state == PlaybackState.DISCONNECTED
                            else session.state
                        )
                        session = await self._update_session_with_retry(
                            guild_id,
                            SessionStateUpdate(
                                state=target_state,
                                voice_channel_id=channel_id,
                                text_channel_id=text_channel_id or session.text_channel_id,
                            ),
                        )
                    return conn, session, False, True

                # 1b. Bot berada di channel berbeda: validasi provenance origin_channel_id
                if origin_channel_id is None or origin_channel_id != conn.channel_id:
                    raise DifferentVoiceChannel(
                        "Bot telah berpindah voice channel sebelum operasi selesai. "
                        "Silakan ulangi perintah."
                    )

                # Validasi izin Move Members pada origin dan target
                if not can_move_members:
                    raise DifferentVoiceChannel(
                        "Bot sedang berada di voice channel lain. "
                        "Diperlukan izin Move Members pada channel asal "
                        "dan channel tujuan untuk memindahkannya."
                    )

                try:
                    new_conn = await self._gateway.move(guild_id, channel_id)
                except Exception as err:
                    if isinstance(err, NadiraApplicationError):
                        raise
                    raise VoiceMoveFailed("Gagal memindahkan bot ke voice channel.") from err

                session = await self._update_session_with_retry(
                    guild_id,
                    SessionStateUpdate(
                        voice_channel_id=channel_id,
                        text_channel_id=text_channel_id or session.text_channel_id,
                    ),
                )
                return new_conn, session, True, False

            # Skenario 2: Bot belum terhubung -> Inisiasi koneksi baru
            # Transisi state: DISCONNECTED -> CONNECTING
            session = await self._update_session_with_retry(
                guild_id,
                SessionStateUpdate(
                    state=PlaybackState.CONNECTING,
                    voice_channel_id=channel_id,
                    text_channel_id=text_channel_id,
                ),
            )

            try:
                new_conn = await self._gateway.connect(guild_id, channel_id)
            except Exception as err:
                # Kompensasi rollback jika network connect gagal
                logger.warning(
                    "Koneksi gateway voice gagal, melakukan kompensasi state ke DISCONNECTED",
                    extra={
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "error_type": type(err).__name__,
                    },
                )
                with contextlib.suppress(Exception):
                    await self._update_session_with_retry(
                        guild_id,
                        SessionStateUpdate(
                            state=PlaybackState.DISCONNECTED,
                            voice_channel_id=None,
                            text_channel_id=None,
                            current_entry=None,
                        ),
                    )

                if isinstance(err, NadiraApplicationError):
                    raise
                raise VoiceConnectionFailed(
                    "Gagal menghubungkan ke voice channel. Silakan coba lagi."
                ) from err

            # Sukses connect: CONNECTING -> IDLE
            session = await self._update_session_with_retry(
                guild_id,
                SessionStateUpdate(
                    state=PlaybackState.IDLE,
                    voice_channel_id=channel_id,
                    text_channel_id=text_channel_id,
                ),
            )
            return new_conn, session, False, False

    async def stop(
        self,
        guild_id: int,
        requester_channel_id: int | None = None,
    ) -> tuple[VersionedGuildSession, bool]:
        """Menghentikan sesi pemutaran, mengosongkan antrean, dan memutuskan koneksi voice.

        Returns:
            Tuple (session, was_active).
        """
        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            conn = await self._gateway.get_connection(guild_id)

            is_gateway_connected = conn is not None and conn.is_connected
            is_clean_disconnected = (
                not is_gateway_connected
                and session.state == PlaybackState.DISCONNECTED
                and session.current_entry is None
                and len(session.upcoming) == 0
            )

            # Skenario 1: Disconnected clean -> Idempotent No-Op sukses
            if is_clean_disconnected:
                return session, False

            # Skenario 2: Gateway masih connected -> wajibkan user di voice & same channel
            if is_gateway_connected:
                if requester_channel_id is None:
                    raise UserNotInVoice(
                        "Masuk ke voice channel terlebih dahulu untuk menghentikan pemutaran."
                    )
                if conn is not None and conn.channel_id != requester_channel_id:
                    raise DifferentVoiceChannel(
                        "Kamu harus berada di voice channel yang sama dengan bot untuk stop."
                    )

            # Skenario 3: Invalidation domain & pengosongan antrean
            # Jika ada current track aktif: naikkan generation tepat satu kali
            if session.current_entry is not None or session.state in (
                PlaybackState.PLAYING,
                PlaybackState.PAUSED,
            ):
                trans = PlaybackTransition(
                    next_current_entry=None,
                    next_upcoming=(),
                    next_state=PlaybackState.STOPPING,
                    increment_generation=True,
                )
                session = await self._apply_transition_with_retry(guild_id, trans)
            else:
                # Tidak ada current track: generation TIDAK boleh bertambah
                if len(session.upcoming) > 0:
                    session = await self._clear_queue_with_retry(guild_id)
                if (
                    session.state != PlaybackState.STOPPING
                    and session.state != PlaybackState.DISCONNECTED
                ):
                    session = await self._update_session_with_retry(
                        guild_id,
                        SessionStateUpdate(state=PlaybackState.STOPPING),
                    )

            # Skenario 4: Disconnect gateway jika sedang terhubung
            if is_gateway_connected:
                disconnect_error = None
                try:
                    await self._gateway.disconnect(guild_id)
                except Exception as err:
                    disconnect_error = err
                    logger.warning(
                        "Terjadi exception saat memutuskan koneksi gateway voice",
                        extra={"guild_id": guild_id, "error_type": type(err).__name__},
                    )

                # Verifikasi status aktual player
                active_conn = await self._gateway.get_connection(guild_id)
                if active_conn is not None and active_conn.is_connected:
                    # Player tetap terhubung: pertahankan STOPPING dan voice_channel_id aktual
                    logger.error(
                        "Gateway tetap terhubung setelah disconnect; mempertahankan STOPPING",
                        extra={"guild_id": guild_id},
                    )
                    if isinstance(disconnect_error, NadiraApplicationError):
                        raise disconnect_error
                    raise VoiceDisconnectFailed(
                        "Gagal memutuskan koneksi dari voice channel."
                    ) from disconnect_error

            # Skenario 5: Finalisasi ke DISCONNECTED secara bersih
            session = await self._update_session_with_retry(
                guild_id,
                SessionStateUpdate(
                    state=PlaybackState.DISCONNECTED,
                    voice_channel_id=None,
                    text_channel_id=None,
                    current_entry=None,
                    idle_deadline=None,
                ),
            )
            return session, True

    async def handle_voice_state_update(
        self,
        guild_id: int,
        old_channel_id: int | None,
        new_channel_id: int | None,
        is_stage: bool = False,
    ) -> None:
        """Menangani rekonsiliasi state saat bot dipindahkan atau dikeluarkan secara eksternal."""
        async with self._operation_locks.get_lock(guild_id):
            if old_channel_id == new_channel_id:
                return

            # Ambil snapshot koneksi gateway aktual setelah memperoleh per-guild lock
            conn = await self._gateway.get_connection(guild_id)
            actual_ch = conn.channel_id if (conn and conn.is_connected) else None

            # Jika snapshot gateway aktual tidak sama dengan new_channel_id dari event,
            # anggap event stale (misalnya bot sudah reconnect ke channel lain) dan abaikan.
            if actual_ch != new_channel_id:
                logger.debug(
                    "Mengabaikan event voice state update yang sudah stale",
                    extra={
                        "guild_id": guild_id,
                        "event_new_channel_id": new_channel_id,
                        "actual_gateway_channel_id": actual_ch,
                    },
                )
                return

            session = await self._repository.get_session(guild_id)

            # Skenario A: Bot dikeluarkan / terputus (Kick or disconnect by admin)
            if new_channel_id is None:
                # Jika domain sudah berstatus DISCONNECTED dan bersih, lakukan no-op tanpa mutasi
                if (
                    session.state == PlaybackState.DISCONNECTED
                    and session.voice_channel_id is None
                    and session.current_entry is None
                    and len(session.upcoming) == 0
                ):
                    return

                logger.info(
                    "Mendeteksi event voice disconnect eksternal untuk bot",
                    extra={"guild_id": guild_id, "old_channel_id": old_channel_id},
                )
                with contextlib.suppress(Exception):
                    await self._gateway.disconnect(guild_id)

                if session.current_entry is not None or session.state in (
                    PlaybackState.PLAYING,
                    PlaybackState.PAUSED,
                ):
                    trans = PlaybackTransition(
                        next_current_entry=None,
                        next_upcoming=(),
                        next_state=PlaybackState.STOPPING,
                        increment_generation=True,
                    )
                    session = await self._apply_transition_with_retry(guild_id, trans)
                elif len(session.upcoming) > 0:
                    session = await self._clear_queue_with_retry(guild_id)

                await self._update_session_with_retry(
                    guild_id,
                    SessionStateUpdate(
                        state=PlaybackState.DISCONNECTED,
                        voice_channel_id=None,
                        text_channel_id=None,
                        current_entry=None,
                        idle_deadline=None,
                    ),
                )
                return

            # Skenario B: Bot dipindahkan ke Stage Channel -> Tidak didukung, disconnect aman
            if is_stage:
                logger.warning(
                    "Bot dipindahkan ke Stage Channel secara eksternal. Memutuskan koneksi.",
                    extra={"guild_id": guild_id, "stage_channel_id": new_channel_id},
                )
                with contextlib.suppress(Exception):
                    await self._gateway.disconnect(guild_id)

                active_conn = await self._gateway.get_connection(guild_id)
                if active_conn is not None and active_conn.is_connected:
                    logger.warning(
                        "Player tetap terhubung setelah disconnect Stage Channel gagal",
                        extra={"guild_id": guild_id},
                    )
                    return

                if session.current_entry is not None or session.state in (
                    PlaybackState.PLAYING,
                    PlaybackState.PAUSED,
                ):
                    trans = PlaybackTransition(
                        next_current_entry=None,
                        next_upcoming=(),
                        next_state=PlaybackState.STOPPING,
                        increment_generation=True,
                    )
                    session = await self._apply_transition_with_retry(guild_id, trans)
                elif len(session.upcoming) > 0:
                    session = await self._clear_queue_with_retry(guild_id)

                await self._update_session_with_retry(
                    guild_id,
                    SessionStateUpdate(
                        state=PlaybackState.DISCONNECTED,
                        voice_channel_id=None,
                        text_channel_id=None,
                        current_entry=None,
                        idle_deadline=None,
                    ),
                )
                return

            # Skenario C: Bot dipindahkan ke Voice Channel standar lain
            # Jika domain CONNECTING dan koneksi fisik terhubung di new_channel_id -> IDLE
            if session.state == PlaybackState.CONNECTING:
                logger.info(
                    "Merekonsiliasi session CONNECTING menjadi IDLE sesuai snapshot fisik",
                    extra={"guild_id": guild_id, "channel_id": new_channel_id},
                )
                await self._update_session_with_retry(
                    guild_id,
                    SessionStateUpdate(
                        state=PlaybackState.IDLE,
                        voice_channel_id=new_channel_id,
                    ),
                )
                return

            # Jika domain sudah mencerminkan channel_id tujuan, no-op tanpa mutasi version
            if session.voice_channel_id == new_channel_id:
                return

            logger.info(
                "Mendeteksi pemindahan voice channel eksternal untuk bot",
                extra={
                    "guild_id": guild_id,
                    "old_channel_id": old_channel_id,
                    "new_channel_id": new_channel_id,
                },
            )
            await self._update_session_with_retry(
                guild_id,
                SessionStateUpdate(
                    voice_channel_id=new_channel_id,
                ),
            )

    async def shutdown(self) -> None:
        """Memutuskan seluruh koneksi gateway audio saat bot dimatikan."""
        await self._gateway.shutdown()

    async def _update_session_with_retry(
        self, guild_id: int, update: SessionStateUpdate, max_retries: int = 3
    ) -> VersionedGuildSession:
        """Mengulang eksekusi update_session_state jika terjadi VersionConflict (maks 3 kali)."""
        for attempt in range(max_retries):
            session = await self._repository.get_session(guild_id)
            try:
                return await self._repository.update_session_state(
                    guild_id, update, expected_version=session.version
                )
            except VersionConflict:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.01)
        raise VersionConflict(f"Gagal memperbarui session guild {guild_id} setelah retry.")

    async def _apply_transition_with_retry(
        self, guild_id: int, transition: PlaybackTransition, max_retries: int = 3
    ) -> VersionedGuildSession:
        """Mengulang eksekusi apply_playback_transition jika terjadi VersionConflict."""
        for attempt in range(max_retries):
            session = await self._repository.get_session(guild_id)
            try:
                return await self._repository.apply_playback_transition(
                    guild_id, transition, expected_version=session.version
                )
            except VersionConflict:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.01)
        raise VersionConflict(f"Gagal menerapkan transisi guild {guild_id} setelah retry.")

    async def _clear_queue_with_retry(
        self, guild_id: int, max_retries: int = 3
    ) -> VersionedGuildSession:
        """Mengulang eksekusi clear queue jika terjadi VersionConflict."""
        for attempt in range(max_retries):
            session = await self._repository.get_session(guild_id)
            try:
                return await self._repository.clear(guild_id, expected_version=session.version)
            except VersionConflict:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.01)
        raise VersionConflict(f"Gagal mengosongkan antrean guild {guild_id} setelah retry.")
