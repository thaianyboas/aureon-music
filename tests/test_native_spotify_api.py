"""Isolated transport tests; no Spotify account or live Home Assistant required."""
import pytest

from custom_components.aureon_music.native_spotify_api import NativeSpotifyApi, SpotifyApiError


class Response:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, **_):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(*self.responses.pop(0))


class Tokens:
    def __init__(self):
        self.forces = []

    async def access_token(self, *, force_refresh=False, rejected_access_token=None):
        self.forces.append(force_refresh)
        return "refreshed" if force_refresh else "initial"


@pytest.mark.asyncio
async def test_unauthorized_request_refreshes_once():
    session = Session((401, None), (200, {"id": "account"}))
    tokens = Tokens()
    result = await NativeSpotifyApi(session, tokens).profile()
    assert result == {"id": "account"}
    assert tokens.forces == [False, True]
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer refreshed"


@pytest.mark.asyncio
async def test_forbidden_does_not_retry_or_expose_body():
    session = Session((403, {"private": "do not log"}))
    tokens = Tokens()
    with pytest.raises(SpotifyApiError) as err:
        await NativeSpotifyApi(session, tokens).profile()
    assert err.value.status == 403
    assert tokens.forces == [False]
    assert "private" not in str(err.value)


@pytest.mark.asyncio
async def test_track_and_episode_use_uris_payload():
    session = Session((204, None), (204, None))
    api = NativeSpotifyApi(session, Tokens())
    await api.play(uri="spotify:track:abc")
    await api.play(uri="spotify:episode:xyz")
    assert session.calls[0][2]["json"] == {"uris": ["spotify:track:abc"]}
    assert session.calls[1][2]["json"] == {"uris": ["spotify:episode:xyz"]}


@pytest.mark.asyncio
async def test_invalid_path_and_uri_are_rejected():
    api = NativeSpotifyApi(Session(), Tokens())
    with pytest.raises(ValueError):
        await api.request("GET", "//evil.example")
    with pytest.raises(ValueError):
        await api.play(uri="https://evil.example")
