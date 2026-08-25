"""Unit tests untuk transition logic, state machine matrix, dan loop semantics."""

import uuid
from datetime import UTC, datetime

import pytest

from nadira_bot.domain.errors import InvalidStateTransition, StalePlaybackEvent
from nadira_bot.domain.models import (
    LoopMode,
    PlaybackState,
    QueueEntry,
    SourceType,
    TrackReference,
    VersionedGuildSession,
)
from nadira_bot.domain.transitions import (
    compute_track_end_transition,
    validate_event_generation,
    validate_state_transition,
)


def make_track(title: str = "Track") -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="abc",
        source_uri=None,
        search_hint="artist - track",
        title=title,
        artists=("Artist",),
        duration_ms=10000,
        thumbnail_url=None,
        canonical_url=None,
    )


def make_entry(guild_id: int = 123, title: str = "Track") -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=make_track(title=title),
        requested_by_user_id=1,
        requested_in_channel_id=1,
        enqueued_at=datetime.now(UTC),
    )


class TestValidateStateTransition:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (PlaybackState.DISCONNECTED, PlaybackState.CONNECTING),
            (PlaybackState.CONNECTING, PlaybackState.IDLE),
            (PlaybackState.CONNECTING, PlaybackState.DISCONNECTED),
            (PlaybackState.IDLE, PlaybackState.PLAYING),
            (PlaybackState.IDLE, PlaybackState.STOPPING),
            (PlaybackState.IDLE, PlaybackState.DISCONNECTED),
            (PlaybackState.PLAYING, PlaybackState.PAUSED),
            (PlaybackState.PLAYING, PlaybackState.IDLE),
            (PlaybackState.PLAYING, PlaybackState.STOPPING),
            (PlaybackState.PLAYING, PlaybackState.DISCONNECTED),
            (PlaybackState.PAUSED, PlaybackState.PLAYING),
            (PlaybackState.PAUSED, PlaybackState.STOPPING),
            (PlaybackState.PAUSED, PlaybackState.DISCONNECTED),
            (PlaybackState.STOPPING, PlaybackState.IDLE),
            (PlaybackState.STOPPING, PlaybackState.DISCONNECTED),
        ],
    )
    def test_valid_transitions(self, current: PlaybackState, target: PlaybackState) -> None:
        validate_state_transition(current, target)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (PlaybackState.DISCONNECTED, PlaybackState.PLAYING),
            (PlaybackState.DISCONNECTED, PlaybackState.IDLE),
            (PlaybackState.DISCONNECTED, PlaybackState.PAUSED),
            (PlaybackState.CONNECTING, PlaybackState.PLAYING),
            (PlaybackState.CONNECTING, PlaybackState.PAUSED),
            (PlaybackState.IDLE, PlaybackState.PAUSED),
            (PlaybackState.IDLE, PlaybackState.CONNECTING),
            (PlaybackState.PAUSED, PlaybackState.IDLE),
            (PlaybackState.STOPPING, PlaybackState.PLAYING),
            (PlaybackState.STOPPING, PlaybackState.CONNECTING),
            (PlaybackState.STOPPING, PlaybackState.PAUSED),
        ],
    )
    def test_illegal_transitions_rejected(
        self, current: PlaybackState, target: PlaybackState
    ) -> None:
        with pytest.raises(InvalidStateTransition, match="Transisi status playback tidak sah"):
            validate_state_transition(current, target)

    def test_same_state_playing_with_flag(self) -> None:
        validate_state_transition(
            PlaybackState.PLAYING, PlaybackState.PLAYING, allow_same_state_playing=True
        )


class TestValidateEventGeneration:
    def test_matching_generation_passes(self) -> None:
        session = VersionedGuildSession(guild_id=123, generation=5)
        validate_event_generation(session, 5)

    def test_stale_generation_raises(self) -> None:
        session = VersionedGuildSession(guild_id=123, generation=5)
        with pytest.raises(StalePlaybackEvent, match="Generation token tidak cocok"):
            validate_event_generation(session, 4)

        with pytest.raises(StalePlaybackEvent, match="Generation token tidak cocok"):
            validate_event_generation(session, 6)


class TestComputeTrackEndTransition:
    def test_precondition_current_entry_none_rejected(self) -> None:
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.IDLE,
            current_entry=None,
            generation=1,
        )
        with pytest.raises(
            InvalidStateTransition,
            match="session.current_entry bernilai None",
        ):
            compute_track_end_transition(session, 1)

    def test_precondition_disconnected_state_rejected(self) -> None:
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.DISCONNECTED,
            current_entry=None,
            generation=1,
        )
        with pytest.raises(
            InvalidStateTransition,
            match="session.current_entry bernilai None",
        ):
            compute_track_end_transition(session, 1)

    def test_stale_generation_rejected(self) -> None:
        e1 = make_entry(guild_id=123, title="Current")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=e1,
            generation=2,
        )
        with pytest.raises(StalePlaybackEvent):
            compute_track_end_transition(session, 1)

    def test_loop_off_with_upcoming(self) -> None:
        curr = make_entry(guild_id=123, title="Current")
        up1 = make_entry(guild_id=123, title="Upcoming 1")
        up2 = make_entry(guild_id=123, title="Upcoming 2")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=curr,
            upcoming=(up1, up2),
            loop_mode=LoopMode.OFF,
            generation=3,
        )
        res = compute_track_end_transition(session, 3)
        assert res.next_current_entry == up1
        assert res.next_upcoming == (up2,)
        assert res.next_state == PlaybackState.PLAYING
        assert res.increment_generation

    def test_loop_off_with_empty_upcoming(self) -> None:
        curr = make_entry(guild_id=123, title="Current")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=curr,
            upcoming=(),
            loop_mode=LoopMode.OFF,
            generation=3,
        )
        res = compute_track_end_transition(session, 3)
        assert res.next_current_entry is None
        assert res.next_upcoming == ()
        assert res.next_state == PlaybackState.IDLE
        assert res.increment_generation

    def test_loop_track_replays_current(self) -> None:
        curr = make_entry(guild_id=123, title="Current")
        up1 = make_entry(guild_id=123, title="Upcoming 1")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=curr,
            upcoming=(up1,),
            loop_mode=LoopMode.TRACK,
            generation=3,
        )
        res = compute_track_end_transition(session, 3)
        assert res.next_current_entry == curr
        assert res.next_upcoming == (up1,)
        assert res.next_state == PlaybackState.PLAYING
        assert res.increment_generation

    def test_loop_queue_with_upcoming(self) -> None:
        curr = make_entry(guild_id=123, title="Current")
        up1 = make_entry(guild_id=123, title="Upcoming 1")
        up2 = make_entry(guild_id=123, title="Upcoming 2")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=curr,
            upcoming=(up1, up2),
            loop_mode=LoopMode.QUEUE,
            generation=3,
        )
        res = compute_track_end_transition(session, 3)
        assert res.next_current_entry == up1
        assert res.next_upcoming == (up2, curr)
        assert res.next_state == PlaybackState.PLAYING
        assert res.increment_generation

    def test_loop_queue_with_empty_upcoming(self) -> None:
        curr = make_entry(guild_id=123, title="Current")
        session = VersionedGuildSession(
            guild_id=123,
            state=PlaybackState.PLAYING,
            current_entry=curr,
            upcoming=(),
            loop_mode=LoopMode.QUEUE,
            generation=3,
        )
        res = compute_track_end_transition(session, 3)
        assert res.next_current_entry == curr
        assert res.next_upcoming == ()
        assert res.next_state == PlaybackState.PLAYING
        assert res.increment_generation
