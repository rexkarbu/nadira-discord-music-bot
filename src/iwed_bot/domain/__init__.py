"""Domain layer Iwed Discord Music Bot.

Menyediakan data models murni, typed exceptions, transition functions, dan value objects.
"""

from iwed_bot.domain.errors import (
    DuplicateQueueEntry,
    GuildMismatch,
    InvalidStateTransition,
    InvalidVolume,
    IwedDomainError,
    QueueEmpty,
    QueueFull,
    QueuePositionOutOfRange,
    StalePlaybackEvent,
    VersionConflict,
)
from iwed_bot.domain.models import (
    ALLOWED_STATE_TRANSITIONS,
    UNSET,
    LoopMode,
    PlaybackState,
    PlaybackTransition,
    PlaylistContext,
    QueueEntry,
    SessionStateUpdate,
    SourceType,
    TrackReference,
    UnsetType,
    VersionedGuildSession,
)

__all__ = [
    "ALLOWED_STATE_TRANSITIONS",
    "UNSET",
    "DuplicateQueueEntry",
    "GuildMismatch",
    "InvalidStateTransition",
    "InvalidVolume",
    "LoopMode",
    "IwedDomainError",
    "PlaybackState",
    "PlaybackTransition",
    "PlaylistContext",
    "QueueEmpty",
    "QueueEntry",
    "QueueFull",
    "QueuePositionOutOfRange",
    "SessionStateUpdate",
    "SourceType",
    "StalePlaybackEvent",
    "TrackReference",
    "UnsetType",
    "VersionConflict",
    "VersionedGuildSession",
]
