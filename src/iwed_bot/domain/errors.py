"""Domain exception classes untuk Iwed Discord Music Bot.

Modul ini mendefinisikan seluruh hierarki typed errors murni pada layer domain.
Seluruh exception mewarisi IwedDomainError dan bebas dari dependensi eksternal.
"""


class IwedDomainError(Exception):
    """Base class untuk seluruh domain errors Iwed."""


class QueueEmpty(IwedDomainError):
    """Antrean kosong saat operasi membutuhkan item."""


class QueueFull(IwedDomainError):
    """Penambahan antrean melebihi batas kapasitas maksimum."""


class QueuePositionOutOfRange(IwedDomainError):
    """Posisi 1-based index di luar rentang upcoming queue."""


class VersionConflict(IwedDomainError):
    """expected_version tidak cocok dengan active version sesi."""


class GuildMismatch(IwedDomainError):
    """QueueEntry.guild_id tidak sesuai dengan target guild session."""


class InvalidVolume(IwedDomainError):
    """Volume berada di luar rentang sah (0 - 100)."""


class InvalidStateTransition(IwedDomainError):
    """Transisi status playback tidak sah, atau precondition operasi tidak terpenuhi."""


class StalePlaybackEvent(IwedDomainError):
    """Event playback membawa generation token usang atau tidak cocok."""


class DuplicateQueueEntry(IwedDomainError):
    """QueueEntry.id duplikat terdeteksi dalam antrean atau batch operasi."""
