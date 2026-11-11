"""Exercise the opt-in player and private persistence using a real HA instance."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant import config_entries
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers.config_entry_oauth2_flow import _decode_jwt
from homeassistant.helpers.entity_platform import async_get_platforms
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aureon_music.native_ha_adapter import NativeTokenStore
from custom_components.aureon_music.native_spotify_oauth import TOKEN_URL


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, **_):
        return self.data


class Spotify:
    def __init__(self):
        self.profile_id = "fixture-user"
        self.offline = False
        self.requests = []
        self.device = {
            "id": "speaker-1",
            "name": "Fixture Speaker",
            "supports_volume": True,
        }

    def post(self, url, **kwargs):
        assert url == TOKEN_URL
        self.requests.append(("TOKEN", kwargs["data"]))
        return Response(
            {"access_token": "access", "refresh_token": "rotated", "expires_in": 3600}
        )

    def request(self, method, url, **kwargs):
        if self.offline:
            raise ConnectionError("Simulated connection loss")
        self.requests.append((method, url, kwargs))
        if url.endswith("/me"):
            return Response(
                {
                    "id": self.profile_id,
                    "account_id": self.profile_id,
                    "display_name": "Fixture Spotify",
                }
            )
        if url.endswith("/devices"):
            return Response(
                {"devices": [self.device, {"id": "restricted", "is_restricted": True}]}
            )
        if method == "GET" and url.endswith("/player"):
            return Response(
                {
                    "device": self.device,
                    "item": {"uri": "spotify:track:abc", "name": "Fixture Song"},
                    "is_playing": True,
                }
            )
        if url.endswith("/library/contains"):
            return Response([True])
        return Response(None, 204)


async def test_native_token_file_survives_reload_and_failed_write(hass, monkeypatch):
    store = NativeTokenStore(hass, "first")
    await store.async_save({"refresh_token": "previous"})
    original = store.path.read_bytes
    assert await hass.async_add_executor_job(original)
    assert (await hass.async_add_executor_job(store.path.stat)).st_mode & 0o777 == 0o600

    def fail(*_):
        raise OSError("disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("custom_components.aureon_music.native_ha_adapter.os.fsync", fail)
        with pytest.raises(OSError, match="disk unavailable"):
            await store.async_save({"refresh_token": "rotated"})
    assert await NativeTokenStore(hass, "first").async_load() == {
        "refresh_token": "previous"
    }
    await store.async_save({"refresh_token": "rotated"})
    assert await NativeTokenStore(hass, "first").async_load() == {
        "refresh_token": "rotated"
    }
    assert await NativeTokenStore(hass, "second").async_load() is None


@pytest.fixture
def spotify(hass, monkeypatch):
    spotify = Spotify()
    for module in ("native_config_flow", "native_ha_adapter"):
        monkeypatch.setattr(
            f"custom_components.aureon_music.{module}.async_get_clientsession",
            lambda _: spotify,
        )
    # The normal My Home Assistant redirect route is registered by HA auth.
    hass.config.components.add("my")
    hass.config.components.add(
        "zeroconf"
    )  # Legacy LAN discovery is outside the native test.
    return spotify


async def _complete(hass, external):
    query = parse_qs(urlsplit(external["url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    state = _decode_jwt(hass, query["state"][0])
    assert state["flow_id"] == external["flow_id"]
    await hass.config_entries.flow.async_configure(
        external["flow_id"], {"state": state, "code": "single-use-code"}
    )
    result = await hass.config_entries.flow.async_configure(external["flow_id"])
    return query, result


async def test_pkce_entry_and_player_services(hass, spotify, hass_read_only_user):
    legacy_data = {
        "aureon_kind": "spotifyplus",
        "token": {"refresh_token": "legacy-fixture"},
    }
    legacy_options = {"spotify_connect_default_device": "Legacy Speaker"}
    legacy = MockConfigEntry(
        domain="aureon_music",
        data=legacy_data,
        options=legacy_options,
        unique_id="fixture-user",
    )
    legacy.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(
        "aureon_music", context={"source": config_entries.SOURCE_USER}
    )
    assert "native" in flow["menu_options"]
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "native"}
    )
    external = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"client_id": "a" * 32}
    )
    query, result = await _complete(hass, external)
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    data = result["result"].data
    assert legacy.data == legacy_data
    assert legacy.options == legacy_options
    assert "token" not in data
    assert (await NativeTokenStore(hass, data["credential_key"]).async_load())[
        "refresh_token"
    ] == "rotated"
    request = spotify.requests[0][1]
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(request["code_verifier"].encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == [challenge]
    assert request["redirect_uri"] == query["redirect_uri"][0]
    (state,) = hass.states.async_all("media_player")
    assert state.attributes["media_title"] == "Fixture Song"
    args = {"entity_id": state.entity_id, "action": "get_spotify_connect_devices"}
    response = await hass.services.async_call(
        "aureon_music", "native_read", args, blocking=True, return_response=True
    )
    assert response == {state.entity_id: {"result": {"devices": [spotify.device]}}}
    await hass.services.async_call(
        "aureon_music",
        "native_write",
        {
            "entity_id": state.entity_id,
            "action": "player_transfer_playback",
            "arguments": {"device_id": "speaker-1"},
        },
        blocking=True,
    )
    assert any(
        method == "PUT" and payload["json"]["device_ids"] == ["speaker-1"]
        for method, *rest in spotify.requests
        if len(rest) == 2
        for _, payload in [rest]
    )
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "aureon_music",
            "native_write",
            {
                "entity_id": state.entity_id,
                "action": "player_media_play_tracks",
                "arguments": {"uris": "spotify:track:abc"},
            },
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "aureon_music",
            "native_write",
            {
                "entity_id": [state.entity_id, "media_player.other"],
                "action": "player_media_play_tracks",
                "arguments": {"uris": "spotify:track:abc"},
            },
            blocking=True,
        )
    assert await hass.config_entries.async_reload(result["result"].entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(state.entity_id).attributes["media_title"] == "Fixture Song"

    player = next(
        platform.entities[state.entity_id]
        for platform in async_get_platforms(hass, "aureon_music")
        if state.entity_id in platform.entities
    )
    credentials = NativeTokenStore(hass, data["credential_key"])
    saved = await credentials.async_load()
    spotify.offline = True
    await player.coordinator.async_refresh()
    assert hass.states.get(state.entity_id).state == "unavailable"
    assert await credentials.async_load() == saved
    spotify.offline = False
    await player.coordinator.async_refresh()
    assert hass.states.get(state.entity_id).state == "playing"
    assert await credentials.async_load() == saved


async def test_reauth_rejects_different_spotify_account(hass, spotify):
    entry = MockConfigEntry(
        domain="aureon_music",
        version=2,
        minor_version=1,
        unique_id="native:fixture-user",
        data={
            "aureon_kind": "native",
            "client_id": "a" * 32,
            "credential_key": "first",
            "spotify_account_id": "fixture-user",
            "spotify_display_name": "Fixture Spotify",
        },
    )
    entry.add_to_hass(hass)
    store = NativeTokenStore(hass, "first")
    await store.async_save(
        {
            "access_token": "existing",
            "refresh_token": "existing",
            "expires_at": time.time() + 3600,
        }
    )
    flow = await hass.config_entries.flow.async_init(
        "aureon_music",
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    external = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    spotify.profile_id = "other"
    _, result = await _complete(hass, external)
    assert result["reason"] == "wrong_account"
    assert entry.data["credential_key"] == "first"
    assert (await store.async_load())["refresh_token"] == "existing"
    spotify.profile_id = "fixture-user"
    flow = await hass.config_entries.flow.async_init(
        "aureon_music",
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    external = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    _, result = await _complete(hass, external)
    assert result["reason"] == "reauth_successful"
    assert entry.data["credential_key"] != "first"
    assert (await NativeTokenStore(hass, entry.data["credential_key"]).async_load())[
        "refresh_token"
    ] == "rotated"
