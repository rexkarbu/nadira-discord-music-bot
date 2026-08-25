"""Sistem logging terstruktur (structured JSON logging) untuk Nadira Discord Music Bot.

Menyediakan formatter JSON yang menyertakan timestamp UTC standar ISO-8601,
level log, logger name, event message, environment, serta atribut kustom (misal: correlation_id,
guild_id) tanpa membocorkan kredensial atau informasi rahasia.
"""

import json
import logging
import os
import platform
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nadira_bot.settings import Settings

# Atribut standar logging record Python yang diabaikan saat mengekstrak custom extra fields
_STANDARD_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """Formatter untuk menghasilkan baris log tunggal berformat JSON terstruktur."""

    def __init__(self, environment: str | None = None) -> None:
        super().__init__()
        self.environment = environment or os.getenv("APP_ENV", "development")

    def format(self, record: logging.LogRecord) -> str:
        """Mengubah LogRecord menjadi string JSON yang aman dan terstruktur."""
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "environment": self.environment,
        }

        # Ekstrak extra context fields jika disertakan (misal: correlation_id, guild_id)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_"):
                log_payload[key] = value

        # Tambahkan exception traceback jika terjadi error
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload, default=str, ensure_ascii=False)


def setup_logging(log_level: str = "INFO") -> None:
    """Mengonfigurasi root logger aplikasi dengan StructuredJsonFormatter.

    Menyesuaikan logger third-party agar tidak memenuhi output dengan heartbeat spam.
    """
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Hapus handler yang sudah ada untuk menghindari duplikasi log
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(StructuredJsonFormatter())
    root_logger.addHandler(stream_handler)

    # Redam logger third-party yang terlalu bising pada level DEBUG/INFO
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.ext.commands.bot").setLevel(logging.ERROR)
    logging.getLogger("wavelink.websocket").setLevel(logging.INFO)


def log_startup_info(logger: logging.Logger, settings: "Settings") -> None:
    """Mencatat informasi runtime startup sistem tanpa membocorkan kredensial rahasia."""
    import discord
    import pydantic
    import wavelink

    startup_metadata = {
        "bot_name": "Nadira",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "discord_py_version": discord.__version__,
        "wavelink_version": getattr(wavelink, "__version__", "unknown"),
        "pydantic_version": pydantic.__version__,
        "source_policy_mode": settings.SOURCE_POLICY_MODE,
        "queue_backend": settings.QUEUE_BACKEND,
        "idle_disconnect_seconds": settings.IDLE_DISCONNECT_SECONDS,
        "max_playlist_tracks": settings.MAX_PLAYLIST_TRACKS,
        "queue_max_tracks": settings.QUEUE_MAX_TRACKS,
    }

    logger.info(
        "Memulai Nadira Music Bot runtime engine",
        extra={"startup_metadata": startup_metadata},
    )
