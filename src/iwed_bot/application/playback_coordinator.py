"""PlaybackCoordinator mengorkestrasi one-shot advance runner dan sinkronisasi event per-guild."""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from iwed_bot.application.concurrency import GuildOperationLockRegistry
from iwed_bot.domain.errors import InvalidStateTransition, StalePlaybackEvent
from iwed_bot.domain.models import PlaybackState, VersionedGuildSession
from iwed_bot.domain.transitions import (
    PlaybackTransition,
    compute_track_end_transition,
    compute_track_failure_transition,
)
from iwed_bot.ports.notifications import PlaybackNotifier
from iwed_bot.ports.playback import PlaybackGateway, PlaybackSnapshot, PreparedPlayback
from iwed_bot.ports.repositories import QueueRepository

logger = logging.getLogger(__name__)


class RunnerStatus(StrEnum):
    """Status hasil eksekusi runner coordinator."""

    STARTED = "started"
    ALREADY_ACTIVE = "already_active"
    SUPERSEDED = "superseded"
    HALTED = "halted"
    EMPTY = "empty"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunnerOutcome:
    """Hasil akhir dari satu iterasi eksekusi advance loop."""

    status: RunnerStatus
    started_entry_id: UUID | None
    generation: int | None
    failed_entry_ids: tuple[UUID, ...] = ()
    snapshot: PlaybackSnapshot | None = None


@dataclass(slots=True)
class RunnerSlot:
    """Slot registri runner per guild."""

    task: asyncio.Task[RunnerOutcome]
    entry_id: UUID | None
    generation: int | None


