"""Music command cog untuk interaksi playback musik dan antrean."""

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from iwed_bot.application.errors import (
    DifferentVoiceChannel,
    NothingPlaying,
    UnsupportedVoiceChannel,
    UserNotInVoice,
)
from iwed_bot.commands.checks import (
    check_bot_voice_permissions,
    ensure_guild_context,
    ensure_user_in_voice,
)
from iwed_bot.domain.models import PlaybackState
from iwed_bot.presentation.interactions import (
    escape_markdown,
    format_duration,
    respond_or_edit,
    safe_truncate,
)

if TYPE_CHECKING:
    from iwed_bot.bot import IwedBot

logger = logging.getLogger(__name__)


def make_progress_bar(position_ms: int | None, duration_ms: int | None, length: int = 12) -> str:
    """Membuat text progress bar interaktif."""
    if duration_ms is None or duration_ms <= 0:
        return "🔴 LIVE"

    pos = max(0, position_ms or 0)
    pos_clamped = min(pos, duration_ms)
    progress_ratio = pos_clamped / duration_ms
    dot_pos = int(progress_ratio * (length - 1))

    bar = ["━"] * length
    bar[dot_pos] = "🔘"
    bar_str = "".join(bar)

    curr_str = format_duration(pos_clamped)
    total_str = format_duration(duration_ms)
    return f"`{curr_str}` {bar_str} `{total_str}`"


