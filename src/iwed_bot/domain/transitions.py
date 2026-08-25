"""Fungsi-fungsi transisi status playback dan semantik loop murni.

Modul ini menyediakan logika synchronous murni tanpa side effects untuk menghitung
transisi state, memvalidasi generation token, serta mengevaluasi matriks loop mode.
"""

from iwed_bot.domain.errors import InvalidStateTransition, StalePlaybackEvent
from iwed_bot.domain.models import (
    ALLOWED_STATE_TRANSITIONS,
    LoopMode,
    PlaybackState,
    PlaybackTransition,
    VersionedGuildSession,
)


def validate_state_transition(
    current_state: PlaybackState,
    next_state: PlaybackState,
    *,
    allow_same_state_playing: bool = False,
) -> None:
    """Memvalidasi bahwa transisi dari current_state ke next_state sah.

    Args:
        current_state: Status playback saat ini.
        next_state: Status playback target yang diinginkan.
        allow_same_state_playing: Apakah PLAYING -> PLAYING diizinkan khusus untuk
            transisi loop lagu baru (TRACK/QUEUE).

    Raises:
        InvalidStateTransition: Jika transisi state tidak diizinkan oleh ALLOWED_STATE_TRANSITIONS.
    """
    if current_state == next_state:
        if current_state == PlaybackState.PLAYING and allow_same_state_playing:
            return
        # Same-state transitions diperbolehkan untuk metadata update
        return

    allowed_targets = ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())
    if next_state not in allowed_targets:
        msg = (
            f"Transisi status playback tidak sah dari '{current_state.value}' "
            f"ke '{next_state.value}'."
        )
        raise InvalidStateTransition(msg)


def validate_event_generation(session: VersionedGuildSession, event_generation: int) -> None:
    """Memvalidasi bahwa event generation token sesuai dengan active generation sesi.

    Args:
        session: Snapshot sesi guild yang aktif.
        event_generation: Generation token yang dibawa oleh event playback.

    Raises:
        StalePlaybackEvent: Jika generation token tidak cocok (event usang/duplikat).
    """
    if event_generation != session.generation:
        msg = (
            f"Generation token tidak cocok: event={event_generation}, "
            f"active_session={session.generation}."
        )
        raise StalePlaybackEvent(msg)


def compute_track_end_transition(
    session: VersionedGuildSession, event_generation: int
) -> PlaybackTransition:
    """Menghitung target PlaybackTransition murni ketika track saat ini selesai diputar.

    Preconditions wajib:
    1. session.current_entry tidak boleh None (jika None -> lempar InvalidStateTransition).
    2. session.state harus PLAYING atau PAUSED (jika tidak -> lempar InvalidStateTransition).
    3. event_generation harus sama persis dengan session.generation.

    Args:
        session: Snapshot sesi guild saat ini.
        event_generation: Token generasi dari event track-end yang diterima.

    Returns:
        PlaybackTransition yang berisi next_current_entry, next_upcoming,
        next_state, dan increment_generation.

    Raises:
        InvalidStateTransition: Jika precondition current_entry atau state dilanggar.
        StalePlaybackEvent: Jika event_generation tidak cocok.
    """
    if session.current_entry is None:
        msg = (
            "Tidak dapat menghitung track-end transition karena session.current_entry "
            "bernilai None."
        )
        raise InvalidStateTransition(msg)

    if session.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
        msg = (
            f"compute_track_end_transition hanya sah saat status PLAYING atau PAUSED, "
            f"status saat ini: '{session.state.value}'."
        )
        raise InvalidStateTransition(msg)

    validate_event_generation(session, event_generation)

    current_entry = session.current_entry
    upcoming = session.upcoming

    match session.loop_mode:
        case LoopMode.OFF:
            if upcoming:
                return PlaybackTransition(
                    next_current_entry=upcoming[0],
                    next_upcoming=upcoming[1:],
                    next_state=PlaybackState.PLAYING,
                    increment_generation=True,
                )
            return PlaybackTransition(
                next_current_entry=None,
                next_upcoming=(),
                next_state=PlaybackState.IDLE,
                increment_generation=True,
            )

        case LoopMode.TRACK:
            return PlaybackTransition(
                next_current_entry=current_entry,
                next_upcoming=upcoming,
                next_state=PlaybackState.PLAYING,
                increment_generation=True,
            )

        case LoopMode.QUEUE:
            if upcoming:
                return PlaybackTransition(
                    next_current_entry=upcoming[0],
                    next_upcoming=upcoming[1:] + (current_entry,),
                    next_state=PlaybackState.PLAYING,
                    increment_generation=True,
                )
            return PlaybackTransition(
                next_current_entry=current_entry,
                next_upcoming=(),
                next_state=PlaybackState.PLAYING,
                increment_generation=True,
            )
