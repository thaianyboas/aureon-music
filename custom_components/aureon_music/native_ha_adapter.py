"""Account-scoped Spotify credentials with a checked atomic write."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .native_oauth import NativeSpotifyTokenManager, SpotifyReauthorizationRequired
from .native_spotify_api import NativeSpotifyApi
from .native_spotify_oauth import SpotifyOAuthRefresher


class NativeTokenStore:
    """Read and write the HA storage envelope without swallowing write failures."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ValueError("Invalid Spotify credential key")
        self.hass = hass
        self.key = f"aureon_music.native_token_{key}"
        self.path = Path(hass.config.path(".storage", self.key))

    async def async_load(self) -> dict[str, Any] | None:
        return await self.hass.async_add_executor_job(self._load)

    def _load(self) -> dict[str, Any] | None:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            raise OSError("Invalid Spotify credential storage") from None
        if (
            not isinstance(stored, dict)
            or stored.get("version") != 1
            or stored.get("key") != self.key
            or not isinstance(stored.get("data"), dict)
        ):
            raise OSError("Invalid Spotify credential storage")
        return stored["data"]

    async def async_save(self, token: dict[str, Any]) -> None:
        write = self.hass.async_add_executor_job(self._save, token)
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            # Hold the token manager lock until the executor finishes replacing
            # the file, so a cancelled write cannot later overwrite a newer one.
            await asyncio.shield(write)
            raise

    def _save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as temporary:
            path = Path(temporary.name)
            try:
                json.dump(
                    {"version": 1, "minor_version": 1, "key": self.key, "data": token},
                    temporary,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary.close()
                path.replace(self.path)
            finally:
                path.unlink(missing_ok=True)

    async def async_remove(self) -> None:
        await self.hass.async_add_executor_job(self.path.unlink, True)


async def async_create_native_spotify_client(
    hass: HomeAssistant, entry: ConfigEntry, *, client_id: str
) -> NativeSpotifyApi:
    """Load credentials for an explicitly native entry."""
    if entry.data.get("aureon_kind") != "native":
        raise ValueError("Only native AUREON Música entries are supported")
    store = NativeTokenStore(hass, entry.data.get("credential_key", entry.entry_id))
    token = await store.async_load()
    if token is None and isinstance(entry.data.get("token"), Mapping):
        token = dict(entry.data["token"])
        await store.async_save(token)
    if not token or not token.get("refresh_token"):
        raise SpotifyReauthorizationRequired("Spotify authorization is missing")
    session = async_get_clientsession(hass)
    tokens = NativeSpotifyTokenManager(
        token, SpotifyOAuthRefresher(session, client_id), store.async_save
    )
    return NativeSpotifyApi(session, tokens)
