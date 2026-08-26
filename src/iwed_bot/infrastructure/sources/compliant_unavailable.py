"""Compliant TrackSource adapter yang menolak request saat compliant source belum tersedia."""

from iwed_bot.application.errors import CompliantSourceUnavailable
from iwed_bot.domain.models import TrackReference
from iwed_bot.ports.sources import TrackSource


class CompliantSourceUnavailableAdapter(TrackSource):
    """Adapter untuk mode SOURCE_POLICY_MODE='compliance-first'."""

    async def search(self, query: str, limit: int = 5) -> tuple[TrackReference, ...]:
        _ = (query, limit)
        raise CompliantSourceUnavailable()

    async def resolve_single_url(self, url: str) -> TrackReference:
        _ = url
        raise CompliantSourceUnavailable()
