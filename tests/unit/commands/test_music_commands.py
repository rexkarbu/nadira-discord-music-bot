"""Unit tests untuk MusicCog slash commands dan validasi voice boundary."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from iwed_bot.application.errors import (
    BotMissingVoicePermission,
    DifferentVoiceChannel,
    GuildOnlyCommand,
    UnsupportedVoiceChannel,
    UserNotInVoice,
    VoiceChannelFull,
)
from iwed_bot.commands.music import MusicCog, make_progress_bar
from iwed_bot.domain.models import (
    PlaybackState,
    QueueEntry,
    SourceType,
    TrackReference,
    VersionedGuildSession,
)
from iwed_bot.ports.sources import SourceClassification


def make_test_track(title: str = "Test Track", duration_ms: int | None = 180000) -> TrackReference:
    return TrackReference(
        id=uuid.uuid4(),
        source_type=SourceType.YOUTUBE,
        source_id="123",
        source_uri="https://www.youtube.com/watch?v=123",
        search_hint="artist - test",
        title=title,
        artists=("Artist",),
        duration_ms=duration_ms,
        thumbnail_url="https://img.youtube.com/vi/123/hqdefault.jpg",
        canonical_url="https://www.youtube.com/watch?v=123",
        is_stream=duration_ms is None,
    )


def make_test_entry(guild_id: int = 100, title: str = "Test Entry") -> QueueEntry:
    return QueueEntry(
        id=uuid.uuid4(),
        guild_id=guild_id,
        track=make_test_track(title=title),
        requested_by_user_id=12345,
        requested_in_channel_id=999,
        enqueued_at=datetime.now(UTC),
    )


class TestMusicCog:
    @pytest.fixture
    def setup_cog(self) -> tuple[MusicCog, MagicMock]:
        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 99999
        bot.play_service = MagicMock()
        bot.queue_control = MagicMock()

        cog = MusicCog(bot)
        return cog, bot

    def _make_interaction(
        self,
        guild_id: int | None = 100,
        in_voice: bool = True,
        voice_channel_id: int = 555,
        channel_type: type = discord.VoiceChannel,
        bot_voice_channel_id: int | None = None,
        view_channel: bool = True,
        connect: bool = True,
        speak: bool = True,
        user_limit: int = 0,
        member_count: int = 1,
    ) -> MagicMock:
        interaction = MagicMock(spec=discord.Interaction)
        interaction.channel_id = 999
        interaction.extras = {}

        if guild_id is not None:
            guild = MagicMock(spec=discord.Guild)
            guild.id = guild_id
            guild.name = "Test Server"

            # Setup bot member (guild.me)
            bot_member = MagicMock(spec=discord.Member)
            bot_member.id = 99999
            if bot_voice_channel_id is not None:
                bot_vs = MagicMock(spec=discord.VoiceState)
                bot_ch = MagicMock(spec=discord.VoiceChannel)
                bot_ch.id = bot_voice_channel_id
                bot_vs.channel = bot_ch
                bot_member.voice = bot_vs
            else:
                bot_member.voice = None
            guild.me = bot_member
            interaction.guild = guild
        else:
            interaction.guild = None

        if in_voice:
            member = MagicMock(spec=discord.Member)
            member.id = 12345
            voice_state = MagicMock(spec=discord.VoiceState)
            voice_ch = MagicMock(spec=channel_type)
            voice_ch.id = voice_channel_id
            voice_ch.name = "Test Voice Channel"
            voice_ch.user_limit = user_limit
            voice_ch.members = [MagicMock()] * member_count

            perms = MagicMock(spec=discord.Permissions)
            perms.view_channel = view_channel
            perms.connect = connect
            perms.speak = speak
            perms.move_members = False
            voice_ch.permissions_for.return_value = perms

            voice_state.channel = voice_ch
            member.voice = voice_state
            interaction.user = member
        else:
            member = MagicMock(spec=discord.Member)
            member.id = 12345
            member.voice = None
            interaction.user = member

        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False

        async def _fake_defer(*_args: object, **_kwargs: object) -> None:
            interaction.response.is_done.return_value = True

        interaction.response.defer = AsyncMock(side_effect=_fake_defer)
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_play_command_success(self, setup_cog: tuple[MusicCog, MagicMock]) -> None:
        cog, bot = setup_cog
        interaction = self._make_interaction()
        track = make_test_track(title="Song 1")
        entry = make_test_entry(title="Song 1")

        bot.play_service.resolve_input = AsyncMock(
            return_value=(SourceClassification.SEARCH_TEXT, track)
        )
        bot.play_service.enqueue_and_start = AsyncMock(return_value=("STARTED", entry, MagicMock()))

        await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        bot.play_service.resolve_input.assert_awaited_once_with("Song 1")
        interaction.edit_original_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_moves_channel_during_search_rejected(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, bot = setup_cog
        interaction = self._make_interaction(voice_channel_id=555)
        track = make_test_track()

        async def fake_search(_query: str) -> tuple[SourceClassification, TrackReference]:
            # Simulate user moving to channel 777 during search
            new_ch = MagicMock(spec=discord.VoiceChannel)
            new_ch.id = 777
            interaction.user.voice.channel = new_ch
            return SourceClassification.SEARCH_TEXT, track

        bot.play_service.resolve_input = AsyncMock(side_effect=fake_search)

        with pytest.raises(UserNotInVoice, match="berpindah voice channel"):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_user_leaves_channel_during_search_rejected(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, bot = setup_cog
        interaction = self._make_interaction(voice_channel_id=555)
        track = make_test_track()

        async def fake_search(_query: str) -> tuple[SourceClassification, TrackReference]:
            # Simulate user leaving voice during search
            interaction.user.voice = None
            return SourceClassification.SEARCH_TEXT, track

        bot.play_service.resolve_input = AsyncMock(side_effect=fake_search)

        with pytest.raises(UserNotInVoice):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_stage_channel_rejected(self, setup_cog: tuple[MusicCog, MagicMock]) -> None:
        cog, _bot = setup_cog
        interaction = self._make_interaction(channel_type=discord.StageChannel)

        with pytest.raises(UnsupportedVoiceChannel):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_missing_bot_permission_rejected(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, _bot = setup_cog
        interaction = self._make_interaction(speak=False)

        with pytest.raises(BotMissingVoicePermission, match="Speak"):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_channel_full_rejected(self, setup_cog: tuple[MusicCog, MagicMock]) -> None:
        cog, _bot = setup_cog
        interaction = self._make_interaction(user_limit=5, member_count=5)

        with pytest.raises(VoiceChannelFull):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_mutating_commands_from_different_channel_rejected(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, _bot = setup_cog
        # Bot in channel 777, user in channel 555
        interaction = self._make_interaction(voice_channel_id=555, bot_voice_channel_id=777)

        with pytest.raises(DifferentVoiceChannel):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

        with pytest.raises(DifferentVoiceChannel):
            await cog.skip.callback(cog, interaction, count=1)  # type: ignore[misc]

        with pytest.raises(DifferentVoiceChannel):
            await cog.pause.callback(cog, interaction)  # type: ignore[misc]

        with pytest.raises(DifferentVoiceChannel):
            await cog.resume.callback(cog, interaction)  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_mutating_commands_defer_ephemeral_and_thinking(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, bot = setup_cog
        interaction_skip = self._make_interaction(voice_channel_id=555, bot_voice_channel_id=555)
        bot.queue_control.skip = AsyncMock(return_value=(1, make_test_entry()))

        await cog.skip.callback(cog, interaction_skip, count=1)  # type: ignore[misc]
        interaction_skip.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

        interaction_pause = self._make_interaction(voice_channel_id=555, bot_voice_channel_id=555)
        bot.queue_control.pause = AsyncMock(return_value=MagicMock())
        await cog.pause.callback(cog, interaction_pause)  # type: ignore[misc]
        interaction_pause.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

        interaction_resume = self._make_interaction(voice_channel_id=555, bot_voice_channel_id=555)
        bot.queue_control.resume = AsyncMock(return_value=MagicMock())
        await cog.resume.callback(cog, interaction_resume)  # type: ignore[misc]
        interaction_resume.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    @pytest.mark.asyncio
    async def test_readonly_commands_defer_public(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, bot = setup_cog
        interaction_queue = self._make_interaction(in_voice=False)
        bot.queue_control.get_queue_page = AsyncMock(return_value=(None, (), 1, 1, 0, 0, 0))
        await cog.queue.callback(cog, interaction_queue, page=1)  # type: ignore[misc]
        interaction_queue.response.defer.assert_awaited_once_with(ephemeral=False)

        interaction_np = self._make_interaction(in_voice=False)
        sess = VersionedGuildSession(guild_id=100, state=PlaybackState.IDLE)
        bot.queue_control.get_now_playing = AsyncMock(return_value=(sess, None))
        await cog.nowplaying.callback(cog, interaction_np)  # type: ignore[misc]
        interaction_np.response.defer.assert_awaited_once_with(ephemeral=False)

    @pytest.mark.asyncio
    async def test_play_command_outside_guild_raises(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, _bot = setup_cog
        interaction = self._make_interaction(guild_id=None)

        with pytest.raises(GuildOnlyCommand):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_play_command_user_not_in_voice_raises(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, _bot = setup_cog
        interaction = self._make_interaction(in_voice=False)

        with pytest.raises(UserNotInVoice):
            await cog.play.callback(cog, interaction, query="Song 1")  # type: ignore[misc]

    def test_progress_bar_helper(self) -> None:
        bar = make_progress_bar(position_ms=60000, duration_ms=180000, length=10)
        assert "🔘" in bar
        assert "`01:00`" in bar
        assert "`03:00`" in bar

        live_bar = make_progress_bar(position_ms=None, duration_ms=None)
        assert live_bar == "🔴 LIVE"

    @pytest.mark.asyncio
    async def test_embed_boundaries_and_truncation_with_extreme_metadata(
        self, setup_cog: tuple[MusicCog, MagicMock]
    ) -> None:
        cog, bot = setup_cog
        interaction = self._make_interaction(voice_channel_id=555, bot_voice_channel_id=555)
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.is_done.return_value = True
        interaction.edit_original_response = AsyncMock()

        # Extreme 1000-char title & artist
        extreme_track = TrackReference(
            id=uuid.uuid4(),
            source_type=SourceType.YOUTUBE,
            source_id="12345678901",
            source_uri="https://www.youtube.com/watch?v=12345678901",
            search_hint="extreme",
            title="A" * 1000,
            artists=("B" * 500, "C" * 500),
            duration_ms=180000,
            thumbnail_url=None,
            canonical_url="https://www.youtube.com/watch?v=12345678901",
        )
        extreme_entry = QueueEntry(
            id=uuid.uuid4(),
            guild_id=100,
            track=extreme_track,
            requested_by_user_id=12345,
            requested_in_channel_id=999,
            enqueued_at=datetime.now(UTC),
        )

        bot.play_service.resolve_input = AsyncMock(
            return_value=(SourceClassification.SEARCH_TEXT, extreme_track)
        )
        bot.play_service.enqueue_and_start = AsyncMock(
            return_value=("STARTED", extreme_entry, None)
        )

        await cog.play.callback(cog, interaction, query="extreme query")  # type: ignore[misc]

        call_kwargs = interaction.edit_original_response.call_args[1]
        embed: discord.Embed = call_kwargs["embed"]

        assert embed.title is not None
        assert len(embed.title) <= 256
        assert embed.description is not None
        assert len(embed.description) <= 4096
        for field in embed.fields:
            assert field.name is not None
            assert len(field.name) <= 256
            assert field.value is not None
            assert len(field.value) <= 1024
        if embed.footer and embed.footer.text:
            assert len(embed.footer.text) <= 2048
        assert len(embed) <= 6000
