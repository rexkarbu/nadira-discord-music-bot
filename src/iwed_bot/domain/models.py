"""Domain data models, value objects, dan enums untuk Iwed Discord Music Bot.

Modul ini mendefinisikan entitas domain murni yang bersifat immutable (frozen dataclasses)
dengan penegakan validasi runtime ketat pada __post_init__.
Bebas dari segala dependensi eksternal.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class SourceType(StrEnum):
    """Tipe sumber audio atau platform resolusi track."""

    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    DIRECT = "direct"
    PROTOTYPE = "prototype"


class LoopMode(StrEnum):
    """Mode perulangan pemutaran musik."""

    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class PlaybackState(StrEnum):
    """Status siklus hidup pemutaran audio per guild."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"


ALLOWED_STATE_TRANSITIONS: dict[PlaybackState, frozenset[PlaybackState]] = {
    PlaybackState.DISCONNECTED: frozenset({PlaybackState.CONNECTING}),
    PlaybackState.CONNECTING: frozenset({PlaybackState.IDLE, PlaybackState.DISCONNECTED}),
    PlaybackState.IDLE: frozenset(
        {PlaybackState.PLAYING, PlaybackState.STOPPING, PlaybackState.DISCONNECTED}
    ),
    PlaybackState.PLAYING: frozenset(
        {
            PlaybackState.PAUSED,
            PlaybackState.IDLE,
            PlaybackState.STOPPING,
            PlaybackState.DISCONNECTED,
        }
    ),
    PlaybackState.PAUSED: frozenset(
        {PlaybackState.PLAYING, PlaybackState.STOPPING, PlaybackState.DISCONNECTED}
    ),
    PlaybackState.STOPPING: frozenset({PlaybackState.IDLE, PlaybackState.DISCONNECTED}),
}


class UnsetType:
    """Sentinel singleton untuk menandakan field yang tidak mengalami perubahan."""

    _instance: "UnsetType | None" = None

    def __new__(cls) -> "UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


_UnsetType = UnsetType
UNSET = UnsetType()


@dataclass(frozen=True, slots=True)
class TrackReference:
    """Referensi metadata track yang dinormalisasi."""

    id: UUID
    source_type: SourceType
    source_id: str | None
    source_uri: str | None
    search_hint: str
    title: str
    artists: tuple[str, ...]
    duration_ms: int | None
    thumbnail_url: str | None
    canonical_url: str | None
    is_stream: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise TypeError("TrackReference.id wajib bertipe UUID.")
        if not self.title or not self.title.strip():
            raise ValueError("TrackReference.title tidak boleh kosong.")
        if not self.search_hint or not self.search_hint.strip():
            raise ValueError("TrackReference.search_hint tidak boleh kosong.")
        if not isinstance(self.artists, tuple):
            raise TypeError("TrackReference.artists wajib bertipe tuple.")
        if type(self.is_stream) is not bool:
            raise TypeError("is_stream wajib bertipe bool.")
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError("duration_ms harus int >= 0 atau None.")


@dataclass(frozen=True, slots=True)
class PlaylistContext:
    """Konteks sumber playlist asal lagu."""

    playlist_id: str
    playlist_name: str
    position: int

    def __post_init__(self) -> None:
        if not self.playlist_id or not self.playlist_id.strip():
            raise ValueError("playlist_id tidak boleh kosong.")
        if not self.playlist_name or not self.playlist_name.strip():
            raise ValueError("playlist_name tidak boleh kosong.")
        if type(self.position) is not int or self.position < 1:
            raise ValueError("playlist position harus int >= 1.")


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """Entri satuan dalam antrean musik dengan provenance lengkap."""

    id: UUID
    guild_id: int
    track: TrackReference
    requested_by_user_id: int
    requested_in_channel_id: int
    enqueued_at: datetime
    playlist_context: PlaylistContext | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise TypeError("QueueEntry.id wajib bertipe UUID.")
        if type(self.guild_id) is not int or self.guild_id <= 0:
            raise ValueError("guild_id harus integer positif (> 0).")
        if type(self.requested_by_user_id) is not int or self.requested_by_user_id <= 0:
            raise ValueError("requested_by_user_id harus integer positif (> 0).")
        if type(self.requested_in_channel_id) is not int or self.requested_in_channel_id <= 0:
            raise ValueError("requested_in_channel_id harus integer positif (> 0).")
        if self.enqueued_at.tzinfo is None or self.enqueued_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("enqueued_at wajib berupa timezone-aware UTC datetime.")
        if type(self.attempt_count) is not int or self.attempt_count < 0:
            raise ValueError("attempt_count harus int >= 0.")


