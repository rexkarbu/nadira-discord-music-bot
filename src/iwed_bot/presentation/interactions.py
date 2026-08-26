"""Helper utilities untuk interaksi Discord dan format respons aman."""

import logging
import uuid
from typing import Any

import discord
from discord import app_commands

from iwed_bot.application.errors import (
    AlreadyPaused,
    BotMissingVoicePermission,
    CompliantSourceUnavailable,
    ConcurrentVoiceOperation,
    DifferentVoiceChannel,
    GuildOnlyCommand,
    InvalidPlayQuery,
    IwedApplicationError,
    LavalinkUnavailable,
    NothingPlaying,
    NotPaused,
    PlaybackFailed,
    PlaylistImportDeferred,
    QueuePageOutOfRange,
    SourceLoadFailed,
    SourceTimeout,
    SpotifySourceDeferred,
    TrackNotFound,
    UnexpectedVoiceClient,
    UnsupportedSource,
    UnsupportedVoiceChannel,
    UserNotInVoice,
    VoiceChannelFull,
    VoiceConnectionFailed,
    VoiceDisconnectFailed,
    VoiceMoveFailed,
)
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

logger = logging.getLogger(__name__)


def safe_truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Memotong string secara aman jika melebihi batas panjang maksimum."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    cut = max(0, max_length - len(suffix))
    return text[:cut] + suffix


def escape_markdown(text: str) -> str:
    """Membersihkan karakter markdown Discord yang berpotensi merusak embed."""
    if not text:
        return ""
    return discord.utils.escape_markdown(text)


def format_duration(duration_ms: int | None, is_stream: bool = False) -> str:
    """Memformat durasi ms menjadi representasi MM:SS atau LIVE."""
    if is_stream or duration_ms is None:
        return "LIVE"
    total_seconds = max(0, duration_ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def unwrap_command_error(error: BaseException) -> BaseException:
    """Membongkar pembungkus CommandInvokeError untuk mendapatkan exception asli."""
    current = error
    while isinstance(current, (app_commands.CommandInvokeError, app_commands.AppCommandError)):
        orig = getattr(current, "original", None)
        if orig is not None and isinstance(orig, BaseException):
            current = orig
        else:
            break
    return current


def format_user_error_message(error: BaseException, correlation_id: uuid.UUID) -> str:
    """Menerjemahkan typed domain/application error menjadi pesan Bahasa Indonesia yang aman."""
    if isinstance(error, GuildOnlyCommand):
        return "[ERROR] Perintah ini hanya dapat digunakan di dalam server Discord."

    if isinstance(error, UserNotInVoice):
        return f"[ERROR] {error}"

    if isinstance(error, UnsupportedVoiceChannel):
        return "[ERROR] Iwed saat ini hanya mendukung voice channel biasa (bukan Stage Channel)."

    if isinstance(error, DifferentVoiceChannel):
        return f"[ERROR] {error}"

    if isinstance(error, BotMissingVoicePermission):
        perms = ", ".join(error.missing_permissions)
        return f"[ERROR] Bot kekurangan izin Discord di voice channel: **{perms}**."

    if isinstance(error, VoiceChannelFull):
        return "[ERROR] Voice channel tujuan sudah penuh."

    if isinstance(error, LavalinkUnavailable):
        return (
            "[ERROR] Layanan audio (Lavalink) sedang tidak tersedia. Coba lagi dalam beberapa saat."
        )

    if isinstance(error, VoiceConnectionFailed):
        return "[ERROR] Gagal menghubungkan ke voice channel. Silakan coba lagi."

    if isinstance(error, VoiceMoveFailed):
        return "[ERROR] Gagal memindahkan bot ke voice channel baru."

    if isinstance(error, VoiceDisconnectFailed):
        return "[ERROR] Gagal memutuskan koneksi dari voice channel."

    if isinstance(error, UnexpectedVoiceClient):
        return "[ERROR] Terjadi kesalahan pada koneksi voice bot."

    if isinstance(error, ConcurrentVoiceOperation):
        return "[WAIT] Operasi voice lain sedang berlangsung. Silakan coba sesaat lagi."

    # Phase 4 typed errors
    if isinstance(error, SourceLoadFailed):
        return (
            "[ERROR] Gagal memuat audio dari sumber penyedia. "
            "Silakan coba lagu lain atau ulangi sesaat lagi."
        )

    if isinstance(
        error,
        (
            InvalidPlayQuery,
            UnsupportedSource,
            TrackNotFound,
            SourceTimeout,
            QueuePageOutOfRange,
        ),
    ):
        return f"[ERROR] {error}"

    if isinstance(
        error,
        (
            PlaylistImportDeferred,
            SpotifySourceDeferred,
            CompliantSourceUnavailable,
            NothingPlaying,
            AlreadyPaused,
            NotPaused,
        ),
    ):
        return f"[INFO] {error}"

    if isinstance(error, PlaybackFailed):
        return (
            "[ERROR] Terjadi kegagalan pada layanan audio saat memutar lagu. "
            f"ID laporan: `{correlation_id}`"
        )

    if isinstance(
        error,
        (QueueEmpty, QueueFull, QueuePositionOutOfRange, DuplicateQueueEntry),
    ):
        return f"[ERROR] Operasi antrean tidak valid: {error}"

    if isinstance(
        error,
        (
            VersionConflict,
            GuildMismatch,
            InvalidStateTransition,
            StalePlaybackEvent,
            InvalidVolume,
        ),
    ):
        return (
            "[ERROR] Terjadi inkonsistensi state atau transisi tidak valid. "
            "Silakan ulangi perintah."
        )

    if isinstance(error, (IwedApplicationError, IwedDomainError)):
        return f"[ERROR] {error}"

    # Unknown / Unhandled internal error
    return f"[WARNING] Terjadi kesalahan internal. ID laporan: `{correlation_id}`"


async def respond_or_edit(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = True,
) -> Any:
    """Mengirim respons interaksi dengan penanganan status deferred/responded yang aman."""
    send_kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if content is not None:
        send_kwargs["content"] = content
    if embed is not None:
        send_kwargs["embed"] = embed

    if not interaction.response.is_done():
        return await interaction.response.send_message(**send_kwargs)

    try:
        return await interaction.edit_original_response(
            content=content,
            embed=embed,
        )
    except discord.HTTPException:
        return await interaction.followup.send(**send_kwargs)
