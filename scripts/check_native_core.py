"""Dependency-light CI smoke tests for the isolated native Spotify engine.

Does not import Home Assistant or legacy SpotifyPlus/Spotcast. Full HA and
migration tests are separate and are NOT implied by these checks.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "custom_components" / "aureon_music"


def load_modules():
    for filename in (
        "native_oauth.py",
        "native_spotify_oauth.py",
        "native_spotify_api.py",
        "native_ha_adapter.py",
    ):
        ast.parse((MODULES / filename).read_text(encoding="utf-8"), filename=filename)
    for name, path in (
        ("custom_components", ROOT / "custom_components"),
        ("custom_components.aureon_music", MODULES),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    loaded = {}
    for name in ("native_oauth", "native_spotify_oauth", "native_spotify_api"):
        fullname = f"custom_components.aureon_music.{name}"
        spec = importlib.util.spec_from_file_location(fullname, MODULES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


class Response:
    def __init__(self, status, body=None):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, **_):
        return self.body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


async def run_checks():
    modules = load_modules()
    manager_type = modules["native_oauth"].NativeSpotifyTokenManager
    refreshed, saved = [], []
    fail_save = True

    async def refresh(old):
        refreshed.append(old)
        await asyncio.sleep(0)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    async def save(token):
        nonlocal fail_save
        if fail_save:
            fail_save = False
            raise OSError("simulated disk error")
        saved.append(dict(token))

    manager = manager_type(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, save, clock=lambda: 100,
    )
    try:
        await manager.access_token()
        raise AssertionError("first save must fail")
    except OSError:
        pass
    assert manager.token["refresh_token"] == "original"
    assert await manager.access_token() == "new"
    assert refreshed == ["original"]
    assert saved[-1]["refresh_token"] == "rotated"

    async def no_refresh(_):
        raise AssertionError("restart must load the rotated credential")

    restarted = manager_type(saved[-1], no_refresh, save, clock=lambda: 150)
    assert await restarted.access_token() == "new"

    async def always_save(_):
        return None

    manager = manager_type(
        {"access_token": "old", "refresh_token": "original", "expires_at": 9999},
        refresh, always_save, clock=lambda: 100,
    )
    refreshed.clear()
    results = await asyncio.gather(*(
        manager.access_token(force_refresh=True, rejected_access_token="old")
        for _ in range(8)
    ))
    assert results == ["new"] * 8 and refreshed == ["original"]

    started, proceed = asyncio.Event(), asyncio.Event()
    refreshed.clear()

    async def blocking_save(_):
        if not started.is_set():
            started.set()
            await proceed.wait()

    manager = manager_type(
        {"access_token": "old", "refresh_token": "original", "expires_at": 0},
        refresh, blocking_save, clock=lambda: 100,
    )
    task = asyncio.create_task(manager.access_token())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    try:
        await task
        raise AssertionError("save task should be cancelled")
    except asyncio.CancelledError:
        pass
    proceed.set()
    assert await manager.access_token() == "new"
    assert refreshed == ["original"]

    class Tokens:
        def __init__(self):
            self.calls = []

        async def access_token(self, *, force_refresh=False, rejected_access_token=None):
            self.calls.append((force_refresh, rejected_access_token))
            return "new" if force_refresh else "old"

    session = Session(Response(401), Response(200, {"id": "ok"}))
    tokens = Tokens()
    assert await modules["native_spotify_api"].NativeSpotifyApi(session, tokens).profile() == {"id": "ok"}
    assert tokens.calls == [(False, None), (True, "old")]

    session = Session(Response(400, {"error": "invalid_grant", "error_description": "secret"}))
    try:
        await modules["native_spotify_oauth"].SpotifyOAuthRefresher(session, "client")("old")
        raise AssertionError("revoked token must require reauthorization")
    except modules["native_oauth"].SpotifyReauthorizationRequired as error:
        assert "secret" not in str(error)

    print("Native Spotify smoke checks passed: syntax, rotation, retry, restart, concurrency, cancellation, API and OAuth")


if __name__ == "__main__":
    asyncio.run(run_checks())
