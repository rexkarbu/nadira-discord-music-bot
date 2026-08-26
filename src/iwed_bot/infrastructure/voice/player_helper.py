"""Helper internal untuk validasi dan pengambilan wavelink.Player per guild."""

from typing import TYPE_CHECKING, Any

import wavelink

from iwed_bot.application.errors import UnexpectedVoiceClient

if TYPE_CHECKING:
    import discord

    from iwed_bot.bot import IwedBot


def get_wavelink_player(
    bot: "IwedBot | discord.Client | Any", guild_id: int
) -> wavelink.Player | None:
    """Mengambil instance wavelink.Player dari guild jika terhubung.

    Raises:
        UnexpectedVoiceClient: Jika voice_client terdaftar bukan instance wavelink.Player.
    """
    guild = bot.get_guild(guild_id)
    if guild is None:
        return None

    voice_client = guild.voice_client
    if voice_client is None:
        return None

    if not isinstance(voice_client, wavelink.Player):
        raise UnexpectedVoiceClient(
            f"Voice client pada guild {guild_id} bertipe {type(voice_client)}, "
            "bukan wavelink.Player."
        )

    return voice_client
