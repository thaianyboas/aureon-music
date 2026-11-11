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


@pytest.mark.asyncio
async def test_parallel_rejected_access_token_only_rotates_once():
    calls = []

    async def refresh(old):
        calls.append(old)
        await asyncio.sleep(0.01)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    async def save(_):
        pass

    manager = NativeSpotifyTokenManager(
        {"access_token": "expired", "refresh_token": "old", "expires_at": 9999},
        refresh, save, clock=lambda: 100,
    )
    results = await asyncio.gather(*(
        manager.access_token(force_refresh=True, rejected_access_token="expired")
        for _ in range(6)
    ))
    assert results == ["new"] * 6
    assert calls == ["old"]


@pytest.mark.asyncio
async def test_failed_save_retries_pending_rotation_without_second_refresh():
    refresh_calls, persisted = [], []
    should_fail = True

    async def refresh(old):
        refresh_calls.append(old)
        return {"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600}

    async def save(token):
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise OSError("disk unavailable")
        persisted.append(dict(token))

    manager = NativeSpotifyTokenManager(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, save, clock=lambda: 100,
    )
    with pytest.raises(OSError):
        await manager.access_token()
    assert manager.token["refresh_token"] == "original"
    assert await manager.access_token() == "new-access"
    assert refresh_calls == ["original"]
    assert persisted[0]["refresh_token"] == "rotated"


@pytest.mark.asyncio
async def test_cancelled_save_keeps_rotation_for_next_request():
    refresh_calls, persisted = [], []
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def refresh(old):
        refresh_calls.append(old)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    async def save(token):
        if not started.is_set():
            started.set()
            await proceed.wait()
        persisted.append(dict(token))

    manager = NativeSpotifyTokenManager(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, save, clock=lambda: 100,
    )
    task = asyncio.create_task(manager.access_token())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    proceed.set()
    assert await manager.access_token() == "new"
    assert refresh_calls == ["original"]
    assert persisted[0]["refresh_token"] == "rotated"


@pytest.mark.asyncio
async def test_reloaded_persisted_rotation_works_after_restart():
    stored = []

    async def refresh(_):
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    async def save(token):
        stored.append(dict(token))

    manager = NativeSpotifyTokenManager(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, save, clock=lambda: 100,
    )
    assert await manager.access_token() == "new"

    async def should_not_refresh(_):
        raise AssertionError("must not refresh after successful restart")

    restarted = NativeSpotifyTokenManager(stored[-1], should_not_refresh, save, clock=lambda: 150)
    assert await restarted.access_token() == "new"
