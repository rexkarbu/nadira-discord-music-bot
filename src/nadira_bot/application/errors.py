"""Hierarki typed error pada Application Layer untuk Nadira Discord Music Bot.

Seluruh exception mewarisi NadiraApplicationError dan merepresentasikan
kondisi validasi atau kegagalan operasional interaksi pengguna.
"""


class NadiraApplicationError(Exception):
    """Base class untuk seluruh application errors Nadira."""


class GuildOnlyCommand(NadiraApplicationError):
    """Perintah dijalankan di luar context guild (misalnya di Direct Message)."""

    def __init__(
        self,
        message: str = "Perintah ini hanya dapat digunakan di dalam server Discord.",
    ) -> None:
        super().__init__(message)


class UserNotInVoice(NadiraApplicationError):
    """Pengguna tidak berada dalam voice channel saat menjalankan perintah."""

    def __init__(
        self,
        message: str = "Masuk ke voice channel terlebih dahulu.",
    ) -> None:
        super().__init__(message)


class UnsupportedVoiceChannel(NadiraApplicationError):
    """Tipe channel tidak didukung (misalnya Stage Channel atau channel non-audio)."""

    def __init__(
        self,
        message: str = "Nadira saat ini hanya mendukung voice channel biasa (bukan Stage Channel).",
    ) -> None:
        super().__init__(message)


class DifferentVoiceChannel(NadiraApplicationError):
    """Pengguna dan bot berada pada voice channel yang berbeda."""

    def __init__(
        self,
        message: str = "Kamu harus berada di voice channel yang sama dengan bot.",
    ) -> None:
        super().__init__(message)


class BotMissingVoicePermission(NadiraApplicationError):
    """Bot kekurangan permission Discord tertentu di voice channel tujuan."""

    def __init__(
        self,
        missing_permissions: tuple[str, ...],
        message: str | None = None,
    ) -> None:
        self.missing_permissions = missing_permissions
        perms_str = ", ".join(missing_permissions)
        msg = message or f"Bot tidak memiliki izin yang diperlukan: {perms_str}."
        super().__init__(msg)


class VoiceChannelFull(NadiraApplicationError):
    """Voice channel tujuan telah mencapai batas maksimum pengguna."""

    def __init__(
        self,
        message: str = "Voice channel tujuan sudah penuh.",
    ) -> None:
        super().__init__(message)


class LavalinkUnavailable(NadiraApplicationError):
    """Node audio Lavalink sedang terputus atau tidak siap."""

    def __init__(
        self,
        message: str = (
            "Layanan audio (Lavalink) sedang tidak tersedia. Coba lagi dalam beberapa saat."
        ),
    ) -> None:
        super().__init__(message)


class VoiceConnectionFailed(NadiraApplicationError):
    """Gagal melakukan koneksi voice ke channel tujuan."""

    def __init__(
        self,
        message: str = "Gagal menghubungkan ke voice channel. Silakan coba lagi.",
    ) -> None:
        super().__init__(message)


class VoiceMoveFailed(NadiraApplicationError):
    """Gagal memindahkan bot ke voice channel baru."""

    def __init__(
        self,
        message: str = "Gagal memindahkan bot ke voice channel baru.",
    ) -> None:
        super().__init__(message)


class VoiceDisconnectFailed(NadiraApplicationError):
    """Gagal memutuskan koneksi bot dari voice channel."""

    def __init__(
        self,
        message: str = "Gagal memutuskan koneksi dari voice channel.",
    ) -> None:
        super().__init__(message)


class UnexpectedVoiceClient(NadiraApplicationError):
    """Voice client terdaftar bukan merupakan instance Wavelink Player yang diharapkan."""

    def __init__(
        self,
        message: str = "Terjadi kesalahan internal pada voice client bot.",
    ) -> None:
        super().__init__(message)


class ConcurrentVoiceOperation(NadiraApplicationError):
    """Operasi voice lain pada guild yang sama sedang berlangsung."""

    def __init__(
        self,
        message: str = "Operasi voice lain sedang berlangsung pada server ini. Silakan coba lagi.",
    ) -> None:
        super().__init__(message)
