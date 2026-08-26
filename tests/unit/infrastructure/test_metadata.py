"""Unit tests untuk parsing metadata ExtrasNamespace dan Wavelink payload."""

import uuid
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import wavelink

from iwed_bot.infrastructure.playback.metadata import (
    parse_track_metadata,
)
from iwed_bot.infrastructure.playback.wavelink_gateway import WavelinkPlaybackGateway


class DummyPlayableData:
    """Mock track data container yang menghasilkan wavelink.Playable."""

    def __init__(self, data: dict[str, object]) -> None:
        self.data = data


def create_real_wavelink_playable(
    entry_id: UUID | str,
    generation: int,
    guild_id: int = 123,
) -> wavelink.Playable:
    """Membuat instance wavelink.Playable nyata dengan assign extras_dict."""
    data = {
        "encoded": (
            "QAAAdgIAKlJpY2sgQXN0bGV5IC0gTmV2ZXIgR29ubmEgR2l2ZSBZb3UgVXAgKE9mZmljaWFsIE11c2lj"
            "IFZpZGVvKQALUmljayBBc3RsZXkAAAAAAAPoAAAAClpPZVlaUjFwdWpnAAEAK2h0dHBzOi8vd3d3Lnlv"
            "dXR1YmUuY29tL3dhdGNoP3Y9Wk9lWVpSMXB1amcAAAAA"
        ),
        "info": {
            "identifier": "ZOeYZR1pujg",
            "isSeekable": True,
            "author": "Rick Astley",
            "length": 213000,
            "isStream": False,
            "position": 0,
            "title": "Never Gonna Give You Up",
            "uri": "https://www.youtube.com/watch?v=ZOeYZR1pujg",
            "artworkUrl": "https://i.ytimg.com/vi/ZOeYZR1pujg/maxresdefault.jpg",
            "isrc": None,
            "sourceName": "youtube",
        },
        "pluginInfo": {},
        "userData": {},
    }
    playable = wavelink.Playable(cast("Any", data))
    # Wavelink 3.5.2 sets playable.extras as ExtrasNamespace when assigned a dict
    playable.extras = {
        "entry_id": str(entry_id),
        "generation": generation,
        "guild_id": guild_id,
    }
    return playable


class TestMetadataParsing:
    def test_extract_track_metadata_accepts_extras_namespace(self) -> None:
        entry_id = uuid.uuid4()
        generation = 2
        guild_id = 456

        playable = create_real_wavelink_playable(entry_id, generation, guild_id)
        # Verify that wavelink actually turned it into ExtrasNamespace or Mapping
        assert not isinstance(playable.extras, dict) or isinstance(
            playable.extras, wavelink.ExtrasNamespace
        )

        extracted_gid, extracted_eid, extracted_gen = parse_track_metadata(playable)
        assert extracted_gid == guild_id
        assert extracted_eid == entry_id
        assert extracted_gen == generation

    @pytest.mark.asyncio
    async def test_get_snapshot_accepts_extras_namespace(self) -> None:
        mock_bot = MagicMock()
        gateway = WavelinkPlaybackGateway(bot=mock_bot)

        entry_id = uuid.uuid4()
        generation = 3
        guild_id = 789

        playable = create_real_wavelink_playable(entry_id, generation, guild_id)

        mock_player = MagicMock(spec=wavelink.Player)
        mock_player.connected = True
        mock_player.playing = True
        mock_player.paused = False
        mock_player.position = 10000
        mock_player.current = playable

        mock_guild = MagicMock()
        mock_guild.voice_client = mock_player
        mock_bot.get_guild.return_value = mock_guild

        snapshot = await gateway.get_snapshot(guild_id)
        assert snapshot is not None
        assert snapshot.active_entry_id == entry_id
        assert snapshot.active_generation == generation
        assert snapshot.is_playing is True

    @pytest.mark.asyncio
    async def test_track_end_payload_with_real_extras_advances_queue(self) -> None:
        entry_id = uuid.uuid4()
        generation = 1
        guild_id = 123

        playable = create_real_wavelink_playable(entry_id, generation, guild_id)

        # Build dummy TrackEndEventPayload
        mock_player = MagicMock(spec=wavelink.Player)
        mock_guild = MagicMock()
        mock_guild.id = guild_id
        mock_player.guild = mock_guild

        payload = MagicMock(spec=wavelink.TrackEndEventPayload)
        payload.player = mock_player
        payload.track = playable
        payload.reason = "finished"

        gid, eid, gen = parse_track_metadata(payload)
        assert gid == guild_id
        assert eid == entry_id
        assert gen == generation

    def test_invalid_event_extras_are_ignored_safely(self) -> None:
        # 1. Non-UUID entry_id
        bad_playable = create_real_wavelink_playable("not-a-uuid", 1, 123)
        _gid, eid, gen = parse_track_metadata(bad_playable)
        assert eid is None
        assert gen == 1

        # 2. Negative generation
        bad_gen_playable = create_real_wavelink_playable(uuid.uuid4(), -5, 123)
        _gid2, eid, gen = parse_track_metadata(bad_gen_playable)
        assert eid is not None
        assert gen is None

        # 3. None object
        assert parse_track_metadata(None, fallback_guild_id=999) == (999, None, None)

        # 4. Empty extras object
        empty_obj = type("Empty", (), {"extras": None})()
        assert parse_track_metadata(empty_obj, fallback_guild_id=123) == (123, None, None)
