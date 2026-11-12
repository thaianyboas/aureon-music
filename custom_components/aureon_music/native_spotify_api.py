"""Minimal Spotify Web API transport for the independent AUREON Music engine.

No SpotifyPlus or Spotcast imports. This is deliberately not registered as a
Home Assistant service yet: the existing playback path remains untouched.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from typing import Any

from .native_oauth import NativeSpotifyTokenManager
from .native_spotify_oauth import retry_after

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
        self._blocked_until = 0.0

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "://" in path
            or "?" in path
            or "#" in path
        ):
            raise ValueError("Spotify API path must begin with a single slash")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError("Unsupported Spotify API method")
        rejected_access: str | None = None
        access: str | None = None
        read_retries = 0
        while True:
            if time.monotonic() < self._blocked_until:
                raise SpotifyApiError(429)
            if access is None:
                access = await self._tokens.access_token(
                    force_refresh=rejected_access is not None,
                    rejected_access_token=rejected_access,
                )
            try:
                async with self._session.request(
                    method,
                    BASE_URL + path,
                    headers={"Authorization": f"Bearer {access}"},
                    params=params,
                    json=json,
                    allow_redirects=False,
                    timeout=10,
                ) as response:
                    status = response.status
                    if status == 401 and rejected_access is None:
                        rejected_access, access = access, None
                        continue
                    if status in (202, 204):
                        return None
                    if status == 429:
                        delay = retry_after(
                            getattr(response, "headers", {}).get("Retry-After")
                        )
                        self._blocked_until = max(
                            self._blocked_until, time.monotonic() + delay
                        )
                        raise SpotifyApiError(status)
                    if 200 <= status < 300:
                        try:
                            return await response.json(content_type=None)
                        except (ValueError, TypeError):
                            raise SpotifyApiError(502) from None
                    if method != "GET" or not 500 <= status <= 599 or read_retries >= 2:
                        raise SpotifyApiError(status)
            except (OSError, TimeoutError):
                if method != "GET" or read_retries >= 2:
                    raise
            await asyncio.sleep(0.25 * 2**read_retries)
            read_retries += 1

    async def profile(self) -> Any:
        return await self.request("GET", "/me")

    async def devices(self) -> Any:
        return await self.request("GET", "/me/player/devices")

    async def playback(self) -> Any:
        return await self.request("GET", "/me/player")

    async def search(
        self, query: str, types: str = "track,album,artist,playlist", limit: int = 10
    ) -> Any:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        return await self.request(
            "GET",
            "/search",
            params={
                "q": query,
                "type": types,
                "limit": max(1, min(limit, 10)),
            },
        )

    async def playlists(self, limit: int = 20, offset: int = 0) -> Any:
        return await self.request(
            "GET",
            "/me/playlists",
            params={
                "limit": max(1, min(limit, 50)),
                "offset": max(0, offset),
            },
        )

    async def transfer_playback(self, device_id: str, *, play: bool = False) -> Any:
        if not device_id:
            raise ValueError("Device ID is required")
        return await self.request(
            "PUT",
            "/me/player",
            json={
                "device_ids": [device_id],
                "play": play,
            },
        )

    async def play(
        self,
        *,
        device_id: str | None = None,
        uri: str | None = None,
        offset_uri: str | None = None,
    ) -> Any:
        params = {"device_id": device_id} if device_id else None
        for value in (uri, offset_uri):
            if value and not re.fullmatch(
                r"spotify:(track|episode|album|artist|playlist|show):[A-Za-z0-9]+",
                value,
            ):
                raise ValueError("Playback URI must be a Spotify URI")
        if uri and uri.startswith(("spotify:track:", "spotify:episode:")):
            payload = {"uris": [uri]}
        else:
            payload = {"context_uri": uri} if uri else {}
            # start an album or playlist at a given song (resuming where it stopped)
            if (
                uri
                and offset_uri
                and uri.startswith(("spotify:album:", "spotify:playlist:"))
            ):
                payload["offset"] = {"uri": offset_uri}
        return await self.request("PUT", "/me/player/play", params=params, json=payload)

    async def pause(self, *, device_id: str | None = None) -> Any:
        return await self.request(
            "PUT",
            "/me/player/pause",
            params={"device_id": device_id} if device_id else None,
        )

    async def next_track(self) -> None:
        await self.request("POST", "/me/player/next")

    async def previous_track(self) -> None:
        await self.request("POST", "/me/player/previous")

    async def seek(self, position_ms: int) -> None:
        await self.request(
            "PUT", "/me/player/seek", params={"position_ms": max(0, position_ms)}
        )

    async def volume(self, percent: int) -> None:
        await self.request(
            "PUT",
            "/me/player/volume",
            params={"volume_percent": max(0, min(100, percent))},
        )

    async def shuffle(self, enabled: bool) -> None:
        await self.request(
            "PUT", "/me/player/shuffle", params={"state": str(enabled).lower()}
        )

    async def repeat(self, state: str) -> None:
        if state not in {"off", "track", "context"}:
            raise ValueError("Unsupported Spotify repeat mode")
        await self.request("PUT", "/me/player/repeat", params={"state": state})

    async def queue(self) -> Any:
        return await self.request("GET", "/me/player/queue")

    async def enqueue(self, uri: str) -> None:
        if not re.fullmatch(r"spotify:(track|episode):[A-Za-z0-9]+", uri):
            raise ValueError("Unsupported Spotify queue item")
        await self.request("POST", "/me/player/queue", params={"uri": uri})
