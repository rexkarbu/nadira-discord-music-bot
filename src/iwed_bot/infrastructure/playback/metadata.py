"""Helper untuk parsing dan validasi metadata track dari Wavelink."""

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def extract_extras_dict(extras_obj: Any) -> dict[str, Any]:
    """Mengekstrak dictionary aman dari objek extras (dict, ExtrasNamespace, dsb)."""
    if extras_obj is None:
        return {}

    if isinstance(extras_obj, dict):
        return extras_obj

    if isinstance(extras_obj, Mapping):
        return dict(extras_obj)

    # Coba konversi via dict(extras_obj) jika mendukung iterasi key-value
    try:
        return dict(extras_obj)
    except Exception:
        pass

    # Coba akses vars() atau __dict__
    try:
        return dict(vars(extras_obj))
    except Exception:
        pass

    # Coba akses atribut umum langsung
    result: dict[str, Any] = {}
    for attr in ("entry_id", "generation", "guild_id"):
        if hasattr(extras_obj, attr):
            result[attr] = getattr(extras_obj, attr)
    return result


def parse_track_metadata(
    track_or_payload: Any,
    fallback_guild_id: int | None = None,
) -> tuple[int | None, UUID | None, int | None]:
    """Mengekstrak dan memvalidasi metadata (guild_id, entry_id, generation) dari payload.

    Returns:
        tuple (guild_id, entry_id, generation) dengan tipe yang sudah divalidasi.
        Jika metadata tidak valid atau tidak lengkap, field bernilai None.
    """
    if track_or_payload is None:
        return fallback_guild_id, None, None

    # 1. Ekstrak guild_id dari player jika ada pada payload
    guild_id: int | None = fallback_guild_id
    player = getattr(track_or_payload, "player", None)
    if player is not None:
        guild = getattr(player, "guild", None)
        if guild is not None:
            raw_gid = getattr(guild, "id", None)
            if raw_gid is not None:
                try:
                    parsed_gid = int(raw_gid)
                    if parsed_gid > 0:
                        guild_id = parsed_gid
                except Exception:
                    pass

    # 2. Ambil objek track kandidat (prioritaskan payload.track, payload.original, atau objek)
    track_candidate = getattr(track_or_payload, "track", None)
    if track_candidate is None:
        track_candidate = getattr(track_or_payload, "original", None)
    if track_candidate is None:
        # Mungkin track_or_payload langsung merupakan instance Playable
        track_candidate = track_or_payload

    # Coba ambil extras dari track_candidate
    extras_raw = getattr(track_candidate, "extras", None)

    # Jika extras_raw kosong dan payload punya .original, coba fallback ke payload.original.extras
    if (
        not extras_raw
        and hasattr(track_or_payload, "original")
        and track_or_payload.original is not None
    ):
        extras_raw = getattr(track_or_payload.original, "extras", None)

    extras_dict = extract_extras_dict(extras_raw)

    # 3. Validasi entry_id
    entry_id: UUID | None = None
    raw_entry = extras_dict.get("entry_id")
    if raw_entry is not None:
        try:
            entry_id = UUID(str(raw_entry))
        except Exception:
            entry_id = None

    # 4. Validasi generation
    generation: int | None = None
    raw_gen = extras_dict.get("generation")
    if raw_gen is not None:
        try:
            parsed_gen = int(raw_gen)
            if parsed_gen >= 0:
                generation = parsed_gen
        except Exception:
            generation = None

    # 5. Ekstrak guild_id dari extras jika belum didapatkan dari player
    if guild_id is None:
        raw_extra_gid = extras_dict.get("guild_id")
        if raw_extra_gid is not None:
            try:
                parsed_extra_gid = int(raw_extra_gid)
                if parsed_extra_gid > 0:
                    guild_id = parsed_extra_gid
            except Exception:
                pass

    if entry_id is None or generation is None:
        logger.warning(
            "Track payload tidak memiliki metadata extras yang valid",
            extra={
                "guild_id": guild_id,
                "has_entry_id": entry_id is not None,
                "has_generation": generation is not None,
            },
        )

    return guild_id, entry_id, generation
