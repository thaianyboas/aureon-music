"""Independent Spotify OAuth token lifecycle for the future native AUREON Music engine.

This module does not replace the currently installed provider or register HA services.
Callers supply the official OAuth refresh operation and an awaited durable save operation.
No secrets or tokens are logged.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Token = dict[str, Any]
Refresh = Callable[[str], Awaitable[Mapping[str, Any]]]
Save = Callable[[Token], Awaitable[None]]


class SpotifyReauthorizationRequired(Exception):
    """The authorization is missing or Spotify has explicitly revoked it."""


class NativeSpotifyTokenManager:
    """Single-flight refresh with durable rotated-token storage.

    A manager is scoped to one Spotify account. Create it with the token saved in
    HA's config entry; pass a persistence callback that awaits storage completion.
    A network failure propagates without discarding the saved refresh token.
    """

    def __init__(
        self,
        token: Mapping[str, Any],
        refresh: Refresh,
        save: Save,
        *,
        clock: Callable[[], float] = time.time,
        refresh_margin: int = 120,
    ) -> None:
        self._token = dict(token)
        self._refresh = refresh
        self._save = save
        self._clock = clock
        self._margin = refresh_margin
        self._lock = asyncio.Lock()

    @property
    def token(self) -> Token:
        return dict(self._token)

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            token = self._token
            if not force_refresh and token.get("access_token") and (
                float(token.get("expires_at") or 0) > self._clock() + self._margin
            ):
                return str(token["access_token"])

            refresh_token = token.get("refresh_token")
            if not refresh_token:
                raise SpotifyReauthorizationRequired("Spotify authorization is missing")

            try:
                refreshed = dict(await self._refresh(str(refresh_token)))
            except SpotifyReauthorizationRequired:
                raise
            # Temporary HTTP, DNS and connection errors must not trigger reauth.
            # The refresh callback must classify an explicit invalid_grant as
            # SpotifyReauthorizationRequired.
            new_access = refreshed.get("access_token")
            if not new_access:
                raise ValueError("Spotify refresh returned no access token")

            merged = {**token, **refreshed}
            # Spotify may omit refresh_token when it has not rotated.
            merged["refresh_token"] = refreshed.get("refresh_token") or refresh_token
            if "expires_at" not in refreshed:
                merged["expires_at"] = self._clock() + float(refreshed.get("expires_in", 3600))
            # Do not expose a new token until its rotated refresh token is saved.
            await self._save(dict(merged))
            self._token = merged
            return str(new_access)
