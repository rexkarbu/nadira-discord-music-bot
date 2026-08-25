"""Konfigurasi dan validasi environment variables untuk Nadira Discord Music Bot.

Modul ini bertanggung jawab memuat seluruh konfigurasi aplikasi dari environment
atau file .env menggunakan Pydantic Settings dengan penegakan tipe data ketat,
pembatasan nilai numerik, serta perlindungan kredensial sensitif.
"""

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Skema konfigurasi tersertifikasi untuk runtime bot Nadira."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Kredensial Discord ---
    DISCORD_TOKEN: SecretStr = Field(
        default=SecretStr(""),
        description="Token otentikasi Bot Discord. Terlindungi dari kebocoran log.",
    )
    DISCORD_APPLICATION_ID: int = Field(
        default=0,
        description="ID Aplikasi / Client ID Discord Bot.",
    )
    DISCORD_TEST_GUILD_ID: int | None = Field(
        default=None,
        description="ID Guild Discord opsional untuk instant slash command sync saat development.",
    )

    # --- Kredensial & Endpoint Lavalink v4 ---
    LAVALINK_URI: str = Field(
        default="http://localhost:2333",
        description="URI endpoint REST/HTTP Lavalink v4 (hanya mendukung http:// dan https://).",
    )
    LAVALINK_PASSWORD: SecretStr = Field(
        default=SecretStr("youshallnotpass"),
        description="Password otentikasi node Lavalink. Terlindungi dari kebocoran log.",
    )

    # --- Observabilitas ---
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Tingkat keparahan logging sistem.",
    )

    # --- Reserved Configurations (Fase 2+) ---
    SOURCE_POLICY_MODE: Literal["prototype", "compliance-first"] = Field(
        default="prototype",
        description="Reserved: Mode kebijakan sumber media.",
    )
    QUEUE_BACKEND: Literal["memory", "redis"] = Field(
        default="memory",
        description="Reserved: Backend penyimpanan antrean lagu.",
    )
    REDIS_URL: str | None = Field(
        default=None,
        description="Reserved: URL koneksi Redis jika QUEUE_BACKEND='redis'.",
    )
    MAX_PLAYLIST_TRACKS: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Reserved: Batas maksimum lagu yang dapat diimpor dari satu playlist.",
    )
    QUEUE_MAX_TRACKS: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Reserved: Kapasitas maksimum total antrean per server/guild.",
    )
    IDLE_DISCONNECT_SECONDS: int = Field(
        default=300,
        ge=10,
        le=86400,
        description="Reserved: Durasi timeout ketidakaktifan (detik) sebelum bot keluar voice.",
    )
    DEFAULT_VOLUME: int = Field(
        default=70,
        ge=0,
        le=100,
        description="Reserved: Volume awal pemutaran musik (0 - 100).",
    )

    def __init__(self, _env_file: str | None = "", **kwargs: Any) -> None:
        # Jika _env_file="", gunakan bawaan config; jika None, nonaktifkan env_file
        if _env_file == "":
            super().__init__(**kwargs)
        else:
            super().__init__(_env_file=_env_file, **kwargs)

    @field_validator("DISCORD_TOKEN")
    @classmethod
    def validate_discord_token(cls, v: SecretStr) -> SecretStr:
        """Memastikan token Discord tidak kosong."""
        if not v.get_secret_value().strip():
            raise ValueError("DISCORD_TOKEN wajib diisi dan tidak boleh kosong.")
        return v

    @field_validator("DISCORD_APPLICATION_ID")
    @classmethod
    def validate_application_id(cls, v: int) -> int:
        """Memastikan Application ID diisi dengan integer positif > 0."""
        if v <= 0:
            raise ValueError("DISCORD_APPLICATION_ID wajib diisi dengan ID valid (> 0).")
        return v

    @field_validator("DISCORD_TEST_GUILD_ID")
    @classmethod
    def validate_test_guild_id(cls, v: int | None) -> int | None:
        """Memastikan Test Guild ID jika disediakan bernilai > 0."""
        if v is not None and v <= 0:
            raise ValueError("DISCORD_TEST_GUILD_ID harus bernilai positif (> 0) atau None.")
        return v

    @field_validator("LAVALINK_URI")
    @classmethod
    def validate_lavalink_uri(cls, v: str) -> str:
        """Memastikan URI Lavalink hanya menggunakan skema http/https dan memiliki host."""
        stripped = v.strip().rstrip("/")
        parsed = urlparse(stripped)

        if parsed.scheme not in ("http", "https"):
            scheme_display = parsed.scheme or stripped
            msg = (
                f"LAVALINK_URI hanya menerima skema 'http' atau 'https'. "
                f"Diberikan: '{scheme_display}'"
            )
            raise ValueError(msg)

        if not parsed.netloc and not parsed.hostname:
            msg = f"LAVALINK_URI harus menyertakan host yang valid. Diberikan: '{stripped}'"
            raise ValueError(msg)

        return stripped

    @model_validator(mode="after")
    def validate_redis_dependency(self) -> "Settings":
        """Menegakkan invariant bahwa REDIS_URL wajib diisi jika QUEUE_BACKEND bernilai 'redis'."""
        if self.QUEUE_BACKEND == "redis":
            if not self.REDIS_URL or not self.REDIS_URL.strip():
                raise ValueError("REDIS_URL wajib diisi jika QUEUE_BACKEND='redis'.")
            if not (
                self.REDIS_URL.startswith("redis://") or self.REDIS_URL.startswith("rediss://")
            ):
                msg = f"REDIS_URL harus diawali redis:// / rediss://. Diberikan: '{self.REDIS_URL}'"
                raise ValueError(msg)
        return self

    def safe_summary(self) -> dict[str, Any]:
        """Mengembalikan representasi dictionary konfigurasi yang aman tanpa kredensial rahasia."""
        return {
            "DISCORD_APPLICATION_ID": self.DISCORD_APPLICATION_ID,
            "DISCORD_TEST_GUILD_ID": self.DISCORD_TEST_GUILD_ID,
            "LAVALINK_URI": self.LAVALINK_URI,
            "LOG_LEVEL": self.LOG_LEVEL,
            "SOURCE_POLICY_MODE": self.SOURCE_POLICY_MODE,
            "QUEUE_BACKEND": self.QUEUE_BACKEND,
            "MAX_PLAYLIST_TRACKS": self.MAX_PLAYLIST_TRACKS,
            "QUEUE_MAX_TRACKS": self.QUEUE_MAX_TRACKS,
            "IDLE_DISCONNECT_SECONDS": self.IDLE_DISCONNECT_SECONDS,
            "DEFAULT_VOLUME": self.DEFAULT_VOLUME,
            "DISCORD_TOKEN": "**********",
            "LAVALINK_PASSWORD": "**********",
            "REDIS_URL": self.REDIS_URL if self.REDIS_URL else None,
        }

    def __repr__(self) -> str:
        """Representasi string aman yang menutupi secret token dan password."""
        return (
            f"Settings(APPLICATION_ID={self.DISCORD_APPLICATION_ID}, "
            f"TEST_GUILD_ID={self.DISCORD_TEST_GUILD_ID}, "
            f"LAVALINK_URI='{self.LAVALINK_URI}', "
            f"LOG_LEVEL='{self.LOG_LEVEL}', "
            f"TOKEN=***, LAVALINK_PASSWORD=***)"
        )