class PlaybackCoordinator:
    """Koordinator one-shot runner yang menjamin eksekusi single-track advance secara terisolasi."""

    def __init__(
        self,
        queue_repository: QueueRepository,
        playback_gateway: PlaybackGateway,
        operation_locks: GuildOperationLockRegistry,
        notifier: PlaybackNotifier | None = None,
    ) -> None:
        self._repository = queue_repository
        self._gateway = playback_gateway
        self._operation_locks = operation_locks
        self._notifier = notifier

        self._guild_runners: dict[int, RunnerSlot] = {}
        self._registry_lock: asyncio.Lock = asyncio.Lock()
        self._consecutive_failures: dict[int, int] = {}
        self._last_failure_entry: dict[int, tuple[UUID | None, int | None]] = {}
        self._is_closing: bool = False

    async def ensure_running(
        self,
        guild_id: int,
        expected_entry_id: UUID | None = None,
        expected_generation: int | None = None,
    ) -> asyncio.Task[RunnerOutcome]:
        """Memastikan ada one-shot runner yang memproses target playback pada guild.

        Returns:
            asyncio.Task yang mengembalikan RunnerOutcome.
        """
        if self._is_closing:
            return asyncio.create_task(self._create_cancelled_outcome())

        while True:
            old_task_to_cancel: asyncio.Task[RunnerOutcome] | None = None
            async with self._registry_lock:
                existing_slot = self._guild_runners.get(guild_id)
                if existing_slot is not None and not existing_slot.task.done():
                    # 1. Jika target entry dan generation sama -> deduplikasi (reuse task aktif)
                    if expected_entry_id is not None and (
                        existing_slot.entry_id,
                        existing_slot.generation,
                    ) == (expected_entry_id, expected_generation):
                        return existing_slot.task

                    # 2. Jika expected_entry_id is None dan task masih berjalan -> reuse
                    if expected_entry_id is None:
                        return existing_slot.task

                    # 3. Target berbeda -> catat old task untuk dibatalkan di luar lock
                    old_task_to_cancel = existing_slot.task
                else:
                    # Buat task baru untuk target di bawah registry lock
                    task = asyncio.create_task(
                        self._run_advance_loop(guild_id, expected_entry_id, expected_generation)
                    )
                    slot = RunnerSlot(
                        task=task, entry_id=expected_entry_id, generation=expected_generation
                    )
                    self._guild_runners[guild_id] = slot

                    # Done callback membersihkan slot hanya jika identity task masih cocok
                    task.add_done_callback(lambda _t, g=guild_id, t=task: self._cleanup_slot(g, t))
                    return task

            # 4. Batalkan dan await old task di luar registry lock
            if old_task_to_cancel is not None:
                old_task_to_cancel.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await old_task_to_cancel

    def _update_slot_target(
        self,
        guild_id: int,
        task: asyncio.Task[Any],
        entry_id: UUID | None,
        generation: int | None,
    ) -> None:
        """Memperbarui metadata slot setelah runner berhasil melakukan claim."""
        slot = self._guild_runners.get(guild_id)
        if slot is not None and slot.task is task:
            slot.entry_id = entry_id
            slot.generation = generation

    async def _create_cancelled_outcome(self) -> RunnerOutcome:
        return RunnerOutcome(
            status=RunnerStatus.CANCELLED,
            started_entry_id=None,
            generation=None,
        )

    def _cleanup_slot(self, guild_id: int, task: asyncio.Task[RunnerOutcome]) -> None:
        """Membersihkan task runner dari registri hanya jika task masih aktif di slot."""
        slot = self._guild_runners.get(guild_id)
        if slot is not None and slot.task is task:
            self._guild_runners.pop(guild_id, None)

        if not task.cancelled():
            err = task.exception()
            if err is not None:
                logger.debug(
                    "Runner task selesai dengan error",
                    extra={"guild_id": guild_id, "error_type": type(err).__name__},
                )

    async def _run_advance_loop(
        self,
        guild_id: int,
        expected_entry_id: UUID | None,
        expected_generation: int | None,
    ) -> RunnerOutcome:
        """Advance loop yang memproses start target, JIT prepare non-blocking, dan safety cap."""
        failed_ids: list[UUID] = []
        max_failures = 3
        current_task = asyncio.current_task()

        while not self._is_closing:
            # === PHASE 1: Identifikasi target di dalam lock singkat ===
            target_entry = None
            target_generation = None
            session: VersionedGuildSession | None = None

            async with self._operation_locks.get_lock(guild_id):
                session = await self._repository.get_session(guild_id)

                if session.state == PlaybackState.IDLE:
                    if not session.upcoming:
                        return RunnerOutcome(
                            status=RunnerStatus.EMPTY,
                            started_entry_id=None,
                            generation=session.generation,
                            failed_entry_ids=tuple(failed_ids),
                        )

                    # Jika expected target ditentukan, pastikan target di antrean sesuai
                    if (
                        expected_entry_id is not None
                        and session.upcoming[0].id != expected_entry_id
                    ) or (
                        expected_generation is not None
                        and session.generation != expected_generation
                    ):
                        return RunnerOutcome(
                            status=RunnerStatus.SUPERSEDED,
                            started_entry_id=None,
                            generation=session.generation,
                            failed_entry_ids=tuple(failed_ids),
                        )

                    # Klaim entry pertama
                    claimed, new_session = await self._repository.claim_next(
                        guild_id, expected_version=session.version
                    )
                    if claimed is None:
                        return RunnerOutcome(
                            status=RunnerStatus.EMPTY,
                            started_entry_id=None,
                            generation=session.generation,
                            failed_entry_ids=tuple(failed_ids),
                        )
                    target_entry = claimed
                    target_generation = new_session.generation
                    session = new_session

                    # Update metadata slot
                    if current_task is not None:
                        self._update_slot_target(
                            guild_id, current_task, target_entry.id, target_generation
                        )

                elif session.state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
                    target_entry = session.current_entry
                    target_generation = session.generation
                    if target_entry is None:
                        return RunnerOutcome(
                            status=RunnerStatus.EMPTY,
                            started_entry_id=None,
                            generation=session.generation,
                            failed_entry_ids=tuple(failed_ids),
                        )

                    # Jika expected target ditentukan dan tidak cocok -> SUPERSEDED
                    if (expected_entry_id is not None and target_entry.id != expected_entry_id) or (
                        expected_generation is not None and target_generation != expected_generation
                    ):
                        return RunnerOutcome(
                            status=RunnerStatus.SUPERSEDED,
                            started_entry_id=None,
                            generation=target_generation,
                            failed_entry_ids=tuple(failed_ids),
                        )

                    # Periksa apakah physical play sudah aktif untuk target ini
                    snapshot = await self._gateway.get_snapshot(guild_id)
                    if (
                        snapshot is not None
                        and snapshot.is_playing
                        and snapshot.active_entry_id == target_entry.id
                        and snapshot.active_generation == target_generation
                    ):
                        return RunnerOutcome(
                            status=RunnerStatus.ALREADY_ACTIVE,
                            started_entry_id=target_entry.id,
                            generation=target_generation,
                            failed_entry_ids=tuple(failed_ids),
                            snapshot=snapshot,
                        )
                else:
                    return RunnerOutcome(
                        status=RunnerStatus.EMPTY,
                        started_entry_id=None,
                        generation=session.generation,
                        failed_entry_ids=tuple(failed_ids),
                    )

            if target_entry is None or target_generation is None:
                return RunnerOutcome(
                    status=RunnerStatus.EMPTY,
                    started_entry_id=None,
                    generation=session.generation if session else None,
                    failed_entry_ids=tuple(failed_ids),
                )

            # === PHASE 2 & 3: JIT Prepare & Physical Play dengan Lifecycle Handle try/finally ===
            prepared: PreparedPlayback | None = None
            consumed = False
            try:
                try:
                    prepared = await self._gateway.prepare_reference(guild_id, target_entry.track)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    failed_ids.append(target_entry.id)
                    is_halt = False
                    async with self._operation_locks.get_lock(guild_id):
                        current_session = await self._repository.get_session(guild_id)
                        if (
                            current_session.generation == target_generation
                            and current_session.current_entry is not None
                            and current_session.current_entry.id == target_entry.id
                        ):
                            current_fail = self._consecutive_failures.get(guild_id, 0) + 1
                            self._consecutive_failures[guild_id] = current_fail
                            is_halt = current_fail >= max_failures

                            trans = compute_track_failure_transition(
                                current_session, target_generation, halt=is_halt
                            )
                            await self._repository.apply_playback_transition(
                                guild_id, trans, current_session.version
                            )

                    logger.warning(
                        "JIT prepare gagal pada track target",
                        extra={
                            "guild_id": guild_id,
                            "track_id": str(target_entry.track.id),
                            "is_halt": is_halt,
                            "error_type": type(err).__name__,
                        },
                    )

                    if is_halt:
                        if self._notifier:
                            dest_channel = target_entry.requested_in_channel_id or (
                                session.text_channel_id if session else None
                            )
                            try:
                                await self._notifier.notify_playback_halted(
                                    guild_id=guild_id,
                                    text_channel_id=dest_channel,
                                    operation_id=uuid.uuid4(),
                                    failed_count=self._consecutive_failures.get(guild_id, 0),
                                )
                            except Exception as notif_err:
                                logger.warning(
                                    "Gagal mengirim notifikasi failure halt",
                                    extra={
                                        "guild_id": guild_id,
                                        "error_type": type(notif_err).__name__,
                                    },
                                )
                        return RunnerOutcome(
                            status=RunnerStatus.HALTED,
                            started_entry_id=None,
                            generation=target_generation,
                            failed_entry_ids=tuple(failed_ids),
                        )

                    # Jika belum halt, lanjutkan advance loop untuk track berikutnya
                    continue

                # === PHASE 3: Re-validation & Physical Play di Dalam Lock Singkat ===
                try:
                    async with self._operation_locks.get_lock(guild_id):
                        rechecked_session = await self._repository.get_session(guild_id)
                        if (
                            rechecked_session.generation != target_generation
                            or rechecked_session.current_entry is None
                            or rechecked_session.current_entry.id != target_entry.id
                        ):
                            return RunnerOutcome(
                                status=RunnerStatus.SUPERSEDED,
                                started_entry_id=None,
                                generation=rechecked_session.generation,
                                failed_entry_ids=tuple(failed_ids),
                            )

                        snapshot = await self._gateway.play_prepared(
                            guild_id=guild_id,
                            prepared=prepared,
                            entry_id=target_entry.id,
                            generation=target_generation,
                            volume=rechecked_session.volume,
                        )
                        consumed = True
                        return RunnerOutcome(
                            status=RunnerStatus.STARTED,
                            started_entry_id=target_entry.id,
                            generation=target_generation,
                            failed_entry_ids=tuple(failed_ids),
                            snapshot=snapshot,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as play_err:
                    failed_ids.append(target_entry.id)
                    is_halt = False
                    async with self._operation_locks.get_lock(guild_id):
                        current_session = await self._repository.get_session(guild_id)
                        if (
                            current_session.generation == target_generation
                            and current_session.current_entry is not None
                            and current_session.current_entry.id == target_entry.id
                        ):
                            current_fail = self._consecutive_failures.get(guild_id, 0) + 1
                            self._consecutive_failures[guild_id] = current_fail
                            is_halt = current_fail >= max_failures

                            trans = compute_track_failure_transition(
                                current_session, target_generation, halt=is_halt
                            )
                            await self._repository.apply_playback_transition(
                                guild_id, trans, current_session.version
                            )

                    logger.error(
                        "Physical play gagal dieksekusi",
                        extra={
                            "guild_id": guild_id,
                            "track_id": str(target_entry.track.id),
                            "is_halt": is_halt,
                            "error_type": type(play_err).__name__,
                        },
                    )

                    if is_halt:
                        if self._notifier:
                            dest_channel = target_entry.requested_in_channel_id or (
                                session.text_channel_id if session else None
                            )
                            try:
                                await self._notifier.notify_playback_halted(
                                    guild_id=guild_id,
                                    text_channel_id=dest_channel,
                                    operation_id=uuid.uuid4(),
                                    failed_count=self._consecutive_failures.get(guild_id, 0),
                                )
                            except Exception as notif_err:
                                logger.warning(
                                    "Gagal mengirim notifikasi failure halt",
                                    extra={
                                        "guild_id": guild_id,
                                        "error_type": type(notif_err).__name__,
                                    },
                                )
                        return RunnerOutcome(
                            status=RunnerStatus.HALTED,
                            started_entry_id=None,
                            generation=target_generation,
                            failed_entry_ids=tuple(failed_ids),
                        )
            finally:
                # Wajib membersihkan prepared handle jika tidak terkonsumsi (defense-in-depth)
                if prepared is not None and not consumed:
                    with contextlib.suppress(Exception):
                        await self._gateway.discard_prepared(prepared)

        return RunnerOutcome(
            status=RunnerStatus.CANCELLED,
            started_entry_id=None,
            generation=None,
            failed_entry_ids=tuple(failed_ids),
        )

    async def handle_track_end(
        self,
        guild_id: int,
        entry_id: UUID | None,
        generation: int | None,
        reason: str,
    ) -> None:
        """Handler untuk event wavelink_track_end."""
        if self._is_closing or generation is None or entry_id is None:
            return

        # Case-sensitive mapping Lavalink v4
        if reason == "finished":
            next_entry_id = None
            next_gen = None
            async with self._operation_locks.get_lock(guild_id):
                session = await self._repository.get_session(guild_id)
                if (
                    session.generation != generation
                    or session.current_entry is None
                    or session.current_entry.id != entry_id
                ):
                    return  # Stale event

                # Reset streak kegagalan pada penyelesaian lagu yang berhasil secara alami
                self._consecutive_failures[guild_id] = 0
                self._last_failure_entry.pop(guild_id, None)

                try:
                    trans = compute_track_end_transition(session, generation)
                    new_session = await self._repository.apply_playback_transition(
                        guild_id, trans, session.version
                    )
                    if trans.next_current_entry is not None:
                        next_entry_id = trans.next_current_entry.id
                        next_gen = new_session.generation
                except (InvalidStateTransition, StalePlaybackEvent):
                    return

            if next_entry_id is not None and next_gen is not None:
                await self.ensure_running(
                    guild_id, expected_entry_id=next_entry_id, expected_generation=next_gen
                )

        elif reason in ("loadFailed", "fault"):
            await self._handle_failure_event(guild_id, entry_id, generation, reason)

        elif reason == "stopped":
            next_entry_id = None
            next_gen = None
            async with self._operation_locks.get_lock(guild_id):
                session = await self._repository.get_session(guild_id)
                if (
                    session.generation != generation
                    or session.current_entry is None
                    or session.current_entry.id != entry_id
                ):
                    return  # Stale event

                snapshot = await self._gateway.get_snapshot(guild_id)
                if (
                    snapshot is not None
                    and snapshot.is_playing
                    and snapshot.active_entry_id != entry_id
                ):
                    return  # Target lain sudah aktif

                if snapshot is None or not snapshot.is_playing:
                    try:
                        trans = compute_track_end_transition(session, generation)
                        new_session = await self._repository.apply_playback_transition(
                            guild_id, trans, session.version
                        )
                        if (
                            trans.next_current_entry is not None
                            and trans.next_state == PlaybackState.PLAYING
                        ):
                            next_entry_id = trans.next_current_entry.id
                            next_gen = new_session.generation
                    except (InvalidStateTransition, StalePlaybackEvent):
                        return

            if next_entry_id is not None and next_gen is not None:
                await self.ensure_running(
                    guild_id, expected_entry_id=next_entry_id, expected_generation=next_gen
                )

        elif reason == "cleanup":
            async with self._operation_locks.get_lock(guild_id):
                session = await self._repository.get_session(guild_id)
                if (
                    session.generation != generation
                    or session.current_entry is None
                    or session.current_entry.id != entry_id
                ):
                    return  # Stale event

                snapshot = await self._gateway.get_snapshot(guild_id)
                if snapshot is None or not snapshot.is_playing:
                    # Cleanup transition: IDLE, clears current_entry, preserves upcoming
                    trans = PlaybackTransition(
                        next_current_entry=None,
                        next_upcoming=session.upcoming,
                        next_state=PlaybackState.IDLE,
                        increment_generation=True,
                    )
                    with contextlib.suppress(InvalidStateTransition, StalePlaybackEvent):
                        await self._repository.apply_playback_transition(
                            guild_id, trans, session.version
                        )

        elif reason == "replaced":
            # Replaced means replacement track already started -> no-op to prevent double advance
            return

        else:
            logger.warning(
                "Event wavelink_track_end diterima dengan reason tidak dikenal",
                extra={"guild_id": guild_id, "reason": reason},
            )

    async def handle_track_exception(
        self,
        guild_id: int,
        entry_id: UUID | None,
        generation: int | None,
        _exception: object,
    ) -> None:
        """Handler untuk event wavelink_track_exception."""
        if self._is_closing or generation is None or entry_id is None:
            return
        await self._handle_failure_event(guild_id, entry_id, generation, "exception")

    async def handle_track_stuck(
        self,
        guild_id: int,
        entry_id: UUID | None,
        generation: int | None,
        _threshold_ms: int,
    ) -> None:
        """Handler untuk event wavelink_track_stuck."""
        if self._is_closing or generation is None or entry_id is None:
            return
        await self._handle_failure_event(guild_id, entry_id, generation, "stuck")

    async def _handle_failure_event(
        self,
        guild_id: int,
        entry_id: UUID,
        generation: int,
        _reason: str,
    ) -> None:
        """Penanganan internal untuk failure event dengan validasi sebelum increment counter."""
        next_entry_id = None
        next_gen = None
        is_halt = False
        current_fail = 0

        async with self._operation_locks.get_lock(guild_id):
            session = await self._repository.get_session(guild_id)
            if (
                session.generation != generation
                or session.current_entry is None
                or session.current_entry.id != entry_id
            ):
                return  # Stale failure event

            if self._last_failure_entry.get(guild_id) == (entry_id, generation):
                return  # Duplicate failure event -> abaikan tanpa double increment

            self._last_failure_entry[guild_id] = (entry_id, generation)
            current_fail = self._consecutive_failures.get(guild_id, 0) + 1
            self._consecutive_failures[guild_id] = current_fail
            is_halt = current_fail >= 3

            try:
                trans = compute_track_failure_transition(session, generation, halt=is_halt)
                new_session = await self._repository.apply_playback_transition(
                    guild_id, trans, session.version
                )
                if not is_halt and trans.next_current_entry is not None:
                    next_entry_id = trans.next_current_entry.id
                    next_gen = new_session.generation
            except (InvalidStateTransition, StalePlaybackEvent):
                return

        if is_halt and self._notifier:
            dest_channel = session.current_entry.requested_in_channel_id or session.text_channel_id
            try:
                await self._notifier.notify_playback_halted(
                    guild_id=guild_id,
                    text_channel_id=dest_channel,
                    operation_id=uuid.uuid4(),
                    failed_count=current_fail,
                )
            except Exception as notif_err:
                logger.warning(
                    "Gagal mengirim notifikasi failure halt",
                    extra={
                        "guild_id": guild_id,
                        "error_type": type(notif_err).__name__,
                    },
                )

        if next_entry_id is not None and next_gen is not None:
            await self.ensure_running(
                guild_id, expected_entry_id=next_entry_id, expected_generation=next_gen
            )

    async def cancel_runner(self, guild_id: int) -> None:
        """Membatalkan runner aktif pada guild secara eksplisit."""
        async with self._registry_lock:
            slot = self._guild_runners.pop(guild_id, None)
            if slot is not None and not slot.task.done():
                slot.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await slot.task

    async def shutdown(self) -> None:
        """Graceful shutdown seluruh coordinator runners."""
        self._is_closing = True
        tasks_to_cancel = []
        async with self._registry_lock:
            for slot in self._guild_runners.values():
                if not slot.task.done():
                    slot.task.cancel()
                    tasks_to_cancel.append(slot.task)
            self._guild_runners.clear()

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
