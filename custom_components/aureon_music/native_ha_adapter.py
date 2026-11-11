"""Home Assistant storage adapter for *opt-in* native Spotify entries only.

Legacy SpotifyPlus and Spotcast entries are never migrated by this helper.
Rotated tokens are written to a private, atomic Home Assistant Store and loaded
from that Store on startup. Config-entry updates alone are not durable.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .native_oauth import NativeSpotifyTokenManager
from .native_spotify_api import NativeSpotifyApi
from .native_spotify_oauth import SpotifyOAuthRefresher


async def async_create_native_spotify_client(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    client_id: str,
    client_secret: str | None = None,
) -> NativeSpotifyApi:
    """Load last persisted native credentials before allowing API operations.

    A separate Store is required because config-entry async_update_entry()
    schedules, rather than awaits, the configuration save. Do not use it as a
    token-rotation durability boundary. The initial native flow must still
    commit its first token before activating this helper.
    """
    if entry.data.get("aureon_kind") != "native":
        raise ValueError("Only native AUREON Música entries are supported")
    initial = entry.data.get("token")
    if not isinstance(initial, Mapping):
        raise ValueError("Native Spotify token is missing")

    store = Store[dict[str, Any]](
        hass,
        1,
        f"aureon_music.native_token_{entry.entry_id}",
        private=True,
        atomic_writes=True,
    )
    saved = await store.async_load()
    if saved is not None and (
        not isinstance(saved, Mapping) or not saved.get("refresh_token")
    ):
        raise ValueError("Saved native Spotify credentials are invalid")
    token = saved if saved is not None else initial
    session = async_get_clientsession(hass)

    async def save(updated: dict[str, Any]) -> None:
        # This awaited write is the authority for rotated credentials.
        # Do not overwrite it with delayed config-entry persistence.
        await store.async_save(dict(updated))

    tokens = NativeSpotifyTokenManager(
        token,
        SpotifyOAuthRefresher(session, client_id, client_secret),
        save,
    )
    return NativeSpotifyApi(session, tokens)
