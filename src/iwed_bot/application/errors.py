"""Hierarki typed error pada Application Layer untuk Iwed Discord Music Bot.

Seluruh exception mewarisi IwedApplicationError dan merepresentasikan
kondisi validasi atau kegagalan operasional interaksi pengguna.
"""


class IwedApplicationError(Exception):
    """Base class untuk seluruh application errors Iwed."""


class GuildOnlyCommand(IwedApplicationError):
    """Perintah dijalankan di luar context guild (misalnya di Direct Message)."""

    def __init__(
        self,
        message: str = "Perintah ini hanya dapat digunakan di dalam server Discord.",
    ) -> None:
        super().__init__(message)


class UserNotInVoice(IwedApplicationError):
    """Pengguna tidak berada dalam voice channel saat menjalankan perintah."""

    def __init__(
        self,
        message: str = "Masuk ke voice channel terlebih dahulu.",
    ) -> None:
        super().__init__(message)


class UnsupportedVoiceChannel(IwedApplicationError):
    """Tipe channel tidak didukung (misalnya Stage Channel atau channel non-audio)."""

    def __init__(
        self,
        message: str = "Iwed saat ini hanya mendukung voice channel biasa (bukan Stage Channel).",
    ) -> None:
        super().__init__(message)


class DifferentVoiceChannel(IwedApplicationError):
    """Pengguna dan bot berada pada voice channel yang berbeda."""

    def __init__(
        self,
        message: str = "Kamu harus berada di voice channel yang sama dengan bot.",
    ) -> None:
        super().__init__(message)


class BotMissingVoicePermission(IwedApplicationError):
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


class VoiceChannelFull(IwedApplicationError):
    """Voice channel tujuan telah mencapai batas maksimum pengguna."""

    def __init__(
        self,
        message: str = "Voice channel tujuan sudah penuh.",
    ) -> None:
        super().__init__(message)


class LavalinkUnavailable(IwedApplicationError):
    """Node audio Lavalink sedang terputus atau tidak siap."""

    def __init__(
        self,
        message: str = (
            "Layanan audio (Lavalink) sedang tidak tersedia. Coba lagi dalam beberapa saat."
        ),
    ) -> None:
        super().__init__(message)


class VoiceConnectionFailed(IwedApplicationError):
    """Gagal melakukan koneksi voice ke channel tujuan."""

    def __init__(
        self,
        message: str = "Gagal menghubungkan ke voice channel. Silakan coba lagi.",
    ) -> None:
        super().__init__(message)


class VoiceMoveFailed(IwedApplicationError):
    """Gagal memindahkan bot ke voice channel baru."""

    def __init__(
        self,
        message: str = "Gagal memindahkan bot ke voice channel baru.",
    ) -> None:
        super().__init__(message)


class VoiceDisconnectFailed(IwedApplicationError):
    """Gagal memutuskan koneksi bot dari voice channel."""

    def __init__(
        self,
        message: str = "Gagal memutuskan koneksi dari voice channel.",
    ) -> None:
        super().__init__(message)


class UnexpectedVoiceClient(IwedApplicationError):
    """Voice client terdaftar bukan merupakan instance Wavelink Player yang diharapkan."""

    def __init__(
        self,
        message: str = "Terjadi kesalahan internal pada voice client bot.",
    ) -> None:
        super().__init__(message)


class ConcurrentVoiceOperation(IwedApplicationError):
    """Operasi voice lain pada guild yang sama sedang berlangsung."""

    def __init__(
        self,
        message: str = "Operasi voice lain sedang berlangsung pada server ini. Silakan coba lagi.",
    ) -> None:
        super().__init__(message)


class InvalidPlayQuery(IwedApplicationError):
    """Query pencarian atau URL /play tidak valid."""

    def __init__(
        self,
        message: str = (
            "Query pencarian tidak valid. Masukkan judul lagu atau "
            "URL YouTube yang sah (1-500 karakter)."
        ),
    ) -> None:
        super().__init__(message)


class UnsupportedSource(IwedApplicationError):
    """Sumber URL tidak didukung oleh bot pada fase ini."""

    def __init__(
        self,
        message: str = (
            "Sumber tautan tidak didukung. Iwed saat ini mendukung "
            "pencarian teks dan URL video tunggal YouTube."
        ),
    ) -> None:
        super().__init__(message)


