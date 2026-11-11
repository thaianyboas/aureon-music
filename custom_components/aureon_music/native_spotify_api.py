"""Minimal Spotify Web API transport for the independent AUREON Music engine.

No SpotifyPlus or Spotcast imports. This is deliberately not registered as a
Home Assistant service yet: the existing playback path remains untouched.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .native_oauth import NativeSpotifyTokenManager

BASE_URL = "https://api.spotify.com/v1"


class SpotifyApiError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        super().__init__(f"Spotify API HTTP {status}: {message[:200]}")


class NativeSpotifyApi:
    """Account-scoped, async Spotify Web API client using an injected HTTP session."""

    def __init__(self, session: Any, tokens: NativeSpotifyTokenManager) -> None:
        self._session = session
        self._tokens = tokens

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Spotify API path must begin with a single slash")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError("Unsupported Spotify API method")
        for attempt in range(2):
            access = await self._tokens.access_token(force_refresh=bool(attempt))
            async with self._session.request(
                method,
                BASE_URL + path,
                headers={"Authorization": f"Bearer {access}"},
                params=params,
                json=json,
            ) as response:
                if response.status == 401 and attempt == 0:
                    continue
                if response.status == 204:
                    return None
                if response.status >= 400:
                    # Do not log response headers, access tokens or user content.
                    raise SpotifyApiError(response.status)
                return await response.json(content_type=None)
        raise SpotifyApiError(401, "Spotify authorization rejected")

    async def profile(self) -> Any:
        return await self.request("GET", "/me")

    async def devices(self) -> Any:
        return await self.request("GET", "/me/player/devices")

    async def playback(self) -> Any:
        return await self.request("GET", "/me/player")

    async def search(self, query: str, types: str = "track,album,artist,playlist", limit: int = 20) -> Any:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        return await self.request("GET", "/search", params={
            "q": query, "type": types, "limit": max(1, min(limit, 50)),
        })

    async def playlists(self, limit: int = 20, offset: int = 0) -> Any:
        return await self.request("GET", "/me/playlists", params={
            "limit": max(1, min(limit, 50)), "offset": max(0, offset),
        })

    async def transfer_playback(self, device_id: str, *, play: bool = False) -> Any:
        if not device_id:
            raise ValueError("Device ID is required")
        return await self.request("PUT", "/me/player", json={
            "device_ids": [device_id], "play": play,
        })

    async def play(self, *, device_id: str | None = None, uri: str | None = None) -> Any:
        params = {"device_id": device_id} if device_id else None
        payload = {"context_uri": uri} if uri and not uri.startswith("spotify:track:") else (
            {"uris": [uri]} if uri else {}
        )
        return await self.request("PUT", "/me/player/play", params=params, json=payload)

    async def pause(self, *, device_id: str | None = None) -> Any:
        return await self.request("PUT", "/me/player/pause", params={"device_id": device_id} if device_id else None)
