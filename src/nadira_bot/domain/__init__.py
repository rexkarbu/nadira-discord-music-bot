"""Domain layer Nadira Discord Music Bot.

Menyediakan data models murni, typed exceptions, transition functions, dan value objects.
"""

from nadira_bot.domain.errors import (
    GuildMismatch,
    InvalidStateTransition,
    InvalidVolume,
    NadiraDomainError,
    QueueEmpty,
    QueueFull,
    QueuePositionOutOfRange,
    StalePlaybackEvent,
    VersionConflict,
)
from nadira_bot.domain.models import (
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
    VersionedGuildSession,
)

__all__ = [
    "ALLOWED_STATE_TRANSITIONS",
    "UNSET",
    "GuildMismatch",
    "InvalidStateTransition",
    "InvalidVolume",
    "LoopMode",
    "NadiraDomainError",
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
    "VersionConflict",
    "VersionedGuildSession",
]
