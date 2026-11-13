"""Exercise the opt-in player and private persistence using a real HA instance."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant import config_entries
from homeassistant.components.config.config_entries import ConfigManagerFlowIndexView
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
        self.access_token = "access"
        self.refresh_token = "rotated"
        self.offline = False
        self.idle = False
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
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_in": 3600,
            }
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
            if self.idle:
                return Response(None, 204)
            return Response(
                {
                    "device": self.device,
                    "item": {"uri": "spotify:track:abc", "name": "Fixture Song"},
                    "is_playing": True,
                }
            )
        if method == "PUT" and url.endswith("/player") and self.idle:
            return Response(None, 404)
        if url.endswith("/player/recently-played"):
            return Response({"items": [{"track": {"uri": "spotify:track:recent"}}]})
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
    assert flow["type"] == "form"
    assert flow["step_id"] == "native"
    visible = ConfigManagerFlowIndexView(hass.config_entries.flow)._prepare_result_json(
        flow
    )
    assert visible["data_schema"][0]["type"] == "string"
    invalid = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"client_id": "not-a-client-id"}
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"client_id": "invalid_client_id"}

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

    spotify.idle = True
    await player.coordinator.async_refresh()
    assert hass.states.get(state.entity_id).state == "idle"
    spotify.requests.clear()
    target = {"entity_id": state.entity_id}
    await hass.services.async_call(
        "aureon_music",
        "native_write",
        {
            **target,
            "action": "player_transfer_playback",
            "arguments": {"device_id": "speaker-1"},
        },
        blocking=True,
    )
    assert hass.states.get(state.entity_id).attributes["source"] == "Fixture Speaker"
    assert not any(call[0] == "PUT" for call in spotify.requests)
    await hass.services.async_call(
        "aureon_music",
        "native_write",
        {
            **target,
            "action": "player_media_play_tracks",
            "arguments": {"uris": "spotify:track:abc"},
        },
        blocking=True,
    )
    play = next(call for call in spotify.requests if call[0] == "PUT")
    assert play[1].endswith("/player/play")
    assert play[2]["params"] == {"device_id": "speaker-1"}
    assert play[2]["json"] == {"uris": ["spotify:track:abc"]}
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    play = next(call for call in spotify.requests if call[0] == "PUT")
    assert play[2]["params"] == {"device_id": "speaker-1"}
    assert play[2]["json"] == {"uris": ["spotify:track:recent"]}

    # Spotify Connect may change the ID after the group reconnects.
    spotify.device = {**spotify.device, "id": "speaker-reconnected"}
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    play = next(call for call in spotify.requests if call[0] == "PUT")
    assert play[2]["params"] == {"device_id": "speaker-reconnected"}

    # Playback started on the phone is visible before the next coordinator poll.
    spotify.device["is_active"] = True
    spotify.idle = False
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    play = next(call for call in spotify.requests if call[0] == "PUT")
    assert play[2]["params"] == {"device_id": "speaker-reconnected"}
    assert play[2]["json"] == {}


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


async def test_native_accounts_keep_credentials_and_commands_separate(hass, spotify):
    async def connect(account):
        spotify.profile_id = account
        spotify.access_token = f"access-{account}"
        spotify.refresh_token = f"refresh-{account}"
        form = await hass.config_entries.flow.async_init(
            "aureon_music", context={"source": config_entries.SOURCE_USER}
        )
        external = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"client_id": "a" * 32}
        )
        _, result = await _complete(hass, external)
        await hass.async_block_till_done()
        return result

    first = (await connect("fixture-first"))["result"]
    second = (await connect("fixture-second"))["result"]
    duplicate = await connect("fixture-second")
    assert duplicate["type"] == "abort"
    assert duplicate["reason"] == "already_configured"
    assert first.data["credential_key"] != second.data["credential_key"]

    accounts = (first, second)
    stores = [
        NativeTokenStore(hass, entry.data["credential_key"]) for entry in accounts
    ]
    assert [(await store.async_load())["refresh_token"] for store in stores] == [
        "refresh-fixture-first",
        "refresh-fixture-second",
    ]

    # The fixture deliberately gives both accounts the same display name.
    entities = {
        state.attributes["sp_user_id"]: state.entity_id
        for state in hass.states.async_all("media_player")
    }
    assert len(set(entities.values())) == 2
    for entry in accounts:
        account = entry.data["spotify_user_id"]
        await hass.services.async_call(
            "aureon_music",
            "native_write",
            {
                "entity_id": entities[account],
                "action": "player_media_play_tracks",
                "arguments": {"uris": "spotify:track:abc"},
            },
            blocking=True,
        )
    assert [
        call[2]["headers"]["Authorization"]
        for call in spotify.requests
        if call[0] == "PUT" and call[1].endswith("/player/play")
    ] == ["Bearer access-fixture-first", "Bearer access-fixture-second"]

    spotify.profile_id = "fixture-first"
    spotify.access_token = "access-first-renewed"
    spotify.refresh_token = "refresh-first-renewed"
    reauth = await hass.config_entries.flow.async_init(
        "aureon_music",
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": first.entry_id},
        data=first.data,
    )
    external = await hass.config_entries.flow.async_configure(reauth["flow_id"], {})
    _, result = await _complete(hass, external)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    renewed = NativeTokenStore(hass, first.data["credential_key"])
    assert (await renewed.async_load())["refresh_token"] == "refresh-first-renewed"
    assert await stores[0].async_load() is None
    assert (await stores[1].async_load())["refresh_token"] == "refresh-fixture-second"

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert await renewed.async_load() is None
    assert (await stores[1].async_load())["refresh_token"] == "refresh-fixture-second"
    assert hass.states.get(entities["fixture-second"]).state == "playing"


async def test_play_resumes_the_last_song_without_an_active_speaker(hass, spotify):
    """Owner 2026-11-12: Play answered 404 while the phone still showed the paused song."""
    form = await hass.config_entries.flow.async_init(
        "aureon_music", context={"source": config_entries.SOURCE_USER}
    )
    external = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"client_id": "a" * 32}
    )
    await _complete(hass, external)
    await hass.async_block_till_done()
    (state,) = hass.states.async_all("media_player")
    target = {"entity_id": state.entity_id}
    phone = {"id": "phone", "name": "Phone", "is_active": False}
    speaker = {"id": "wohnung", "name": "Wohnung Musik", "is_active": False}
    paused = {
        "device": phone,
        "item": {"uri": "spotify:track:last"},
        "is_playing": False,
    }
    mode = {"player": paused, "asleep": False, "missing_context": False}
    gateway_errors = []
    original = spotify.request

    def request(method, url, **kwargs):
        if url.endswith("/devices"):
            spotify.requests.append((method, url, kwargs))
            return Response({"devices": [phone, speaker]})
        if method == "GET" and url.endswith("/player"):
            spotify.requests.append((method, url, kwargs))
            return (
                Response(mode["player"], 200) if mode["player"] else Response(None, 204)
            )
        if url.endswith("/player/recently-played"):
            spotify.requests.append((method, url, kwargs))
            return Response(
                {
                    "items": [
                        {
                            "track": {"uri": "spotify:track:last"},
                            "context": {"uri": "spotify:playlist:mix"},
                        }
                    ]
                }
            )
        if method == "PUT" and url.endswith("/player") and mode["asleep"]:
            speaker["id"] = "wohnung-reconnected"
            mode["asleep"] = False
        if method == "PUT" and url.endswith("/player/play") and gateway_errors:
            spotify.requests.append((method, url, kwargs))
            if gateway_errors.pop(0):
                mode["player"] = {
                    "device": {**speaker, "is_active": True},
                    "item": {"uri": "spotify:track:last"},
                    "is_playing": True,
                }
            return Response(None, 502)
        if (
            method == "PUT"
            and url.endswith("/player/play")
            and (
                mode["asleep"]
                or (mode["missing_context"] and "context_uri" in kwargs["json"])
            )
        ):
            spotify.requests.append((method, url, kwargs))
            return Response(None, 404)
        return original(method, url, **kwargs)

    spotify.request = request
    player = next(
        p
        for p in async_get_platforms(hass, "aureon_music")
        if p.domain == "media_player"
    ).entities[state.entity_id]
    await player.coordinator.async_refresh()
    player._pending_device = speaker  # chosen on the player card while Spotify was idle

    # 1. the paused song moves with its position to the chosen speaker
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    puts = [c for c in spotify.requests if c[0] == "PUT"]
    assert puts[0][1].endswith("/me/player")
    assert puts[0][2]["json"] == {"device_ids": ["wohnung"], "play": True}

    # 2. nothing paused anywhere: the last song starts inside its playlist on the speaker
    mode["player"] = None
    await player.coordinator.async_refresh()
    await player.async_choose_device(speaker)
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    play = next(c for c in spotify.requests if c[0] == "PUT")
    assert play[2]["params"] == {"device_id": "wohnung"}
    assert play[2]["json"] == {
        "context_uri": "spotify:playlist:mix",
        "offset": {"uri": "spotify:track:last"},
    }

    # A deleted or inaccessible playlist does not prevent playing the track.
    mode["missing_context"] = True
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    puts = [c for c in spotify.requests if c[0] == "PUT"]
    assert len(puts) == 2
    assert puts[-1][2]["json"] == {"uris": ["spotify:track:last"]}
    mode["missing_context"] = False

    # 3. a sleeping speaker reconnects with a new ID after transfer
    mode["asleep"] = True
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    puts = [c for c in spotify.requests if c[0] == "PUT"]
    assert [p[1].rsplit("/", 1)[-1] for p in puts] == [
        "play",
        "play",
        "player",
        "play",
    ]
    assert puts[-1][2]["params"] == {"device_id": "wohnung-reconnected"}

    # A transient 502 is retried on the selected group. If playback already
    # started despite the error, retrying would restart the song.
    for already_started in (False, True):
        gateway_errors.append(already_started)
        spotify.requests.clear()
        await hass.services.async_call(
            "media_player", "media_play", target, blocking=True
        )
        plays = [c for c in spotify.requests if c[0] == "PUT"]
        assert len(plays) == (1 if already_started else 2)
        assert all(
            call[2]["params"] == {"device_id": "wohnung-reconnected"} for call in plays
        )

    mode["player"] = None
    # 4. no speaker chosen and none active: a clear message, nothing sent
    player._pending_device = None
    spotify.requests.clear()
    with pytest.raises(ServiceValidationError, match="Choose a speaker"):
        await hass.services.async_call(
            "media_player", "media_play", target, blocking=True
        )
    assert not [c for c in spotify.requests if c[0] == "PUT"]

    # pausing with nothing playing does not complain either (Spotify answers 404)
    def no_device(method, url, **kwargs):
        if method == "PUT" and url.endswith("/player/pause"):
            return Response(None, 404)
        return request(method, url, **kwargs)

    spotify.request = no_device
    await hass.services.async_call("media_player", "media_pause", target, blocking=True)


async def test_play_wakes_an_amazon_speaker_through_alexa(hass, spotify, monkeypatch):
    """Owner 2026-11-13: Wohnung Musik (an Alexa group) refused every Web API start; Alexa wakes it first."""
    import asyncio

    from homeassistant.helpers import entity_registry as er

    form = await hass.config_entries.flow.async_init(
        "aureon_music", context={"source": config_entries.SOURCE_USER}
    )
    external = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"client_id": "a" * 32}
    )
    await _complete(hass, external)
    await hass.async_block_till_done()
    (state,) = hass.states.async_all("media_player")
    target = {"entity_id": state.entity_id}
    speaker = {"id": "wohnung", "name": "Wohnung Musik", "is_active": False}
    awake = {"value": False}
    original = spotify.request

    def request(method, url, **kwargs):
        if url.endswith("/devices"):
            spotify.requests.append((method, url, kwargs))
            return Response({"devices": [dict(speaker, is_active=awake["value"])]})
        if method == "GET" and url.endswith("/player"):
            spotify.requests.append((method, url, kwargs))
            return Response(None, 204)
        if url.endswith("/player/recently-played"):
            spotify.requests.append((method, url, kwargs))
            return Response(
                {
                    "items": [
                        {
                            "track": {
                                "uri": "spotify:track:last",
                                "name": "Where Do We Go",
                                "artists": [{"name": "LUM!X"}],
                            },
                            "context": {"uri": "spotify:playlist:mix"},
                        }
                    ]
                }
            )
        if method == "PUT" and "/player" in url:
            spotify.requests.append((method, url, kwargs))
            return Response({}, 204) if awake["value"] else Response(None, 404)
        return original(method, url, **kwargs)

    spotify.request = request
    sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda delay: sleep(0))
    player = next(
        p
        for p in async_get_platforms(hass, "aureon_music")
        if p.domain == "media_player"
    ).entities[state.entity_id]
    player._pending_device = speaker

    # without Alexa Media Player: a message that says why and what helps
    with pytest.raises(ServiceValidationError, match="Alexa Media Player"):
        await hass.services.async_call(
            "media_player", "media_play", target, blocking=True
        )

    # with it: Alexa starts the song on the group, then the exact song inside its playlist follows
    er.async_get(hass).async_get_or_create(
        "media_player", "alexa_media", "group-1", original_name="Wohnung Musik"
    )
    asked = []

    async def alexa(call):
        asked.append(dict(call.data))
        awake["value"] = True

    hass.services.async_register("media_player", "play_media", alexa)
    spotify.requests.clear()
    await hass.services.async_call("media_player", "media_play", target, blocking=True)
    assert asked and asked[0]["media_content_type"] == "SPOTIFY"
    assert asked[0]["media_content_id"] == "Where Do We Go LUM!X"
    final = [c for c in spotify.requests if c[0] == "PUT"][-1]
    assert final[2]["params"] == {"device_id": "wohnung"}
    assert final[2]["json"] == {
        "context_uri": "spotify:playlist:mix",
        "offset": {"uri": "spotify:track:last"},
    }
