"""Tests for the independent Spotify token manager (no live Spotify credentials)."""
import asyncio
import pytest

from custom_components.aureon_music.native_oauth import (
    NativeSpotifyTokenManager,
    SpotifyReauthorizationRequired,
)


@pytest.mark.asyncio
async def test_parallel_refresh_stores_rotated_token_once():
    calls, stored = [], []

    async def refresh(old):
        calls.append(old)
        await asyncio.sleep(0.01)
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

    async def save(token):
        stored.append(dict(token))

    manager = NativeSpotifyTokenManager(
        {"access_token": "old-access", "refresh_token": "old-refresh", "expires_at": 0},
        refresh, save, clock=lambda: 100,
    )
    results = await asyncio.gather(*(manager.access_token() for _ in range(8)))
    assert results == ["new-access"] * 8
    assert calls == ["old-refresh"]
    assert len(stored) == 1
    assert stored[0]["refresh_token"] == "new-refresh"


@pytest.mark.asyncio
async def test_transient_failure_keeps_existing_credentials():
    async def refresh(_):
        raise ConnectionError("temporarily offline")

    async def save(_):
        raise AssertionError("must not save after failed refresh")

    manager = NativeSpotifyTokenManager(
        {"access_token": "old", "refresh_token": "still-valid", "expires_at": 0},
        refresh, save,
    )
    with pytest.raises(ConnectionError):
        await manager.access_token()
    assert manager.token["refresh_token"] == "still-valid"


@pytest.mark.asyncio
async def test_missing_refresh_token_requires_reauthorization():
    async def refresh(_):
        raise AssertionError("must not refresh")

    async def save(_):
        raise AssertionError("must not save")

    manager = NativeSpotifyTokenManager({"expires_at": 0}, refresh, save)
    with pytest.raises(SpotifyReauthorizationRequired):
        await manager.access_token()


@pytest.mark.asyncio
async def test_save_failure_does_not_publish_unpersisted_token():
    async def refresh(_):
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    async def save(_):
        raise OSError("storage unavailable")

    manager = NativeSpotifyTokenManager(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, save,
    )
    with pytest.raises(OSError):
        await manager.access_token()
    assert manager.token["refresh_token"] == "original"
