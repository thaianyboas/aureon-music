"""Home Assistant adapter for the isolated native Spotify OAuth foundation.

This module is intentionally not imported by the legacy config flow. It only
accepts an explicitly native entry with a token mapping under 'token'.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .native_oauth import NativeSpotifyTokenManager
from .native_spotify_api import NativeSpotifyApi
from .native_spotify_oauth import SpotifyOAuthRefresher


def create_native_spotify_client(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    client_id: str,
    client_secret: str | None = None,
) -> NativeSpotifyApi:
    """Create a client without modifying or migrating existing config entries.

    This helper is reserved for a future explicitly native flow. SpotifyPlus
    and Spotcast entries must never be passed here or rewritten automatically.
    """
    if entry.data.get("aureon_kind") != "native":
        raise ValueError("Only native AUREON Música entries are supported")
    token = entry.data.get("token")
    if not isinstance(token, Mapping):
        raise ValueError("Native Spotify token is missing")

    session = async_get_clientsession(hass)

    async def save(updated: dict[str, Any]) -> None:
        # HA persists entry data through the config-entry manager.
        # Never store token material in logs, diagnostics or options.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": updated}
        )

    tokens = NativeSpotifyTokenManager(
        token,
        SpotifyOAuthRefresher(session, client_id, client_secret),
        save,
    )
    return NativeSpotifyApi(session, tokens)
