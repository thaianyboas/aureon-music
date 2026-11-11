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
    """Serialize refresh and persist rotations before exposing their tokens.

    If saving a rotated token fails or is cancelled, hold it in memory and
    retry its save under the account lock on the next request. Never reuse the
    old refresh token while a rotated one is pending. A process crash before
    saving is still an unavoidable risk and is not claimed to be recoverable.
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
        self._pending: Token | None = None
        self._refresh = refresh
        self._save = save
        self._clock = clock
        self._margin = refresh_margin
        self._lock = asyncio.Lock()

    @property
    def token(self) -> Token:
        """Last confirmed persisted token (copy; pending credentials excluded)."""
        return dict(self._token)

    async def _persist_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        # A failure or cancellation deliberately leaves _pending available
        # for retry. Save callbacks must be idempotent for the same token.
        await self._save(dict(pending))
        self._token = pending
        self._pending = None

    async def access_token(
        self, *, force_refresh: bool = False, rejected_access_token: str | None = None
    ) -> str:
        async with self._lock:
            # CRITICAL: retry saving before using credentials or refreshing.
            # The previous refresh token may already have been invalidated.
            await self._persist_pending()
            token = self._token
            if (
                force_refresh
                and rejected_access_token is not None
                and token.get("access_token") != rejected_access_token
            ):
                return str(token["access_token"])
            if (
                not force_refresh
                and token.get("access_token")
                and float(token.get("expires_at") or 0) > self._clock() + self._margin
            ):
                return str(token["access_token"])

            refresh_token = token.get("refresh_token")
            if not refresh_token:
                raise SpotifyReauthorizationRequired("Spotify authorization is missing")

            # A transient failure leaves last persisted token untouched.
            refreshed = dict(await self._refresh(str(refresh_token)))
            new_access = refreshed.get("access_token")
            if not isinstance(new_access, str) or not new_access:
                raise ValueError("Spotify refresh returned no access token")

            merged = {**token, **refreshed}
            merged["refresh_token"] = refreshed.get("refresh_token") or refresh_token
            if "expires_at" not in refreshed:
                merged["expires_at"] = self._clock() + float(refreshed.get("expires_in", 3600))

            # Set before awaiting the save so failed/cancelled operations do
            # not lose the rotated refresh token inside this process.
            self._pending = merged
            await self._persist_pending()
            return new_access
