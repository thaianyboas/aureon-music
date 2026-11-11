"""AUREON Música: refreshed Spotify tokens must be stored before the next refresh, so a rotated refresh token is
never reused (that caused 'Authentication expired' several times a day)."""
import asyncio, time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL
URLT = URL("https://accounts.spotify.com/api/token")
INFO = RequestInfo(URLT, "POST", CIMultiDictProxy(CIMultiDict()), URLT)
from pytest_homeassistant_custom_component.common import MockConfigEntry
import custom_components.aureon_music.spotifyplus_core as core


class RotatingSpotify:
    """Fake Spotify token server: every refresh hands out a new refresh token and revokes the old one."""
    def __init__(self, status_on_refresh=None):
        self.name, self.domain, self.client_id = "Thaiany", "aureon_music_client", "client"
        self.current, self.n, self.refused, self.status = "rt0", 0, 0, status_on_refresh
    async def async_refresh_token(self, token):
        await asyncio.sleep(0.05)
        if self.status:
            raise ClientResponseError(INFO, (), status=self.status, message="Service Unavailable")
        if token.get("refresh_token") != self.current:
            self.refused += 1
            raise ClientResponseError(INFO, (), status=400, message="Bad Request")
        self.n += 1
        self.current = f"rt{self.n}"
        return {**token, "access_token": f"at{self.n}", "refresh_token": self.current, "expires_in": 3600, "expires_at": time.time() + 3600}


class Stop(Exception):
    pass


async def capture_updater(hass, impl):
    entry = MockConfigEntry(domain="aureon_music", title="Thaiany", data={"auth_implementation": impl.domain, "id": "thaiany", "token": {
        "access_token": "at0", "refresh_token": "rt0", "token_type": "Bearer", "expires_in": 3600, "expires_at": time.time() + 3600, "scope": " ".join(core.SPOTIFY_SCOPES)}})
    entry.add_to_hass(hass)
    REAUTH.clear()
    async def spy(domain, *, context=None, data=None):
        REAUTH.append(context or {})
    hass.config_entries.flow.async_init = spy
    got = {}
    def fake_client(*args, **kw):
        got["updater"] = args[3]
        raise Stop()
    with patch.object(core, "async_get_config_entry_implementation", return_value=impl), \
         patch.object(core.zeroconf, "async_get_instance", return_value=None), \
         patch.object(core, "SpotifyClient", fake_client):
        try:
            await core.async_setup_entry(hass, entry)
        except Exception:
            pass
    assert "updater" in got
    hass.data.setdefault(core.DATA_KEY, {})[entry.entry_id] = SimpleNamespace(runtime_data={})
    # the access token has expired now
    hass.config_entries.async_update_entry(entry, data={**entry.data, "token": {**entry.data["token"], "expires_at": time.time() - 10}})
    return entry, got["updater"]


REAUTH = []
def reauth_flows(hass):
    return [c for c in REAUTH if c.get("source") == "reauth"]


async def test_parallel_refreshes_use_the_newest_refresh_token(hass):
    impl = RotatingSpotify()
    entry, updater = await capture_updater(hass, impl)
    tokens = await asyncio.gather(*[hass.async_add_executor_job(updater) for _ in range(6)])
    await hass.async_block_till_done()
    assert impl.refused == 0
    assert impl.n == 1
    assert entry.data["token"]["refresh_token"] == impl.current
    assert all(t["refresh_token"] == impl.current for t in tokens)
    assert reauth_flows(hass) == []
    # an hour later: still fine, no re-authentication
    hass.config_entries.async_update_entry(entry, data={**entry.data, "token": {**entry.data["token"], "expires_at": time.time() - 10}})
    await asyncio.gather(*[hass.async_add_executor_job(updater) for _ in range(6)])
    await hass.async_block_till_done()
    assert impl.refused == 0 and impl.n == 2 and reauth_flows(hass) == []


async def test_server_trouble_never_asks_to_log_in_again(hass):
    impl = RotatingSpotify(status_on_refresh=503)
    entry, updater = await capture_updater(hass, impl)
    with pytest.raises(Exception):
        await hass.async_add_executor_job(updater)
    await hass.async_block_till_done()
    assert reauth_flows(hass) == []
    assert entry.data["token"]["refresh_token"] == "rt0"


async def test_a_really_revoked_token_still_asks_once(hass):
    impl = RotatingSpotify()
    impl.current = "revoked-on-spotify"
    entry, updater = await capture_updater(hass, impl)
    await hass.async_add_executor_job(updater)
    await hass.async_block_till_done()
    assert len(reauth_flows(hass)) == 1