class MusicCog(commands.Cog):
    """Cog perintah musik: /play, /skip, /pause, /resume, /queue, dan /nowplaying."""

    def __init__(self, bot: "IwedBot") -> None:
        self.bot = bot

    @app_commands.command(name="play", description="Putar lagu dari judul atau URL YouTube.")
    @app_commands.describe(query="Judul lagu atau URL video tunggal YouTube")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        guild = ensure_guild_context(interaction)
        user_channel = ensure_user_in_voice(interaction)

        # Mutating command wajib defer ephemeral + thinking
        await interaction.response.defer(ephemeral=True, thinking=True)
        text_channel_id = interaction.channel_id

        # 1. Validasi bot voice connection / permissions sebelum pencarian
        if guild.me.voice is None or guild.me.voice.channel is None:
            check_bot_voice_permissions(user_channel, guild.me)
        elif guild.me.voice.channel.id != user_channel.id:
            raise DifferentVoiceChannel(
                "Bot berada di voice channel yang berbeda. Gunakan /join untuk memindahkan bot."
            )

        # 2. Resolve input di luar lock
        _kind, track = await self.bot.play_service.resolve_input(query)

        # 3. Baca ulang (re-read) voice state member setelah proses search selesai
        if not isinstance(interaction.user, discord.Member):
            raise UserNotInVoice()
        current_voice = interaction.user.voice
        if (
            current_voice is None
            or current_voice.channel is None
            or current_voice.channel.id != user_channel.id
        ):
            raise UserNotInVoice(
                "Anda telah meninggalkan atau berpindah voice channel sebelum pencarian selesai."
            )

        if isinstance(current_voice.channel, discord.StageChannel) or not isinstance(
            current_voice.channel, discord.VoiceChannel
        ):
            raise UnsupportedVoiceChannel(
                "Iwed saat ini hanya mendukung voice channel biasa (bukan Stage Channel)."
            )

        # 4. Enqueue and start
        status, entry, _snapshot = await self.bot.play_service.enqueue_and_start(
            guild_id=guild.id,
            track=track,
            user_id=interaction.user.id,
            channel_id=user_channel.id,
            text_channel_id=text_channel_id,
        )

        # 5. Format Embed
        embed = discord.Embed(
            color=discord.Color.brand_green() if status == "STARTED" else discord.Color.blue()
        )
        if status == "STARTED":
            embed.title = "▶️ Sedang Memutar"
        else:
            embed.title = "📋 Ditambahkan ke Antrean"

        title_text = safe_truncate(escape_markdown(track.title), 200)
        if track.canonical_url:
            embed.description = f"**[{title_text}]({track.canonical_url})**"
        else:
            embed.description = f"**{title_text}**"

        raw_artists = ", ".join(track.artists) if track.artists else "Tidak diketahui"
        artists_text = safe_truncate(escape_markdown(raw_artists), 250)
        embed.add_field(name="Artis", value=artists_text, inline=True)
        embed.add_field(
            name="Durasi",
            value=format_duration(track.duration_ms, track.is_stream),
            inline=True,
        )
        embed.add_field(name="Diminta oleh", value=f"<@{interaction.user.id}>", inline=True)

        if track.thumbnail_url:
            embed.set_thumbnail(url=track.thumbnail_url)

        embed.set_footer(text=f"Iwed Music • ID: {str(entry.id)[:8]}")
        await respond_or_edit(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="skip", description="Lewati lagu yang sedang diputar.")
    @app_commands.describe(count="Jumlah lagu yang ingin dilewati (1-25)")
    async def skip(
        self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25] = 1
    ) -> None:
        guild = ensure_guild_context(interaction)
        user_channel = ensure_user_in_voice(interaction)

        await interaction.response.defer(ephemeral=True, thinking=True)

        if guild.me.voice is None or guild.me.voice.channel is None:
            raise NothingPlaying("Bot tidak terhubung ke voice channel.")
        if guild.me.voice.channel.id != user_channel.id:
            raise DifferentVoiceChannel(
                "Anda harus berada di voice channel yang sama dengan bot untuk melewati lagu."
            )

        skipped_count, next_entry = await self.bot.queue_control.skip(
            guild.id, count=count, requester_channel_id=user_channel.id
        )

        embed = discord.Embed(color=discord.Color.gold())
        if next_entry is not None:
            embed.title = "⏭️ Lagu Dilewati"
            next_title = next_entry.track.title
            link = next_entry.track.canonical_url
            target_text = f"**[{next_title}]({link})**" if link else f"**{next_title}**"
            embed.description = (
                f"Melewati **{skipped_count} lagu**.\n\n"
                f"Lagu berikutnya sedang disiapkan: {target_text}"
            )
            if next_entry.track.thumbnail_url:
                embed.set_thumbnail(url=next_entry.track.thumbnail_url)
        else:
            embed.title = "⏹️ Antrean Selesai"
            embed.description = (
                f"Melewati **{skipped_count} lagu**. Tidak ada lagi lagu dalam antrean."
            )

        await respond_or_edit(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="pause", description="Jeda pemutaran musik saat ini.")
    async def pause(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild_context(interaction)
        user_channel = ensure_user_in_voice(interaction)

        await interaction.response.defer(ephemeral=True, thinking=True)

        if guild.me.voice is None or guild.me.voice.channel is None:
            raise NothingPlaying("Bot tidak terhubung ke voice channel.")
        if guild.me.voice.channel.id != user_channel.id:
            raise DifferentVoiceChannel(
                "Anda harus berada di voice channel yang sama dengan bot untuk menjeda musik."
            )

        await self.bot.queue_control.pause(guild.id, requester_channel_id=user_channel.id)
        embed = discord.Embed(
            title="⏸️ Pemutaran Musik Dijeda",
            description="Gunakan `/resume` untuk melanjutkan pemutaran.",
            color=discord.Color.orange(),
        )
        await respond_or_edit(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="resume", description="Lanjutkan pemutaran musik yang dijeda.")
    async def resume(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild_context(interaction)
        user_channel = ensure_user_in_voice(interaction)

        await interaction.response.defer(ephemeral=True, thinking=True)

        if guild.me.voice is None or guild.me.voice.channel is None:
            raise NothingPlaying("Bot tidak terhubung ke voice channel.")
        if guild.me.voice.channel.id != user_channel.id:
            raise DifferentVoiceChannel(
                "Anda harus berada di voice channel yang sama dengan bot untuk melanjutkan musik."
            )

        await self.bot.queue_control.resume(guild.id, requester_channel_id=user_channel.id)
        embed = discord.Embed(
            title="▶️ Pemutaran Musik Dilanjutkan",
            description="Musik kembali diputar.",
            color=discord.Color.brand_green(),
        )
        await respond_or_edit(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="queue", description="Lihat daftar antrean lagu server.")
    @app_commands.describe(page="Nomor halaman antrean (1, 2, ...)")
    async def queue(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1
    ) -> None:
        guild = ensure_guild_context(interaction)
        # Read-only command tetap public
        await interaction.response.defer(ephemeral=False)

        (
            current_entry,
            page_items,
            curr_page,
            total_pages,
            total_tracks,
            total_duration_ms,
            stream_count,
        ) = await self.bot.queue_control.get_queue_page(guild.id, page=page, per_page=10)

        embed = discord.Embed(
            title=safe_truncate(f"📋 Antrean Musik — {escape_markdown(guild.name)}", 250),
            color=discord.Color.blurple(),
        )

        # Header: Current track
        if current_entry is not None:
            c_track = current_entry.track
            c_dur = format_duration(c_track.duration_ms, c_track.is_stream)
            c_link = c_track.canonical_url
            c_title = safe_truncate(escape_markdown(c_track.title), 100)
            c_val = f"[{c_title}]({c_link})" if c_link else c_title
            embed.add_field(
                name="▶️ Sedang Diputar",
                value=f"**{c_val}** ({c_dur}) | <@{current_entry.requested_by_user_id}>",
                inline=False,
            )
        else:
            embed.add_field(
                name="▶️ Sedang Diputar",
                value="*Tidak ada lagu yang sedang diputar.*",
                inline=False,
            )

        # Body: Upcoming page items
        if page_items:
            lines = []
            start_idx = (curr_page - 1) * 10
            for i, item in enumerate(page_items):
                num = start_idx + i + 1
                i_track = item.track
                i_dur = format_duration(i_track.duration_ms, i_track.is_stream)
                i_link = i_track.canonical_url
                i_title = safe_truncate(escape_markdown(i_track.title), 60)
                i_val = f"[{i_title}]({i_link})" if i_link else i_title
                lines.append(f"`{num}.` **{i_val}** ({i_dur}) | <@{item.requested_by_user_id}>")

            body_value = safe_truncate("\n".join(lines), 1020)
            embed.add_field(
                name=f"Antrean Berikutnya (Halaman {curr_page}/{total_pages})",
                value=body_value,
                inline=False,
            )
        else:
            embed.add_field(
                name="Antrean Berikutnya",
                value="*Tidak ada lagu dalam antrean.*",
                inline=False,
            )

        # Footer
        dur_str = format_duration(total_duration_ms)
        if stream_count > 0:
            dur_str += f" + {stream_count} Live"
        embed.set_footer(
            text=safe_truncate(
                f"Halaman {curr_page}/{total_pages} • Total {total_tracks} lagu ({dur_str})",
                2000,
            )
        )
        await respond_or_edit(interaction, embed=embed, ephemeral=False)

    @app_commands.command(
        name="nowplaying", description="Lihat informasi lagu yang sedang diputar."
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild_context(interaction)
        # Read-only command tetap public
        await interaction.response.defer(ephemeral=False)

        session, snapshot = await self.bot.queue_control.get_now_playing(guild.id)
        if session.current_entry is None or session.state == PlaybackState.IDLE:
            embed = discord.Embed(
                title="🎶 Now Playing",
                description="Tidak ada lagu yang sedang diputar saat ini.",
                color=discord.Color.light_grey(),
            )
            await respond_or_edit(interaction, embed=embed, ephemeral=False)
            return

        track = session.current_entry.track
        position_ms = snapshot.position_ms if snapshot else 0
        progress_str = make_progress_bar(position_ms, track.duration_ms)

        state_str = "⏸️ Dijeda" if session.state == PlaybackState.PAUSED else "▶️ Memutar"
        embed = discord.Embed(
            title=f"🎶 {state_str}",
            color=discord.Color.purple(),
        )

        title_text = safe_truncate(escape_markdown(track.title), 200)
        if track.canonical_url:
            embed.description = f"**[{title_text}]({track.canonical_url})**\n\n{progress_str}"
        else:
            embed.description = f"**{title_text}**\n\n{progress_str}"

        raw_artists = ", ".join(track.artists) if track.artists else "Tidak diketahui"
        artists_text = safe_truncate(escape_markdown(raw_artists), 250)
        embed.add_field(name="Artis", value=artists_text, inline=True)
        embed.add_field(name="Volume", value=f"{session.volume}%", inline=True)
        embed.add_field(name="Loop", value=session.loop_mode.value.capitalize(), inline=True)
        embed.add_field(
            name="Diminta oleh",
            value=f"<@{session.current_entry.requested_by_user_id}>",
            inline=True,
        )

        if track.thumbnail_url:
            embed.set_thumbnail(url=track.thumbnail_url)

        embed.set_footer(text=f"Iwed Music • Generasi Sesi: {session.generation}")
        await respond_or_edit(interaction, embed=embed, ephemeral=False)


async def setup(bot: "IwedBot") -> None:
    """Entry point untuk registrasi MusicCog ke bot."""
    await bot.add_cog(MusicCog(bot))
