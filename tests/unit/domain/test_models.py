"""Unit tests untuk domain models dan value objects Iwed."""

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from iwed_bot.domain.models import (
    ALLOWED_STATE_TRANSITIONS,
    UNSET,
    PlaybackState,
    PlaylistContext,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
    VersionedGuildSession,
)


def make_track(
    track_id: uuid.UUID | None = None,
    title: str = "Test Title",
    search_hint: str = "Test Artist - Test Title",
    duration_ms: int | None = 180000,
) -> TrackReference:
    return TrackReference(
        id=track_id or uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="dQw4w9WgXcQ",
        source_uri="https://youtube.com/watch?v=dQw4w9WgXcQ",
        search_hint=search_hint,
        title=title,
        artists=("Test Artist",),
        duration_ms=duration_ms,
        thumbnail_url="https://example.com/thumb.jpg",
        canonical_url="https://example.com/track",
    )


def make_entry(
    guild_id: int = 123456789,
    entry_id: uuid.UUID | None = None,
    duration_ms: int | None = 180000,
) -> QueueEntry:
    return QueueEntry(
        id=entry_id or uuid.uuid4(),
        guild_id=guild_id,
        track=make_track(duration_ms=duration_ms),
        requested_by_user_id=987654321,
        requested_in_channel_id=1122334455,
        enqueued_at=datetime.now(UTC),
    )


class TestTrackReference:
    def test_valid_creation(self) -> None:
        track = make_track()
        assert track.title == "Test Title"
        assert track.duration_ms == 180000
        assert not track.is_stream

    def test_immutability(self) -> None:
        track = make_track()
        with pytest.raises(FrozenInstanceError):
            track.title = "New Title"  # type: ignore[misc]

    def test_invalid_uuid(self) -> None:
        with pytest.raises(TypeError, match="TrackReference.id wajib bertipe UUID"):
            TrackReference(
                id="not-a-uuid",  # type: ignore[arg-type]
                source_type=SourceType.YOUTUBE,
                source_id="123",
                source_uri=None,
                search_hint="artist - title",
                title="title",
                artists=(),
                duration_ms=1000,
                thumbnail_url=None,
                canonical_url=None,
            )

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValueError, match="title tidak boleh kosong"):
            make_track(title="   ")

    def test_empty_search_hint_rejected(self) -> None:
        with pytest.raises(ValueError, match="search_hint tidak boleh kosong"):
            make_track(search_hint="")

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match="duration_ms harus int >= 0"):
            make_track(duration_ms=-10)

    def test_duration_boolean_rejected(self) -> None:
        with pytest.raises(ValueError, match="duration_ms harus int >= 0"):
            make_track(duration_ms=True)  # type: ignore[arg-type]

    def test_none_duration_allowed(self) -> None:
        track = make_track(duration_ms=None)
        assert track.duration_ms is None


class TestPlaylistContext:
    def test_valid_creation(self) -> None:
        ctx = PlaylistContext(playlist_id="p1", playlist_name="My Playlist", position=1)
        assert ctx.position == 1

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="playlist_id tidak boleh kosong"):
            PlaylistContext(playlist_id="", playlist_name="Name", position=1)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="playlist_name tidak boleh kosong"):
            PlaylistContext(playlist_id="p1", playlist_name="   ", position=1)

    def test_invalid_position_rejected(self) -> None:
        with pytest.raises(ValueError, match="playlist position harus int >= 1"):
            PlaylistContext(playlist_id="p1", playlist_name="Name", position=0)

    def test_boolean_position_rejected(self) -> None:
        with pytest.raises(ValueError, match="playlist position harus int >= 1"):
            PlaylistContext(playlist_id="p1", playlist_name="Name", position=True)  # type: ignore[arg-type]


class TestQueueEntry:
    def test_valid_creation(self) -> None:
        entry = make_entry()
        assert entry.guild_id == 123456789
        assert entry.attempt_count == 0

    def test_invalid_snowflake_ids(self) -> None:
        with pytest.raises(ValueError, match="guild_id harus integer positif"):
            QueueEntry(
                id=uuid.uuid4(),
                guild_id=0,
                track=make_track(),
                requested_by_user_id=1,
                requested_in_channel_id=1,
                enqueued_at=datetime.now(UTC),
            )

    def test_boolean_snowflake_rejected(self) -> None:
        with pytest.raises(ValueError, match="guild_id harus integer positif"):
            QueueEntry(
                id=uuid.uuid4(),
                guild_id=True,  # type: ignore[arg-type]
                track=make_track(),
                requested_by_user_id=1,
                requested_in_channel_id=1,
                enqueued_at=datetime.now(UTC),
            )

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware UTC datetime"):
            QueueEntry(
                id=uuid.uuid4(),
                guild_id=123,
                track=make_track(),
                requested_by_user_id=1,
                requested_in_channel_id=1,
                enqueued_at=datetime.now(),  # naive datetime
            )


