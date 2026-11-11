"""Spotify OAuth refresh transport for the future native AUREON Música provider.

Uses Spotify Accounts token endpoint directly. Callers supply the OAuth client
identifier, optionally the client secret, and an aiohttp-compatible session.
For public clients, PKCE refresh uses client_id without a client secret.
No token values, request bodies, or Spotify error descriptions enter exceptions.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from typing import Any

from .native_oauth import SpotifyReauthorizationRequired

TOKEN_URL = "https://accounts.spotify.com/api/token"


class SpotifyTokenRefreshError(Exception):
    """A retryable OAuth refresh failure; existing credentials remain intact."""


def retry_after(value: str | None) -> int:
    """Parse Spotify's Retry-After in seconds."""
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


class SpotifyOAuthRefresher:
    def __init__(
        self, session: Any, client_id: str, client_secret: str | None = None
    ) -> None:
        if not client_id:
            raise ValueError("Spotify OAuth client ID is required")
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._blocked_until = 0.0
        self._failures = 0

    async def __call__(self, refresh_token: str) -> Mapping[str, Any]:
        if not refresh_token:
            raise SpotifyReauthorizationRequired("Spotify authorization is missing")
        if time.monotonic() < self._blocked_until:
            raise SpotifyTokenRefreshError("Spotify token refresh is in backoff")
        try:
            return await self._exchange(refresh_token)
        except SpotifyReauthorizationRequired:
            raise
        except (OSError, TimeoutError, SpotifyTokenRefreshError):
            self._failures += 1
            delay = min(60, 2 ** min(self._failures - 1, 6))
            self._blocked_until = max(self._blocked_until, time.monotonic() + delay)
            raise

    async def _exchange(self, refresh_token: str) -> Mapping[str, Any]:
        data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self._client_secret is None:
            data["client_id"] = self._client_id
        else:
            credentials = f"{self._client_id}:{self._client_secret}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode(
                "ascii"
            )

        async with self._session.post(
            TOKEN_URL, data=data, headers=headers, allow_redirects=False, timeout=10
        ) as response:
            if response.status == 429:
                delay = retry_after(getattr(response, "headers", {}).get("Retry-After"))
                self._blocked_until = time.monotonic() + delay
            if response.status == 429 or response.status >= 500:
                raise SpotifyTokenRefreshError(
                    f"Spotify token endpoint unavailable ({response.status})"
                )
            if response.status != 200:
                # Only an explicit invalid_grant is treated as revoked.
                # Never leak the response's error_description, tokens or headers.
                try:
                    body = await response.json(content_type=None)
                except (ValueError, TypeError):
                    body = {}
                if (
                    response.status == 400
                    and isinstance(body, dict)
                    and body.get("error") == "invalid_grant"
                ):
                    raise SpotifyReauthorizationRequired(
                        "Spotify authorization was revoked"
                    )
                raise SpotifyTokenRefreshError(
                    f"Spotify token refresh rejected ({response.status})"
                )
            try:
                body = await response.json(content_type=None)
            except (ValueError, TypeError):
                raise SpotifyTokenRefreshError(
                    "Invalid Spotify token response"
                ) from None
            if not isinstance(body, dict) or not isinstance(
                body.get("access_token"), str
            ):
                raise SpotifyTokenRefreshError("Invalid Spotify token response")
            self._failures = 0
            return body