@dataclass(frozen=True, slots=True)
class SessionStateUpdate:
    """Objek mutasi eksplisit untuk memperbarui metadata sesi guild."""

    state: PlaybackState | _UnsetType = UNSET
    voice_channel_id: int | None | _UnsetType = UNSET
    text_channel_id: int | None | _UnsetType = UNSET
    current_entry: QueueEntry | None | _UnsetType = UNSET
    idle_deadline: datetime | None | _UnsetType = UNSET


@dataclass(frozen=True, slots=True)
class PlaybackTransition:
    """Target perubahan atomik hasil kalkulasi domain transition."""

    next_current_entry: QueueEntry | None
    next_upcoming: tuple[QueueEntry, ...]
    next_state: PlaybackState
    increment_generation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.next_upcoming, tuple):
            raise TypeError("next_upcoming wajib bertipe tuple.")


@dataclass(frozen=True, slots=True)
class VersionedGuildSession:
    """Immutable snapshot sesi guild dengan optimistic versioning."""

    guild_id: int
    version: int = 0
    voice_channel_id: int | None = None
    text_channel_id: int | None = None
    state: PlaybackState = PlaybackState.DISCONNECTED
    current_entry: QueueEntry | None = None
    upcoming: tuple[QueueEntry, ...] = ()
    loop_mode: LoopMode = LoopMode.OFF
    volume: int = 70
    generation: int = 0
    idle_deadline: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.guild_id) is not int or self.guild_id <= 0:
            raise ValueError("guild_id harus integer positif (> 0).")
        if type(self.version) is not int or self.version < 0:
            raise ValueError("version harus int >= 0.")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation harus int >= 0.")
        if type(self.volume) is not int or not (0 <= self.volume <= 100):
            raise ValueError("volume harus int antara 0 dan 100.")
        if self.voice_channel_id is not None and (
            type(self.voice_channel_id) is not int or self.voice_channel_id <= 0
        ):
            raise ValueError("voice_channel_id harus integer positif (> 0) jika tidak None.")
        if self.text_channel_id is not None and (
            type(self.text_channel_id) is not int or self.text_channel_id <= 0
        ):
            raise ValueError("text_channel_id harus integer positif (> 0) jika tidak None.")
        if not isinstance(self.upcoming, tuple):
            raise TypeError("upcoming wajib bertipe tuple.")
        if self.current_entry is not None and self.current_entry.guild_id != self.guild_id:
            raise ValueError("current_entry.guild_id harus sama dengan session.guild_id.")
        for entry in self.upcoming:
            if entry.guild_id != self.guild_id:
                raise ValueError(
                    "Setiap upcoming entry harus memiliki guild_id yang sama dengan session."
                )

        # State and current_entry consistency invariants
        if (
            self.state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
            and self.current_entry is None
        ):
            msg = f"Status '{self.state.value}' wajib memiliki current_entry."
            raise ValueError(msg)
        if (
            self.state in (PlaybackState.IDLE, PlaybackState.DISCONNECTED, PlaybackState.CONNECTING)
            and self.current_entry is not None
        ):
            msg = f"Status '{self.state.value}' wajib memiliki current_entry bernilai None."
            raise ValueError(msg)

        # Uniqueness of QueueEntry.id
        upcoming_ids = [e.id for e in self.upcoming]
        if len(upcoming_ids) != len(set(upcoming_ids)):
            raise ValueError("Terdapat QueueEntry.id duplikat di dalam upcoming queue.")
        if self.current_entry is not None and self.current_entry.id in set(upcoming_ids):
            raise ValueError("current_entry.id tidak boleh muncul juga di upcoming.")

        if self.idle_deadline is not None and (
            self.idle_deadline.tzinfo is None
            or self.idle_deadline.utcoffset() != UTC.utcoffset(None)
        ):
            raise ValueError("idle_deadline wajib berupa timezone-aware UTC datetime.")

    @property
    def queue_length(self) -> int:
        """Jumlah item antrean upcoming saat ini."""
        return len(self.upcoming)

    @property
    def is_empty(self) -> bool:
        """True jika antrean upcoming kosong."""
        return len(self.upcoming) == 0

    @property
    def known_total_duration_ms(self) -> int:
        """Total durasi (ms) dari track di upcoming yang diketahui durasinya."""
        return sum(e.track.duration_ms for e in self.upcoming if e.track.duration_ms is not None)

    @property
    def unknown_duration_count(self) -> int:
        """Jumlah track di upcoming yang durasinya tidak diketahui (misal stream)."""
        return sum(1 for e in self.upcoming if e.track.duration_ms is None)

    @property
    def exact_total_duration_ms(self) -> int | None:
        """Total durasi pasti (ms), atau None jika ada track dengan durasi tidak diketahui."""
        if self.unknown_duration_count > 0:
            return None
        return self.known_total_duration_ms