class TestVersionedGuildSession:
    def test_default_values(self) -> None:
        session = VersionedGuildSession(guild_id=123)
        assert session.version == 0
        assert session.state == PlaybackState.DISCONNECTED
        assert session.current_entry is None
        assert session.upcoming == ()
        assert session.volume == 70
        assert session.generation == 0
        assert session.queue_length == 0
        assert session.is_empty
        assert session.exact_total_duration_ms == 0

    def test_negative_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="version harus int >= 0"):
            VersionedGuildSession(guild_id=123, version=-1)

    def test_boolean_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="version harus int >= 0"):
            VersionedGuildSession(guild_id=123, version=True)  # type: ignore[arg-type]

    def test_invalid_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match="volume harus int antara 0 dan 100"):
            VersionedGuildSession(guild_id=123, volume=101)

    def test_boolean_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match="volume harus int antara 0 dan 100"):
            VersionedGuildSession(guild_id=123, volume=True)  # type: ignore[arg-type]

    def test_invalid_channel_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="voice_channel_id harus integer positif"):
            VersionedGuildSession(guild_id=123, voice_channel_id=0)

        with pytest.raises(ValueError, match="text_channel_id harus integer positif"):
            VersionedGuildSession(guild_id=123, text_channel_id=-5)

    def test_non_tuple_upcoming_rejected(self) -> None:
        entry = make_entry(guild_id=123)
        with pytest.raises(TypeError, match="upcoming wajib bertipe tuple"):
            VersionedGuildSession(guild_id=123, upcoming=[entry])  # type: ignore[arg-type]

    def test_cross_guild_current_entry_rejected(self) -> None:
        entry = make_entry(guild_id=999)
        with pytest.raises(ValueError, match="current_entry.guild_id harus sama"):
            VersionedGuildSession(guild_id=123, state=PlaybackState.PLAYING, current_entry=entry)

    def test_cross_guild_upcoming_entry_rejected(self) -> None:
        entry = make_entry(guild_id=999)
        with pytest.raises(
            ValueError, match="Setiap upcoming entry harus memiliki guild_id yang sama"
        ):
            VersionedGuildSession(guild_id=123, upcoming=(entry,))

    def test_state_current_entry_consistency(self) -> None:
        entry = make_entry(guild_id=123)

        # PLAYING / PAUSED wajib memiliki current_entry
        with pytest.raises(ValueError, match="Status 'playing' wajib memiliki current_entry"):
            VersionedGuildSession(guild_id=123, state=PlaybackState.PLAYING, current_entry=None)

        with pytest.raises(ValueError, match="Status 'paused' wajib memiliki current_entry"):
            VersionedGuildSession(guild_id=123, state=PlaybackState.PAUSED, current_entry=None)

        # IDLE / DISCONNECTED / CONNECTING wajib current_entry=None
        with pytest.raises(ValueError, match="wajib memiliki current_entry bernilai None"):
            VersionedGuildSession(guild_id=123, state=PlaybackState.IDLE, current_entry=entry)

        with pytest.raises(ValueError, match="wajib memiliki current_entry bernilai None"):
            VersionedGuildSession(
                guild_id=123, state=PlaybackState.DISCONNECTED, current_entry=entry
            )

        with pytest.raises(ValueError, match="wajib memiliki current_entry bernilai None"):
            VersionedGuildSession(guild_id=123, state=PlaybackState.CONNECTING, current_entry=entry)

    def test_current_entry_in_upcoming_rejected(self) -> None:
        entry = make_entry(guild_id=123)
        with pytest.raises(
            ValueError, match="current_entry.id tidak boleh muncul juga di upcoming"
        ):
            VersionedGuildSession(
                guild_id=123,
                state=PlaybackState.PLAYING,
                current_entry=entry,
                upcoming=(entry,),
            )

    def test_duplicate_entry_id_in_upcoming_rejected(self) -> None:
        entry = make_entry(guild_id=123)
        with pytest.raises(
            ValueError, match="Terdapat QueueEntry.id duplikat di dalam upcoming queue"
        ):
            VersionedGuildSession(
                guild_id=123,
                upcoming=(entry, entry),
            )

    def test_same_track_different_entry_ids_allowed(self) -> None:
        track = make_track(title="Identical Track")
        e1 = QueueEntry(
            id=uuid.uuid4(),
            guild_id=123,
            track=track,
            requested_by_user_id=1,
            requested_in_channel_id=1,
            enqueued_at=datetime.now(UTC),
        )
        e2 = QueueEntry(
            id=uuid.uuid4(),
            guild_id=123,
            track=track,
            requested_by_user_id=1,
            requested_in_channel_id=1,
            enqueued_at=datetime.now(UTC),
        )
        session = VersionedGuildSession(guild_id=123, upcoming=(e1, e2))
        assert session.queue_length == 2

    def test_duration_metrics(self) -> None:
        e1 = make_entry(guild_id=123, duration_ms=60000)
        e2 = make_entry(guild_id=123, duration_ms=120000)
        session = VersionedGuildSession(guild_id=123, upcoming=(e1, e2))
        assert session.queue_length == 2
        assert not session.is_empty
        assert session.known_total_duration_ms == 180000
        assert session.unknown_duration_count == 0
        assert session.exact_total_duration_ms == 180000

    def test_duration_metrics_with_unknown_duration(self) -> None:
        e1 = make_entry(guild_id=123, duration_ms=60000)
        e2 = make_entry(guild_id=123, duration_ms=None)
        session = VersionedGuildSession(guild_id=123, upcoming=(e1, e2))
        assert session.known_total_duration_ms == 60000
        assert session.unknown_duration_count == 1
        assert session.exact_total_duration_ms is None


class TestSessionStateUpdateAndTransitions:
    def test_unset_sentinel(self) -> None:
        update = SessionStateUpdate()
        assert update.state is UNSET
        assert update.voice_channel_id is UNSET
        assert repr(UNSET) == "UNSET"

    def test_allowed_state_transitions_matrix_contains_keys(self) -> None:
        assert PlaybackState.DISCONNECTED in ALLOWED_STATE_TRANSITIONS
        assert PlaybackState.CONNECTING in ALLOWED_STATE_TRANSITIONS
        assert PlaybackState.IDLE in ALLOWED_STATE_TRANSITIONS
        assert PlaybackState.PLAYING in ALLOWED_STATE_TRANSITIONS
        assert PlaybackState.PAUSED in ALLOWED_STATE_TRANSITIONS
        assert PlaybackState.STOPPING in ALLOWED_STATE_TRANSITIONS
