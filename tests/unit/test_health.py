"""Unit tests untuk slash command /health dan embed status kesehatan Iwed."""

from typing import Any

import discord
import pytest

from iwed_bot.commands.health import HealthCog, build_health_embed


def test_build_health_embed_when_lavalink_connected(sample_health_data: dict[str, Any]) -> None:
    """Memverifikasi tampilan embed ketika bot dan node Lavalink dalam kondisi sehat (online)."""
    embed = build_health_embed(sample_health_data)

    assert isinstance(embed, discord.Embed)
    assert embed.title == "🏥 Status Kesehatan Sistem — Iwed"
    assert embed.color == discord.Color.from_rgb(46, 204, 113)  # Hijau

    fields_dict: dict[str, str] = {
        str(field.name): str(field.value) for field in embed.fields if field.name
    }

    assert "🤖 Status Bot" in fields_dict
    assert "🟢 `Online`" in fields_dict["🤖 Status Bot"]

    assert "🎵 Node Audio (Lavalink)" in fields_dict
    assert "🟢 `Terhubung`" in fields_dict["🎵 Node Audio (Lavalink)"]

    assert "📡 Latensi Gateway" in fields_dict
    assert "42.5 ms" in fields_dict["📡 Latensi Gateway"]

    assert "⏱️ Uptime" in fields_dict
    assert "1j 1m 5d" in fields_dict["⏱️ Uptime"]

    assert "⚙️ Mode Startup" in fields_dict
    assert "`Normal`" in fields_dict["⚙️ Mode Startup"]

    assert "🏷️ Versi Aplikasi" in fields_dict
    assert "DAVE-compatible stack" in fields_dict["🏷️ Versi Aplikasi"]

    # Pastikan Queue Backend tidak diklaim sebagai komponen aktif
    assert "📦 Queue Backend" not in fields_dict


def test_build_health_embed_when_lavalink_degraded(degraded_health_data: dict[str, Any]) -> None:
    """Memverifikasi tampilan embed ketika node Lavalink offline (degraded state)."""
    embed = build_health_embed(degraded_health_data)

    assert embed.color == discord.Color.from_rgb(231, 76, 60)  # Merah / degraded

    fields_dict: dict[str, str] = {
        str(field.name): str(field.value) for field in embed.fields if field.name
    }

    assert "🟡 `Degraded (Audio Offline)`" in fields_dict["🤖 Status Bot"]
    assert "🔴 `Terputus / Tidak Tersedia`" in fields_dict["🎵 Node Audio (Lavalink)"]
    assert "`Sinkronisasi...`" in fields_dict["📡 Latensi Gateway"]
    assert "`Degraded (Audio Offline)`" in fields_dict["⚙️ Mode Startup"]


def test_health_embed_does_not_leak_secrets(sample_health_data: dict[str, Any]) -> None:
    """Memverifikasi bahwa tidak ada token atau password yang bocor ke dalam atribut embed."""
    raw_secret_token = "secret_token_12345"
    raw_secret_password = "secret_password_xyz"

    # Tambahkan field rahasia ke data status jika ada kelalaian
    sample_health_data["token"] = raw_secret_token
    sample_health_data["password"] = raw_secret_password

    embed = build_health_embed(sample_health_data)

    embed_str = str(embed.to_dict())
    assert raw_secret_token not in embed_str
    assert raw_secret_password not in embed_str


@pytest.mark.asyncio
async def test_health_cog_initialization(valid_settings: Any) -> None:
    """Memverifikasi inisialisasi HealthCog untuk Iwed."""
    from iwed_bot.bot import IwedBot

    bot = IwedBot(valid_settings)
    try:
        cog = HealthCog(bot)
        assert cog.bot is bot
    finally:
        await bot.close()