class PlaylistImportDeferred(IwedApplicationError):
    """Tautan playlist terdeteksi tetapi import playlist baru didukung pada Fase 5."""

    def __init__(
        self,
        message: str = (
            "Tautan playlist terdeteksi. Fitur import playlist lengkap akan hadir pada Fase 5. "
            "Untuk saat ini, silakan masukkan URL lagu tunggal."
        ),
    ) -> None:
        super().__init__(message)


class SpotifySourceDeferred(IwedApplicationError):
    """Tautan Spotify terdeteksi tetapi integrasi Spotify baru didukung pada Fase 5."""

    def __init__(
        self,
        message: str = (
            "Tautan Spotify terdeteksi. "
            "Dukungan integrasi metadata Spotify akan tersedia pada Fase 5."
        ),
    ) -> None:
        super().__init__(message)


class TrackNotFound(IwedApplicationError):
    """Lagu tidak ditemukan pada penyedia audio."""

    def __init__(
        self,
        message: str = (
            "Lagu tidak ditemukan. Coba gunakan kata kunci pencarian yang lebih spesifik."
        ),
    ) -> None:
        super().__init__(message)


class SourceLoadFailed(IwedApplicationError):
    """Gagal memuat audio dari penyedia sumber."""

    def __init__(
        self,
        message: str = (
            "Gagal memuat audio dari sumber penyedia. "
            "Silakan coba lagu lain atau ulangi sesaat lagi."
        ),
    ) -> None:
        super().__init__(message)


class SourceTimeout(IwedApplicationError):
    """Waktu pencarian lagu habis (timeout)."""

    def __init__(
        self,
        message: str = (
            "Waktu pencarian lagu habis (timeout). Silakan periksa koneksi atau coba sesaat lagi."
        ),
    ) -> None:
        super().__init__(message)


class PlaybackFailed(IwedApplicationError):
    """Kegagalan memulai pemutaran audio di node Lavalink."""

    def __init__(
        self,
        message: str = "Terjadi kegagalan pada layanan audio saat memutar lagu.",
    ) -> None:
        super().__init__(message)


class NothingPlaying(IwedApplicationError):
    """Operasi kontrol antrean gagal karena tidak ada lagu yang sedang diputar."""

    def __init__(
        self,
        message: str = "Tidak ada lagu yang sedang diputar saat ini.",
    ) -> None:
        super().__init__(message)


class AlreadyPaused(IwedApplicationError):
    """Pemutaran musik sudah dalam keadaan dijeda."""

    def __init__(
        self,
        message: str = "Pemutaran musik sudah dalam keadaan dijeda (paused).",
    ) -> None:
        super().__init__(message)


class NotPaused(IwedApplicationError):
    """Pemutaran musik sedang berjalan dan tidak dalam keadaan jeda."""

    def __init__(
        self,
        message: str = "Pemutaran musik sedang berjalan (tidak dalam keadaan jeda).",
    ) -> None:
        super().__init__(message)


class QueuePageOutOfRange(IwedApplicationError):
    """Nomor halaman antrean di luar rentang halaman yang tersedia."""

    def __init__(
        self,
        max_page: int = 1,
        message: str | None = None,
    ) -> None:
        self.max_page = max_page
        msg = (
            message
            or f"Nomor halaman antrean tidak valid. Halaman yang tersedia: 1 sampai {max_page}."
        )
        super().__init__(msg)


class EntrySuperseded(IwedApplicationError):
    """Lagu dibatalkan atau dilewati saat menunggu giliran putar."""

    def __init__(
        self,
        message: str = "Lagu telah dilewati atau dibatalkan dari antrean.",
    ) -> None:
        super().__init__(message)


class PlaybackReconciliationFailed(IwedApplicationError):
    """Rekonsiliasi status pemutaran gagal setelah operasi audio fisik."""

    def __init__(
        self,
        message: str = "Gagal merekonsiliasi status pemutaran audio.",
    ) -> None:
        super().__init__(message)


class CompliantSourceUnavailable(IwedApplicationError):
    """Sumber audio tidak tersedia pada mode kebijakan compliance-first."""

    def __init__(
        self,
        message: str = (
            "Sumber audio belum tersedia pada mode compliance-first. "
            "Silakan aktifkan mode prototype jika diizinkan."
        ),
    ) -> None:
        super().__init__(message)
