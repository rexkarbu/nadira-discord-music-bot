"""Slash command /health untuk memantau status operasional Iwed dan node Lavalink."""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from iwed_bot.bot import IwedBot

logger = logging.getLogger(__name__)


def build_health_embed(health_data: dict[str, Any]) -> discord.Embed:
    """Membangun Discord Embed yang rapi dan aman dari data status kesehatan."""
    is_lavalink_connected = health_data.get("lavalink_connected", False)
    is_degraded = not is_lavalink_connected

    # Pilih warna embed berdasarkan status kesehatan: Hijau jika sehat, Merah jika degraded
    embed_color = (
        discord.Color.from_rgb(231, 76, 60) if is_degraded else discord.Color.from_rgb(46, 204, 113)
    )

    embed = discord.Embed(
        title="🏥 Status Kesehatan Sistem — Iwed",
        description="Ringkasan operasional bot, latensi gateway, dan status node audio.",
        color=embed_color,
        timestamp=datetime.now(UTC),
    )

    # Status Bot & Uptime
    bot_status_str = (
        f"🟢 `{health_data.get('bot_status', 'Online')}`"
        if not is_degraded
        else "🟡 `Degraded (Audio Offline)`"
    )
    embed.add_field(
        name="🤖 Status Bot",
        value=bot_status_str,
        inline=True,
    )
    embed.add_field(
        name="⏱️ Uptime",
        value=f"`{health_data.get('uptime_str', '0d')}`",
        inline=True,
    )

    # Latensi Discord Gateway
    latency_ms = health_data.get("discord_latency_ms")
    latency_display = (
        f"`{latency_ms:.1f} ms`" if isinstance(latency_ms, (int, float)) else "`Sinkronisasi...`"
    )
    embed.add_field(
        name="📡 Latensi Gateway",
        value=latency_display,
        inline=True,
    )

    # Status Node Lavalink
    lavalink_status = (
        "🟢 `Terhubung`" if is_lavalink_connected else "🔴 `Terputus / Tidak Tersedia`"
    )
    embed.add_field(
        name="🎵 Node Audio (Lavalink)",
        value=lavalink_status,
        inline=True,
    )

    # Mode Startup & Versi Aplikasi
    default_startup_mode = "Normal" if is_lavalink_connected else "Degraded (Audio Offline)"
    startup_mode_val = health_data.get("startup_mode", default_startup_mode)
    embed.add_field(
        name="⚙️ Mode Startup",
        value=f"`{startup_mode_val}`",
        inline=True,
    )
    app_ver = health_data.get("app_version", "0.1.0 • DAVE-compatible stack")
    embed.add_field(
        name="🏷️ Versi Aplikasi",
        value=f"`{app_ver}`",
        inline=True,
    )

    embed.set_footer(text="Iwed Discord Music Bot • DAVE-compatible stack")
    return embed


class HealthCog(commands.Cog):
    """Cog yang memuat perintah pemantauan kesehatan bot Iwed."""

    def __init__(self, bot: "IwedBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="health",
        description="Periksa status kesehatan Iwed, latensi gateway, dan status node audio.",
    )
    async def health(self, interaction: discord.Interaction) -> None:
        """Menangani slash command /health dan merespons dengan embed status sistem."""
        health_data = self.bot.get_health_status()
        embed = build_health_embed(health_data)
        await interaction.response.send_message(embed=embed)
        logger.info(
            "Menjalankan slash command /health",
            extra={
                "guild_id": interaction.guild_id,
                "user_id": interaction.user.id,
                "lavalink_connected": health_data.get("lavalink_connected", False),
            },
        )


async def setup(bot: "IwedBot") -> None:
    """Fungsi entrypoint untuk memuat HealthCog ke dalam bot."""
    await bot.add_cog(HealthCog(bot))
