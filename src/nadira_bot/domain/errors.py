"""Domain exception classes untuk Nadira Discord Music Bot.

Modul ini mendefinisikan seluruh hierarki typed errors murni pada layer domain.
Seluruh exception mewarisi NadiraDomainError dan bebas dari dependensi eksternal.
"""


class NadiraDomainError(Exception):
    """Base class untuk seluruh domain errors Nadira."""


class QueueEmpty(NadiraDomainError):
    """Antrean kosong saat operasi membutuhkan item."""


class QueueFull(NadiraDomainError):
    """Penambahan antrean melebihi batas kapasitas maksimum."""


class QueuePositionOutOfRange(NadiraDomainError):
    """Posisi 1-based index di luar rentang upcoming queue."""


class VersionConflict(NadiraDomainError):
    """expected_version tidak cocok dengan active version sesi."""


class GuildMismatch(NadiraDomainError):
    """QueueEntry.guild_id tidak sesuai dengan target guild session."""


class InvalidVolume(NadiraDomainError):
    """Volume berada di luar rentang sah (0 - 100)."""


class InvalidStateTransition(NadiraDomainError):
    """Transisi status playback tidak sah, atau precondition operasi tidak terpenuhi."""


class StalePlaybackEvent(NadiraDomainError):
    """Event playback membawa generation token usang atau tidak cocok."""
