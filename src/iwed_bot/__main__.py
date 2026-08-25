"""Entry point utama untuk menjalankan Iwed Discord Music Bot runtime engine."""

import asyncio
import contextlib
import logging
import signal
import sys

import discord
from pydantic import ValidationError

from iwed_bot.bot import IwedBot
from iwed_bot.observability.logging import log_startup_info, setup_logging
from iwed_bot.settings import Settings

logger = logging.getLogger("iwed_bot.main")


async def main() -> int:
    """Fungsi utama untuk memvalidasi konfigurasi, memulai bot Iwed, dan menangani lifecycle."""
    # 1. Inisialisasi logging awal
    setup_logging(log_level="INFO")

    # 2. Muat dan validasi konfigurasi environment
    try:
        settings = Settings()
    except ValidationError as err:
        logger.critical(
            "Konfigurasi environment tidak valid atau kredensial wajib belum diisi!",
            extra={
                "validation_errors": [
                    {
                        "field": ".".join(str(loc) for loc in e.get("loc", [])),
                        "message": e.get("msg"),
                        "type": e.get("type"),
                    }
                    for e in err.errors()
                ],
            },
        )
        return 1
    except Exception as err:
        logger.critical(
            "Terjadi kesalahan saat memuat konfigurasi aplikasi",
            extra={"error": str(err)},
        )
        return 1

    # Perbarui level log sesuai konfigurasi settings
    setup_logging(log_level=settings.LOG_LEVEL)
    log_startup_info(logger, settings)

    # 3. Inisialisasi instance Bot Iwed
    bot = IwedBot(settings)

    # 4. Daftarkan signal handlers untuk graceful shutdown di Linux/Unix jika didukung
    loop = asyncio.get_running_loop()
    shutdown_triggered = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Menerima sinyal terminasi, memicu shutdown...")
        shutdown_triggered.set()
        asyncio.create_task(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Di Windows add_signal_handler tidak didukung di standard loop
            loop.add_signal_handler(sig, _signal_handler)

    # 5. Jalankan bot
    try:
        token = settings.DISCORD_TOKEN.get_secret_value()
        await bot.start(token)
        return 0
    except discord.LoginFailure:
        logger.critical(
            "Gagal login ke Discord: Token DISCORD_TOKEN tidak valid atau ditolak oleh gateway!",
        )
        return 1
    except asyncio.CancelledError:
        logger.info("Eksekusi bot Iwed dibatalkan.")
        return 0
    except Exception:
        logger.exception("Terjadi kesalahan fatal yang tidak tertangani pada runtime bot.")
        return 1
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Bot Iwed dihentikan oleh pengguna (KeyboardInterrupt).")
        sys.exit(0)
